"""
Beat tracking in a child process, so the models never hold the capture process's GIL.

Measured reason: run as a thread, madmom's Python-level LSTM steps held the GIL often enough that, with every CPU
core busy, the capture thread stalled for up to about 1 s (about 50 ms at most with beat detection off).

Data flow: the capture thread writes samples into a shared-memory ring (a copy, no pickling, no locks); the child
reads them, runs `BeatTracker`, and sends the beat times back over a pipe; a listener thread in this process feeds
them to a `TempoEstimator`, which answers tempo queries. Failures of the child (exceptions, or the process dying)
are kept and reported by `error`.

No cross-process lock, semaphore or Event is shared: the child can be killed at any instant (OOM killer, crash)
without leaving a primitive held that the parent would then wait on forever. Shared values are lock-free
(one writer each) and the stop request is a flag the child polls.

The child is spawned, so it re-imports the program's main module: programs must start capture under
`if __name__ == "__main__":` (every cobeart entry point does).
"""
import logging
import multiprocessing
import os
import threading
import time
import traceback
from multiprocessing.connection import Connection
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.sharedctypes import Synchronized
from typing import Final, Optional

import numpy as np

from cobeart.audiocapture.beat.tempo import TempoEstimator, TempoState
from cobeart.audiocapture.beat.tracker import MODEL_COUNT, BeatBacklogError, BeatTracker, frame_geometry

logger: logging.Logger = logging.getLogger(__name__)

# spawn, not fork: the capture process has running threads (emitter, capture) that fork would copy half-way.
_CONTEXT: Final = multiprocessing.get_context("spawn")
_READY_TIMEOUT_S: Final[float] = 60.0
_LISTEN_POLL_S: Final[float] = 0.02
_WORKER_IDLE_S: Final[float] = 0.01
_STATS_PERIOD_S: Final[float] = 5.0


class BeatProcessError(RuntimeError):
    """The beat process failed to start, raised an exception, or exited unexpectedly."""


def _worker(shm_name: str, capacity: int, sample_rate: int, min_bpm: float, max_bpm: float,
            written: Synchronized, consumed: Synchronized, analysed: Synchronized, frames: Synchronized,
            busy: Synchronized, stop: Synchronized, conn: Connection) -> None:
    """Child process: move samples from the ring into a BeatTracker and send back the beats it finds.

    Exits when asked through `stop`, or by itself when the parent dies (killed, or exited without stop_stream),
    which reparents this process, so it never outlives the capture program.
    """
    parent = os.getppid()
    # Attaching registers the segment with the resource tracker this spawned child shares with the parent; that
    # registration is the parent's own (a set entry), and the parent's unlink removes it.
    shm = SharedMemory(name=shm_name)
    try:
        ring = np.ndarray((capacity,), dtype=np.float32, buffer=shm.buf)
        # The ring holds the backlog; the tracker only needs room for a frame plus one batch.
        tracker = BeatTracker(sample_rate, min_bpm=min_bpm, max_bpm=max_bpm, max_backlog_seconds=1.0)
        conn.send(("ready",))
        position = 0
        while not stop.value and os.getppid() == parent:
            take = min(written.value - position, tracker.free_samples)
            if take > 0:
                start = position % capacity
                first = min(take, capacity - start)
                tracker.add_samples(ring[start:start + first])
                if take > first:
                    tracker.add_samples(ring[:take - first])
                position += take
                consumed.value = position
            started = time.perf_counter()
            beats = tracker.process_pending()
            busy.value += time.perf_counter() - started
            frames.value = tracker.frames_processed
            analysed.value = tracker.stream_seconds
            if beats:
                conn.send(("beats", beats, tracker.stream_seconds))
            elif take <= 0:
                time.sleep(_WORKER_IDLE_S)
        del ring
    except Exception as exc:
        conn.send(("error", type(exc).__name__, str(exc), traceback.format_exc()))
    finally:
        shm.close()


class BeatProcess:
    """Runs beat tracking in a child process and keeps the resulting tempo state here."""

    def __init__(self, sample_rate: int, min_bpm: float = 55.0, max_bpm: float = 215.0,
                 max_backlog_seconds: float = 5.0, tempo: Optional[TempoEstimator] = None,
                 debug: bool = False) -> None:
        """
        Args:
            sample_rate: Capture sample rate in Hz (a multiple of 100).
            min_bpm, max_bpm: Tempo range of the beat tracker.
            max_backlog_seconds: Audio the ring holds for the child; overflowing it is a BeatBacklogError.
            tempo: Tempo estimator fed with the beats (defaults to TempoEstimator()).
            debug: Log the child's load (frames, ms per frame, busy share, backlog, tempo) every few seconds.
        """
        self.sample_rate: int = sample_rate
        self.frame_size, self.hop_size = frame_geometry(sample_rate)
        self.min_bpm: float = min_bpm
        self.max_bpm: float = max_bpm
        self.max_backlog_seconds: float = max_backlog_seconds
        self.tempo: TempoEstimator = tempo if tempo is not None else TempoEstimator()
        self.debug: bool = debug
        self._capacity: int = int(max_backlog_seconds * sample_rate)
        self._shm: Optional[SharedMemory] = None
        self._ring: Optional[np.ndarray] = None
        self._written_local: int = 0
        self._written: Synchronized = _CONTEXT.Value("q", 0, lock=False)
        self._consumed: Synchronized = _CONTEXT.Value("q", 0, lock=False)
        self._analysed: Synchronized = _CONTEXT.Value("d", 0.0, lock=False)
        self._frames: Synchronized = _CONTEXT.Value("q", 0, lock=False)
        self._busy: Synchronized = _CONTEXT.Value("d", 0.0, lock=False)
        self._stop: Synchronized = _CONTEXT.Value("b", 0, lock=False)
        self._process: Optional[multiprocessing.process.BaseProcess] = None
        self._conn: Optional[Connection] = None
        self._listener: Optional[threading.Thread] = None
        self._listening: bool = False
        self._error: Optional[BaseException] = None
        self._dropped_samples: int = 0
        self._lag_warned: bool = False

    def describe(self) -> str:
        """One-line summary of the beat configuration, for startup logs."""
        return (
            f"madmom online LSTM ensemble ({MODEL_COUNT} nets) + DBN forward tracker in a child process, "
            f"{self.min_bpm:g}-{self.max_bpm:g} BPM, frame {self.frame_size} / hop {self.hop_size} samples "
            f"at {self.sample_rate} Hz, tempo stable after {self.tempo.stable_beats} beats within "
            f"{self.tempo.tempo_tolerance:.0%}, lag warning {self.tempo.analysis_lag_seconds:g} s, "
            f"max backlog {self.max_backlog_seconds:g} s"
        )

    @property
    def pid(self) -> Optional[int]:
        return None if self._process is None else self._process.pid

    def start(self) -> None:
        """Spawn the child and wait until its models are loaded; on any failure everything is released again."""
        if self._process is not None or self._shm is not None:
            raise RuntimeError("BeatProcess.start called twice")
        try:
            self._start()
        except BaseException:
            self.stop()
            raise

    def _start(self) -> None:
        self._shm = SharedMemory(create=True, size=self._capacity * np.dtype(np.float32).itemsize)
        self._ring = np.ndarray((self._capacity,), dtype=np.float32, buffer=self._shm.buf)
        receive, send = _CONTEXT.Pipe(duplex=False)
        self._conn = receive
        self._process = _CONTEXT.Process(
            target=_worker, name="beat-process", daemon=True,
            args=(self._shm.name, self._capacity, self.sample_rate, self.min_bpm, self.max_bpm, self._written,
                  self._consumed, self._analysed, self._frames, self._busy, self._stop, send))
        try:
            self._process.start()
        finally:
            send.close()  # the child holds its own copy; ours must go so a dead child reads as EOF
        if not receive.poll(_READY_TIMEOUT_S):
            raise BeatProcessError(f"Beat process did not load its models within {_READY_TIMEOUT_S:g} s")
        try:
            message = receive.recv()
        except EOFError:
            raise BeatProcessError(
                f"Beat process exited during start-up (exit code {self._exitcode()}); if the program builds an "
                "AudioCapturer at import time, move that under `if __name__ == \"__main__\":` (the beat process "
                "is spawned and re-imports the main module)") from None
        if message[0] != "ready":
            raise BeatProcessError(f"Beat process failed to start: {message[1]}: {message[2]}\n{message[3]}")
        self._listening = True
        self._listener = threading.Thread(target=self._listen, name="beat-listener", daemon=True)
        self._listener.start()

    def _exitcode(self) -> Optional[int]:
        if self._process is None:
            return None
        self._process.join(timeout=1.0)
        return self._process.exitcode

    def _listen(self) -> None:
        """Listener thread: feed beats to the tempo estimator, watch the child's health and lag."""
        assert self._conn is not None
        stats_at = time.monotonic()
        stats_frames, stats_busy = 0, 0.0
        while self._listening:
            try:
                if self._conn.poll(_LISTEN_POLL_S):
                    message = self._conn.recv()
                    if message[0] == "beats":
                        self.tempo.update(message[1], message[2])
                    else:
                        _, name, text, trace = message
                        logger.error("Beat process failed:\n%s", trace)
                        self._error = (BeatBacklogError(text) if name == "BeatBacklogError"
                                       else BeatProcessError(f"{name}: {text}"))
                        return
                else:
                    self.tempo.update([], self._analysed.value)
            except EOFError:
                if self._listening:
                    self._error = BeatProcessError(
                        f"Beat process exited unexpectedly (exit code {self._exitcode()})")
                    logger.error("%s", self._error)
                return
            lag = self.backlog_seconds
            if lag > self.tempo.analysis_lag_seconds and not self._lag_warned:
                self._lag_warned = True
                logger.warning("Beat analysis is %.2f s behind the audio (warning threshold %.2f s); the CPU is "
                               "struggling, beats may drop out (it fails at %g s behind)",
                               lag, self.tempo.analysis_lag_seconds, self.max_backlog_seconds)
            now = time.monotonic()
            if self.debug and now - stats_at >= _STATS_PERIOD_S:
                frames, busy = self._frames.value - stats_frames, self._busy.value - stats_busy
                logger.info("Beat process: %d frames in %.1f s, %.2f ms/frame, busy %.1f%%, backlog %.3f s, "
                            "tempo %s", frames, now - stats_at, 1000 * busy / max(frames, 1),
                            100 * busy / (now - stats_at), lag, self.tempo.tempo_bpm)
                stats_at, stats_frames, stats_busy = now, self._frames.value, self._busy.value

    def add_samples(self, block: np.ndarray) -> None:
        """Copy mono samples into the ring (capture thread; never blocks, never raises)."""
        ring = self._ring
        if ring is None:
            raise RuntimeError("BeatProcess.add_samples called before start()")
        n = len(block)
        written = self._written_local
        if written + n - self._consumed.value > self._capacity:
            # Raising here would kill the capture thread; `error` reports it instead.
            self._dropped_samples += n
            return
        start = written % self._capacity
        first = min(n, self._capacity - start)
        ring[start:start + first] = block[:first]
        ring[:n - first] = block[first:]
        self._written_local = written + n
        self._written.value = self._written_local

    @property
    def backlog_seconds(self) -> float:
        """Audio received but not yet analysed, in seconds."""
        return max(0.0, self._written_local / self.sample_rate - self._analysed.value)

    @property
    def error(self) -> Optional[BaseException]:
        """Why beat tracking stopped, or None while it is healthy."""
        if self._error is None and self._dropped_samples:
            self._error = BeatBacklogError(
                f"Beat processing fell more than {self.max_backlog_seconds:g} s behind the audio and "
                f"{self._dropped_samples} samples were dropped; the CPU is too busy for real-time beat detection "
                "(close other load or disable beat detection)")
            logger.error("%s", self._error)
        return self._error

    def get_prediction_state(self, live_seconds: float) -> Optional[TempoState]:
        """Stable tempo for the predictor (see TempoEstimator.get_prediction_state)."""
        return self.tempo.get_prediction_state(live_seconds)

    def stop(self) -> None:
        """Stop the child and the listener, and release the shared memory."""
        self._listening = False
        self._stop.value = 1
        if self._process is not None and self._process.pid is not None:
            self._process.join(timeout=5.0)
            if self._process.is_alive():
                logger.warning("Beat process did not stop within 5 s; terminating it")
                self._process.terminate()
                self._process.join(timeout=1.0)
        if self._listener is not None:
            self._listener.join(timeout=1.0)
            self._listener = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._ring = None
        if self._shm is not None:
            self._shm.close()
            self._shm.unlink()
            self._shm = None

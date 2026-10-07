"""
Streaming beat tracking with madmom's online beat models.

Audio is analysed once, frame by frame, as it arrives: madmom's online LSTM ensemble (BEATS_LSTM) turns each
10 ms frame into a beat activation, and the DBN beat tracker's forward algorithm decides causally whether that
frame is a beat. Both keep their state between calls, so each sample is processed exactly once.

All times here are stream seconds: seconds of audio received since the detector was created. A frame is stamped
with the time of its newest sample (its right edge), which is the earliest moment its content is known.
`StreamClock` maps stream seconds to wall-clock time.

Threading: `add_samples` is called from the capture thread and only copies samples into a pending buffer.
`process_pending` runs the models on a separate thread; when that thread falls more than `max_backlog_seconds`
behind, it raises `BeatBacklogError` instead of silently dropping audio.
"""
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Final, Optional

import numpy as np
from madmom.audio.signal import FramedSignal, Signal
from madmom.audio.spectrogram import (
    FilteredSpectrogramProcessor,
    LogarithmicSpectrogramProcessor,
    SpectrogramDifferenceProcessor,
)
from madmom.audio.stft import ShortTimeFourierTransformProcessor
from madmom.features.beats import DBNBeatTrackingProcessor
from madmom.ml.nn import NeuralNetworkEnsemble
from madmom.models import BEATS_LSTM
from madmom.processors import SequentialProcessor

logger: logging.Logger = logging.getLogger(__name__)

FPS: Final[int] = 100
"""Frame rate of the beat models (frames per second); the hop is sample_rate / FPS samples."""
MODEL_SAMPLE_RATE: Final[int] = 44100
MODEL_FRAME_SIZE: Final[int] = 2048
"""madmom's online beat models use 2048-sample frames at 44.1 kHz (46.4 ms)."""


class BeatBacklogError(RuntimeError):
    """Beat processing fell too far behind the incoming audio to stay real time."""


@dataclass(frozen=True)
class TempoState:
    """A stable tempo: the latest beat (stream seconds), the beat interval (seconds) and the tempo (BPM)."""

    last_beat: float
    interval: float
    bpm: float


class BeatDetector:
    """Online beat tracker over a live sample stream (see module docstring)."""

    def __init__(
        self,
        sample_rate: int,
        min_bpm: float = 55.0,
        max_bpm: float = 215.0,
        tempo_tolerance: float = 0.10,
        stable_beats: int = 4,
        history_size: int = 8,
        max_missed_beats: float = 2.0,
        max_backlog_seconds: float = 5.0,
    ) -> None:
        """
        Args:
            sample_rate: Capture sample rate in Hz; must be a multiple of FPS so frames are exactly 10 ms apart.
            min_bpm, max_bpm: Tempo range of the DBN tracker.
            tempo_tolerance: A beat interval more than this fraction away from the running mean restarts the
                tempo estimate.
            stable_beats: Consecutive consistent beats needed before the tempo counts as stable.
            history_size: Beats kept for the tempo estimate (the BPM is the mean of their intervals).
            max_missed_beats: The tempo stops being stable when no beat was detected for this many intervals.
            max_backlog_seconds: Unprocessed audio allowed before `process_pending` raises BeatBacklogError.
        """
        if sample_rate % FPS != 0:
            raise ValueError(
                f"Beat detection needs a sample rate that is a multiple of {FPS} Hz (got {sample_rate} Hz); "
                "use 44100 or 48000"
            )
        if stable_beats < 3 or history_size < stable_beats:
            raise ValueError(
                f"Need 3 <= stable_beats <= history_size (got stable_beats={stable_beats}, "
                f"history_size={history_size})"
            )
        self.sample_rate: int = sample_rate
        self.hop_size: int = sample_rate // FPS
        # Same window duration (and therefore the same FFT bin frequencies) as the model's 2048 @ 44.1 kHz.
        self.frame_size: int = round(MODEL_FRAME_SIZE * sample_rate / MODEL_SAMPLE_RATE)
        # Compensates the larger window's higher FFT magnitudes, so features match the model's input scale.
        self._frame_gain: float = MODEL_FRAME_SIZE / self.frame_size
        self.min_bpm: float = min_bpm
        self.max_bpm: float = max_bpm
        self.tempo_tolerance: float = tempo_tolerance
        self.stable_beats: int = stable_beats
        self.max_missed_beats: float = max_missed_beats
        self.max_backlog_seconds: float = max_backlog_seconds

        # Pending audio (capture thread appends, processing thread consumes).
        self._pending: np.ndarray = np.zeros(self.frame_size + int(max_backlog_seconds * sample_rate),
                                             dtype=np.float32)
        self._pending_count: int = 0
        self._dropped_samples: int = 0
        self._pending_lock: threading.Lock = threading.Lock()

        # madmom online pipeline, mirroring RNNBeatProcessor(online=True) minus its framing, which is done here.
        self._spectrogram: SequentialProcessor = SequentialProcessor([
            ShortTimeFourierTransformProcessor(),
            FilteredSpectrogramProcessor(num_bands=12, fmin=30, fmax=17000, norm_filters=True),
            LogarithmicSpectrogramProcessor(mul=1, add=1),
        ])
        self._difference: SpectrogramDifferenceProcessor = SpectrogramDifferenceProcessor(
            diff_ratio=0.5, positive_diffs=True, stack_diffs=np.hstack)
        self._network: NeuralNetworkEnsemble = NeuralNetworkEnsemble.load(BEATS_LSTM)
        self._tracker: DBNBeatTrackingProcessor = DBNBeatTrackingProcessor(
            fps=FPS, online=True, min_bpm=min_bpm, max_bpm=max_bpm)
        self._tracker.reset()
        self._frames_processed: int = 0

        # Tempo state (processing thread writes, any thread reads).
        self._beat_history: deque[float] = deque(maxlen=history_size)
        self._state_lock: threading.Lock = threading.Lock()

    def describe(self) -> str:
        """One-line summary of the beat configuration, for startup logs."""
        return (
            f"madmom online LSTM ensemble ({len(BEATS_LSTM)} nets) + DBN forward tracker, "
            f"{self.min_bpm:g}-{self.max_bpm:g} BPM, frame {self.frame_size} / hop {self.hop_size} samples "
            f"at {self.sample_rate} Hz, tempo stable after {self.stable_beats} beats within "
            f"{self.tempo_tolerance:.0%}, max backlog {self.max_backlog_seconds:g} s"
        )

    def add_samples(self, block: np.ndarray) -> None:
        """Queue mono samples for processing (capture thread; copies only, never runs the models)."""
        n = len(block)
        with self._pending_lock:
            if self._pending_count + n > len(self._pending):
                # Raising here would kill the capture thread; the processing thread raises on its next call.
                self._dropped_samples += n
                return
            self._pending[self._pending_count:self._pending_count + n] = block
            self._pending_count += n

    @property
    def backlog_seconds(self) -> float:
        """Audio received but not yet analysed, in seconds."""
        with self._pending_lock:
            return self._pending_count / self.sample_rate

    @property
    def stream_seconds(self) -> float:
        """Stream time up to which audio has been analysed (right edge of the last processed frame)."""
        if self._frames_processed == 0:
            return 0.0
        return self._frame_time(self._frames_processed - 1)

    def _frame_time(self, frame_index: int) -> float:
        return (frame_index * self.hop_size + self.frame_size) / self.sample_rate

    def process_pending(self) -> int:
        """Run the models on every complete pending frame; returns the number of frames processed."""
        with self._pending_lock:
            if self._dropped_samples:
                raise BeatBacklogError(
                    f"Beat processing fell more than {self.max_backlog_seconds:g} s behind the audio and "
                    f"{self._dropped_samples} samples were dropped; the CPU is too busy for real-time beat "
                    "detection (close other load or disable beat detection)"
                )
            if self._pending_count < self.frame_size:
                return 0
            num_frames = (self._pending_count - self.frame_size) // self.hop_size + 1
            span = self.frame_size + (num_frames - 1) * self.hop_size
            segment = self._pending[:span] * self._frame_gain

        frames = FramedSignal(Signal(segment, sample_rate=self.sample_rate), frame_size=self.frame_size,
                              hop_size=self.hop_size, origin='stream', num_frames=num_frames)
        first = self._frames_processed == 0
        spectrogram = self._spectrogram(frames)
        # The difference processor sizes its online buffer from its first call and then always returns that many
        # rows, so it must see the same number of frames on every call: one.
        features = np.vstack([self._difference(spectrogram[i:i + 1], reset=first and i == 0)
                              for i in range(num_frames)])
        activations = self._network(features, reset=first)
        beats = self._tracker.process_online(activations, reset=False)

        consumed = num_frames * self.hop_size
        with self._pending_lock:
            remaining = self._pending_count - consumed
            self._pending[:remaining] = self._pending[consumed:self._pending_count]
            self._pending_count = remaining
        self._frames_processed += num_frames

        for beat_seconds in beats:
            self._add_beat(self._frame_time(int(round(beat_seconds * FPS))))
        return num_frames

    def _add_beat(self, beat_time: float) -> None:
        with self._state_lock:
            if len(self._beat_history) >= 2:
                mean_interval = (self._beat_history[-1] - self._beat_history[0]) / (len(self._beat_history) - 1)
                interval = beat_time - self._beat_history[-1]
                if abs(interval - mean_interval) > self.tempo_tolerance * mean_interval:
                    logger.debug("Beat interval %.3f s breaks tempo (mean %.3f s); restarting estimate",
                                 interval, mean_interval)
                    last = self._beat_history[-1]
                    self._beat_history.clear()
                    self._beat_history.append(last)
            self._beat_history.append(beat_time)
        logger.debug("Beat at stream %.3f s (history %d)", beat_time, len(self._beat_history))

    def get_prediction_state(self) -> Optional[TempoState]:
        """The current stable tempo, or None while there is none (too few consistent beats, or beats stopped)."""
        now = self.stream_seconds
        with self._state_lock:
            if len(self._beat_history) < self.stable_beats:
                return None
            last = self._beat_history[-1]
            interval = (last - self._beat_history[0]) / (len(self._beat_history) - 1)
        if now - last > self.max_missed_beats * interval:
            return None
        return TempoState(last_beat=last, interval=interval, bpm=60.0 / interval)

    @property
    def tempo_bpm(self) -> Optional[float]:
        """Stable tempo in BPM, or None."""
        state = self.get_prediction_state()
        return None if state is None else state.bpm

"""Beat detection on synthetic click tracks: the detector offline, and the full AudioCapturer in real time."""
import json
import logging
import os
import signal
import time
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from cobeart.audiocapture.beat import BeatBacklogError, BeatProcess, BeatProcessError, BeatTracker, TempoEstimator
from cobeart.audiocapture.beat.tempo import DEFAULT_ANALYSIS_LAG_SECONDS
from cobeart.audiocapture.capture import AudioCapturer

SAMPLE_RATE: int = 48000
FIRST_CLICK_S: float = 0.25
BLOCK: int = 480  # 10 ms, the capture block size AudioCapturer requests at 48 kHz

# Tolerances. The detector stamps each 10 ms model frame with its newest sample, and the DBN may pick the frame
# before or after the one containing the click, so offline beats land within two frames (20 ms) of the click; the
# extra 5 ms is margin, not tuning (measured offsets were -4 to +7 ms). In real time the wall-clock mapping adds up
# to one 10 ms capture block of arrival jitter, plus scheduling noise on a loaded test machine; 40 ms is also the
# order of the audio-visual sync detectability threshold (ITU-R BT.1359: about 45 ms for audio late), so beats
# inside it read as on time.
BPM_TOLERANCE: float = 2.0
OFFLINE_BEAT_TOLERANCE_S: float = 0.025
REALTIME_BEAT_TOLERANCE_S: float = 0.040
# Stable tempo needs four consistent beats (three intervals) after the DBN's first beat, which comes about one
# beat after the first click: about five beat periods, plus one for slack.
LOCK_IN_BEATS: float = 6.0
# Real time only: beat analysis may trail the live audio by up to the tolerated analysis lag (0.5 s) without
# that being a fault, so tempo checks start that much later in stream time.
ANALYSIS_LAG_S: float = DEFAULT_ANALYSIS_LAG_SECONDS
# A real capture device buffers samples (PulseAudio/PipeWire record streams hold far more than 0.5 s by default),
# so a late read loses nothing as long as the delay stays inside that buffer; the late samples are then read
# back-to-back. Audio is lost only if the delay outgrows the buffer. The bound is 0.5 s, the analysis lag at
# which beat detection warns that the CPU is struggling. A capture loop just 6% slower than real time exceeds it
# within this 9 s run. With the models in their own process the capture thread stays within about 50 ms even
# with every CPU core busy (it stalled for up to 1 s when they shared its GIL).
MAX_READ_DELAY_S: float = DEFAULT_ANALYSIS_LAG_SECONDS


def click_track(bpm: float, seconds: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Mono click track (1 kHz tone plus noise burst, 8 ms decay) over a -46 dBFS noise floor, and click times."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    audio = (rng.standard_normal(n) * 0.005).astype(np.float32)
    t = np.arange(int(0.03 * SAMPLE_RATE)) / SAMPLE_RATE
    click = (0.5 * np.sin(2 * np.pi * 1000 * t) + 0.3 * rng.standard_normal(len(t))) * np.exp(-t / 0.008)
    onsets = np.arange(FIRST_CLICK_S, seconds, 60.0 / bpm)
    for onset in onsets:
        start = int(onset * SAMPLE_RATE)
        segment = audio[start:start + len(click)]
        segment += click[:len(segment)].astype(np.float32)
    return audio, onsets


def run_detector(audio: np.ndarray) -> tuple[TempoEstimator, list[float], list[tuple[float, float | None]]]:
    """Track audio fed in capture-sized blocks, in process; return the tempo estimator, the beats found and
    (analysed stream time, tempo) per block.

    Processing runs after irregular numbers of blocks, as the real beat process does, so batches vary in size.
    """
    tracker = BeatTracker(sample_rate=SAMPLE_RATE)
    tempo = TempoEstimator()
    beats: list[float] = []
    tempi: list[tuple[float, float | None]] = []
    for index, start in enumerate(range(0, len(audio), BLOCK)):
        tracker.add_samples(audio[start:start + BLOCK])
        if index % 3 == 0 or index % 7 == 0:
            found = tracker.process_pending()
            beats.extend(found)
            tempo.update(found, tracker.stream_seconds)
        state = tempo.get_prediction_state((start + BLOCK) / SAMPLE_RATE)
        tempi.append((tracker.stream_seconds, None if state is None else state.bpm))
    return tempo, beats, tempi


@pytest.mark.parametrize("bpm", [90.0, 120.0, 147.0])
def test_detector_locks_onto_click_track(bpm: float) -> None:
    audio, onsets = click_track(bpm, 12.0, seed=int(bpm))
    _, beats, tempi = run_detector(audio)
    lock_in = FIRST_CLICK_S + LOCK_IN_BEATS * 60.0 / bpm
    after = [tempo for t, tempo in tempi if t >= lock_in]
    assert after and all(tempo is not None and abs(tempo - bpm) <= BPM_TOLERANCE for tempo in after), \
        f"tempo after {lock_in:.2f} s not within {BPM_TOLERANCE} of {bpm}: {sorted(set(after), key=str)[:5]}"
    errors = np.array([min(abs(onsets - beat)) for beat in beats])
    assert errors.max() <= OFFLINE_BEAT_TOLERANCE_S, f"beat errors (s): {np.round(errors, 4)}"
    beats_arr = np.array(beats)
    missed = [round(o, 3) for o in onsets[onsets >= lock_in] if np.abs(beats_arr - o).min() > OFFLINE_BEAT_TOLERANCE_S]
    assert not missed, f"clicks at {missed} s after lock-in produced no beat"


def test_tempo_goes_stale_when_analysis_stops() -> None:
    audio, _ = click_track(120.0, 6.0, seed=3)
    tempo, beats, tempi = run_detector(audio)
    analysed = tempi[-1][0]
    assert tempo.get_prediction_state(analysed) is not None
    # Live audio moves on while nothing more is analysed: stale after 2 intervals (1 s) plus the tolerated lag.
    assert tempo.get_prediction_state(beats[-1] + 1.0 + ANALYSIS_LAG_S + 0.05) is None


def test_tempo_restarts_from_the_beat_that_breaks_it() -> None:
    tempo = TempoEstimator()
    tempo.update([0.0, 0.5, 1.0, 1.5], 1.5)
    assert tempo.get_prediction_state(1.5) is not None
    # 1.5 -> 1.8 breaks the 0.5 s grid; the estimate restarts at 1.8, so three more consistent beats re-lock it.
    tempo.update([1.8, 2.2, 2.6], 2.6)
    assert tempo.get_prediction_state(2.6) is None
    tempo.update([3.0], 3.0)
    state = tempo.get_prediction_state(3.0)
    assert state is not None and abs(state.interval - 0.4) < 1e-9


def test_detector_reports_no_tempo_on_noise() -> None:
    rng = np.random.default_rng(1)
    noise = (rng.standard_normal(8 * SAMPLE_RATE) * 0.01).astype(np.float32)
    _, _, tempi = run_detector(noise)
    assert all(tempo is None for _, tempo in tempi)


def test_tracker_backlog_raises() -> None:
    tracker = BeatTracker(sample_rate=SAMPLE_RATE, max_backlog_seconds=0.1)
    with pytest.raises(BeatBacklogError, match="behind"):
        tracker.add_samples(np.zeros(SAMPLE_RATE, dtype=np.float32))


def test_tracker_rejects_sample_rate_without_whole_hop() -> None:
    with pytest.raises(ValueError, match="multiple of 100"):
        BeatTracker(sample_rate=22050)


def test_beat_process_warns_on_lag_once_and_fails_on_overflow(caplog: pytest.LogCaptureFixture) -> None:
    beat = BeatProcess(sample_rate=SAMPLE_RATE, max_backlog_seconds=5.0)
    with caplog.at_level(logging.WARNING, logger="cobeart.audiocapture.beat.process"):
        beat.start()
        try:
            # 3 s arriving at once is 3 s of lag, well over the 0.5 s warning threshold.
            beat.add_samples(np.zeros(3 * SAMPLE_RATE, dtype=np.float32))
            deadline = time.time() + 5.0
            while not any("behind the audio" in r.getMessage() for r in caplog.records):
                assert time.time() < deadline, "no lag warning"
                time.sleep(0.02)
            beat.add_samples(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
            time.sleep(0.1)
            assert sum("behind the audio" in r.getMessage() for r in caplog.records) == 1
            assert beat.error is None
            beat.add_samples(np.zeros(6 * SAMPLE_RATE, dtype=np.float32))  # more than the ring can ever hold
            assert isinstance(beat.error, BeatBacklogError)
        finally:
            beat.stop()


class _PacedRecorder(AbstractContextManager["_PacedRecorder"]):
    """Plays the audio back in real time, like a device: record() blocks until the samples would exist.

    A late read gets its samples at once, as from a device buffer, and its delay is recorded.
    """

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.position = 0
        self.start = 0.0
        self.read_delays: list[float] = []

    def __enter__(self) -> "_PacedRecorder":
        self.start = time.time()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        return None

    def record(self, numframes: int) -> np.ndarray:
        deadline = self.start + (self.position + numframes) / SAMPLE_RATE
        wait = deadline - time.time()
        if wait > 0:
            time.sleep(wait)
        self.read_delays.append(max(0.0, -wait))
        block = np.zeros((numframes, 1), dtype=np.float32)
        chunk = self.audio[self.position:self.position + numframes]
        block[:len(chunk), 0] = chunk
        self.position += numframes
        return block


class _ClickMic:
    def __init__(self, audio: np.ndarray) -> None:
        self.recorder_instance = _PacedRecorder(audio)

    def recorder(self, samplerate: float, channels: list[int], blocksize: int) -> _PacedRecorder:
        assert samplerate == SAMPLE_RATE
        assert blocksize == BLOCK
        return self.recorder_instance


class _ListSink:
    """Collects what the capture thread emits."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def push_metrics(self, metrics: dict[str, Any]) -> None:
        self.payloads.append(metrics)

    def stop(self) -> None:
        return None


def _run_capturer(audio: np.ndarray, seconds: float) -> tuple[_PacedRecorder, list[dict[str, Any]]]:
    """Run AudioCapturer with emission on the capture thread for `seconds`; return the device and the payloads."""
    mic = _ClickMic(audio)
    sink = _ListSink()
    capturer = AudioCapturer(chunk_size=1024, sample_rate=SAMPLE_RATE, mic=mic, emitter=sink)
    capturer.start_stream()
    try:
        end = time.time() + seconds
        while time.time() < end:
            assert capturer.capture_error is None
            time.sleep(0.05)
    finally:
        capturer.stop_stream()
    return mic.recorder_instance, sink.payloads


def test_capturer_emits_beats_on_time(audio_validator: Draft202012Validator) -> None:
    bpm = 120.0
    seconds = 9.0
    audio, onsets = click_track(bpm, seconds, seed=7)
    device, payloads = _run_capturer(audio, seconds)

    # One payload per block, built on the capture thread, so payload i covers the stream up to (i + 1) blocks.
    blocks = len(device.read_delays)
    assert len(payloads) >= blocks - 1, f"{blocks} blocks read but only {len(payloads)} payloads emitted"
    assert max(device.read_delays) < MAX_READ_DELAY_S, \
        f"capture loop fell {max(device.read_delays):.3f} s behind the device; audio would be lost"
    stream_end = (np.arange(len(payloads)) + 1) * BLOCK / SAMPLE_RATE

    lock_in = FIRST_CLICK_S + LOCK_IN_BEATS * 60.0 / bpm + ANALYSIS_LAG_S
    after = [p["tempo_bpm"] for p, t in zip(payloads, stream_end) if t >= lock_in]
    assert after and all(tempo is not None and abs(tempo - bpm) <= BPM_TOLERANCE for tempo in after), \
        f"tempo after stream {lock_in:.2f} s not within {BPM_TOLERANCE} of {bpm}: {sorted(set(after), key=str)[:5]}"

    click_times = device.start + onsets
    beat_payloads = [p for p in payloads if p["beat"]]
    for p in beat_payloads:
        audio_validator.validate(p)
    beat_times = np.array([p["beat_timestamp"] for p in beat_payloads])
    matched = [int(np.argmin(np.abs(click_times - b))) for b in beat_times]
    errors = np.abs(click_times[matched] - beat_times)
    assert len(errors) and errors.max() <= REALTIME_BEAT_TOLERANCE_S, f"beat errors (s): {np.round(errors, 4)}"
    assert len(set(matched)) == len(matched), "a click produced more than one beat"
    expected = np.nonzero((onsets >= lock_in) & (onsets <= stream_end[-1] - 0.05))[0]
    assert len(expected) > 0
    missed = sorted(set(expected.tolist()) - set(matched))
    assert not missed, f"clicks {missed} after lock-in produced no beat"


def test_dead_beat_process_stops_beats_and_tempo(caplog: pytest.LogCaptureFixture) -> None:
    audio, _ = click_track(120.0, 12.0, seed=11)
    mic = _ClickMic(audio)
    sink = _ListSink()
    capturer = AudioCapturer(chunk_size=1024, sample_rate=SAMPLE_RATE, mic=mic, emitter=sink)
    beat = capturer._beat_process
    assert beat is not None

    capturer.start_stream()
    try:
        deadline = time.time() + 8.0
        while not any(p["tempo_bpm"] is not None for p in sink.payloads[-5:]):
            assert time.time() < deadline, "tempo never became stable"
            time.sleep(0.05)
        assert beat.pid is not None
        os.kill(beat.pid, signal.SIGKILL)  # as the OOM killer would
        deadline = time.time() + 2.0
        while capturer.capture_error is None:
            assert time.time() < deadline, "beat thread failure never surfaced"
            time.sleep(0.01)
        # Skip the payload that may have been in flight while the error was being recorded.
        died_at = len(sink.payloads) + 1
        time.sleep(1.5)  # three beat intervals that a stale predictor would keep extrapolating
    finally:
        capturer.stop_stream()
    later = sink.payloads[died_at:]
    assert len(later) > 100
    assert isinstance(capturer.capture_error, BeatProcessError)
    assert "exited unexpectedly" in str(capturer.capture_error)
    assert all(not p["beat"] and p["tempo_bpm"] is None and p["beat_timestamp"] is None for p in later)


def test_silent_payload_is_finite_json() -> None:
    capturer = AudioCapturer(chunk_size=1024, sample_rate=SAMPLE_RATE, mic=_ClickMic(np.zeros(1, np.float32)))
    for _ in range(20):
        payload = capturer.compute_metrics_payload(np.zeros(1024, dtype=np.float32))
        json.dumps(payload, allow_nan=False)

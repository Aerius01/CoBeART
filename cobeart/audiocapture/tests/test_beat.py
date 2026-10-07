"""Beat detection on synthetic click tracks: the detector offline, and the full AudioCapturer in real time."""
import json
import logging
import threading
import time
from contextlib import AbstractContextManager
from types import TracebackType

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from cobeart.audiocapture.beat import BeatBacklogError, BeatDetector
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


def run_detector(audio: np.ndarray) -> tuple[BeatDetector, list[float], list[tuple[float, float | None]]]:
    """Feed audio in capture-sized blocks; return the detector, its beats and (stream time, tempo) per block.

    Processing runs after irregular numbers of blocks, as the real beat thread does, so batches vary in size.
    """
    detector = BeatDetector(sample_rate=SAMPLE_RATE)
    beats: list[float] = []
    tempi: list[tuple[float, float | None]] = []
    for index, start in enumerate(range(0, len(audio), BLOCK)):
        detector.add_samples(audio[start:start + BLOCK])
        if index % 3 == 0 or index % 7 == 0:
            detector.process_pending()
        state = detector.get_prediction_state()
        if state is not None and (not beats or state.last_beat != beats[-1]):
            beats.append(state.last_beat)
        tempi.append((detector.stream_seconds, None if state is None else state.bpm))
    return detector, beats, tempi


@pytest.mark.parametrize("bpm", [90.0, 120.0, 147.0])
def test_detector_locks_onto_click_track(bpm: float) -> None:
    audio, onsets = click_track(bpm, 12.0, seed=int(bpm))
    _, beats, tempi = run_detector(audio)
    lock_in = FIRST_CLICK_S + LOCK_IN_BEATS * 60.0 / bpm
    after = [tempo for t, tempo in tempi if t >= lock_in]
    assert after and all(tempo is not None and abs(tempo - bpm) <= BPM_TOLERANCE for tempo in after), \
        f"tempo after {lock_in:.2f} s not within {BPM_TOLERANCE} of {bpm}: {sorted(set(after), key=str)[:5]}"
    errors = [min(abs(onsets - beat)) for beat in beats]
    assert max(errors) <= OFFLINE_BEAT_TOLERANCE_S, f"beat errors (s): {np.round(errors, 4)}"
    assert len(beats) >= len(onsets[onsets >= lock_in]), "beats missed after lock-in"


def test_detector_reports_no_tempo_on_noise() -> None:
    rng = np.random.default_rng(1)
    noise = (rng.standard_normal(8 * SAMPLE_RATE) * 0.01).astype(np.float32)
    _, _, tempi = run_detector(noise)
    assert all(tempo is None for _, tempo in tempi)


def test_detector_backlog_raises() -> None:
    detector = BeatDetector(sample_rate=SAMPLE_RATE, max_backlog_seconds=0.1)
    detector.add_samples(np.zeros(SAMPLE_RATE, dtype=np.float32))
    with pytest.raises(BeatBacklogError, match="behind"):
        detector.process_pending()


def test_detector_rejects_sample_rate_without_whole_hop() -> None:
    with pytest.raises(ValueError, match="multiple of 100"):
        BeatDetector(sample_rate=22050)


class _PacedRecorder(AbstractContextManager["_PacedRecorder"]):
    """Plays the audio back in real time, like a device: record() blocks until the samples would exist."""

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.position = 0
        self.start = 0.0
        self.max_lateness = 0.0

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
        else:
            self.max_lateness = max(self.max_lateness, -wait)
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
        return self.recorder_instance


def test_capturer_emits_beats_on_time(audio_validator: Draft202012Validator) -> None:
    bpm = 120.0
    seconds = 9.0
    audio, onsets = click_track(bpm, seconds, seed=7)
    mic = _ClickMic(audio)
    capturer = AudioCapturer(chunk_size=1024, sample_rate=SAMPLE_RATE, mic=mic)
    payloads: list[tuple[float, dict]] = []
    capturer.start_stream()
    try:
        end = time.time() + seconds
        while time.time() < end:
            payload = capturer.compute_metrics_payload(capturer.read_chunk())
            assert payload is not None
            payloads.append((time.time(), payload))
            time.sleep(0.005)
        assert capturer.capture_error is None
    finally:
        capturer.stop_stream()

    start = mic.recorder_instance.start
    assert mic.recorder_instance.max_lateness < 0.05, "capture loop blocked: device reads fell behind"
    lock_in = start + FIRST_CLICK_S + LOCK_IN_BEATS * 60.0 / bpm
    after = [p["tempo_bpm"] for t, p in payloads if t >= lock_in]
    assert after and all(tempo is not None and abs(tempo - bpm) <= BPM_TOLERANCE for tempo in after)

    click_times = start + onsets
    beat_times = [p["beat_timestamp"] for _, p in payloads if p["beat"]]
    for _, p in payloads:
        if p["beat"]:
            audio_validator.validate(p)
    matched = [int(np.argmin(np.abs(click_times - b))) for b in beat_times]
    errors = [abs(click_times[i] - b) for i, b in zip(matched, beat_times)]
    assert errors and max(errors) <= REALTIME_BEAT_TOLERANCE_S, f"beat errors (s): {np.round(errors, 4)}"
    assert len(set(matched)) == len(matched), "a click produced more than one beat"
    expected = click_times[(click_times >= lock_in) & (click_times <= payloads[-1][0] - 0.05)]
    assert set(range(len(click_times))[-len(expected):]) <= set(matched), "beats missed after lock-in"


def test_silent_payload_is_finite_json() -> None:
    capturer = AudioCapturer(chunk_size=1024, sample_rate=SAMPLE_RATE, mic=_ClickMic(np.zeros(1, np.float32)))
    for _ in range(20):
        payload = capturer.compute_metrics_payload(np.zeros(1024, dtype=np.float32))
        json.dumps(payload, allow_nan=False)


def test_beat_thread_failure_surfaces_as_capture_error(monkeypatch: pytest.MonkeyPatch,
                                                       caplog: pytest.LogCaptureFixture) -> None:
    capturer = AudioCapturer(chunk_size=1024, sample_rate=SAMPLE_RATE,
                             mic=_ClickMic(np.zeros(SAMPLE_RATE, np.float32)))
    assert capturer._beat_detector is not None
    failed = threading.Event()

    def fail() -> int:
        failed.set()
        raise BeatBacklogError("simulated overload")

    monkeypatch.setattr(capturer._beat_detector, "process_pending", fail)
    with caplog.at_level(logging.ERROR, logger="cobeart.audiocapture.capture"):
        capturer.start_stream()
        try:
            assert failed.wait(2.0)
            assert capturer._beat_processing_thread is not None
            capturer._beat_processing_thread.join(timeout=2.0)
            assert isinstance(capturer.capture_error, BeatBacklogError)
        finally:
            capturer.stop_stream()
    assert any("Beat processing thread failed" in r.getMessage() for r in caplog.records)

"""AudioCapturer payload contract and capture-thread error surfacing, with no real audio device."""
import logging
from contextlib import AbstractContextManager
from types import TracebackType

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from cobeart.audiocapture.capture import AudioCapturer
from cobeart.audiocapture.emitter import AudioEmitter

CHUNK_SIZE: int = 1024


class _FailingRecorder(AbstractContextManager["_FailingRecorder"]):
    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        return None

    def record(self, numframes: int) -> np.ndarray:
        raise RuntimeError("device unplugged")


class _FakeMic:
    """Capture source whose recorder raises on the first read."""

    def recorder(self, samplerate: float, channels: list[int], blocksize: int) -> _FailingRecorder:
        return _FailingRecorder()


def test_payload_matches_contract(audio_validator: Draft202012Validator) -> None:
    capturer = AudioCapturer(chunk_size=CHUNK_SIZE, mic=_FakeMic())
    rng = np.random.default_rng(0)
    for _ in range(30):
        payload = capturer.compute_metrics_payload(rng.uniform(-0.5, 0.5, CHUNK_SIZE).astype(np.float32))
        assert payload is not None
        audio_validator.validate(payload)
    assert payload["schemaVersion"] == 1


def test_capture_thread_failure_is_observable(caplog: pytest.LogCaptureFixture) -> None:
    # Capture-thread behaviour only; beat failures are covered in test_beat.py.
    capturer = AudioCapturer(chunk_size=CHUNK_SIZE, mic=_FakeMic(), enable_beat_detection=False)
    assert capturer.capture_error is None
    with caplog.at_level(logging.ERROR, logger="cobeart.audiocapture.capture"):
        capturer.start_stream()
        try:
            assert capturer._capture_thread is not None
            capturer._capture_thread.join(timeout=5.0)
        finally:
            capturer.stop_stream()
    assert isinstance(capturer.capture_error, RuntimeError)
    assert "device unplugged" in str(capturer.capture_error)
    assert any("capture thread failed" in r.getMessage() for r in caplog.records)


def test_emitter_warns_once_while_hub_is_down(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="cobeart.audiocapture.emitter"):
        emitter = AudioEmitter(socketio_url="http://127.0.0.1:9")
        for _ in range(50):
            emitter.push_metrics({"rms": 0.0})
        emitter.stop()
    warnings = [r for r in caplog.records if "Cannot reach" in r.getMessage()]
    assert len(warnings) == 1

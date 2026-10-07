import numpy as np

from cobeart.audiocapture.capture import meter_line


class _Capturer:
    """Metric getters return constants; has_beat counts calls like the real consuming one."""

    def __init__(self) -> None:
        self.has_beat_calls: int = 0

    def get_rms(self, data: np.ndarray) -> float:
        return 0.1

    get_peak_amplitude = get_zero_crossing_rate = get_dominant_frequency = get_rms

    def has_beat(self) -> tuple[bool, float | None, float | None]:
        self.has_beat_calls += 1
        return True, 120.0, 1.5


DATA = np.zeros(8, dtype=np.float32)


def test_meter_does_not_consume_beats_unless_asked() -> None:
    capturer = _Capturer()
    meter_line(capturer, DATA, show_beat=False)  # type: ignore[arg-type]
    assert capturer.has_beat_calls == 0


def test_meter_shows_beat_and_tempo_when_it_is_the_consumer() -> None:
    capturer = _Capturer()
    line = meter_line(capturer, DATA, show_beat=True)  # type: ignore[arg-type]
    assert capturer.has_beat_calls == 1
    assert "BEAT" in line and "120.0 BPM" in line


def test_main_wires_show_beat_only_to_no_emit() -> None:
    import inspect

    from cobeart.audiocapture import capture
    assert "show_beat=beat_detection and args.no_emit" in inspect.getsource(capture.main)

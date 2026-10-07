"""Audio scenario tests: contract validity and beat behavior."""
import numpy as np
import pytest
from jsonschema import Draft202012Validator

from cobeart.simulator.audio import (
    SIGNALS,
    AudioConfig,
    TempoChange,
    beat_times,
    build_audio_scenario,
    parse_tempo_change,
)
from cobeart.simulator.models import AudioMessage
from cobeart.simulator.tests.helpers import audio_config


def _run(name: str, cfg: AudioConfig, seconds: float, seed: int = 0) -> list[AudioMessage]:
    scenario = build_audio_scenario(name, cfg)
    rng = np.random.default_rng(seed)
    return [scenario(i * cfg.dt, rng) for i in range(int(seconds * cfg.rate_hz))]


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_every_message_matches_contract(name: str, audio_validator: Draft202012Validator) -> None:
    cfg = audio_config(bpm=120.0, tempo_changes=(TempoChange(90.0, 8.0),))
    messages: list[AudioMessage] = _run(name, cfg, 14.0)
    # Schema validation of spectrum_2d is slow: sample the stream, but always check beat messages.
    for index, message in enumerate(messages):
        if index % 13 == 0 or message.beat:
            audio_validator.validate(message.to_payload())


def test_non_beat_scenarios_never_beat() -> None:
    for name in ("silence", "tone", "sweep"):
        messages = _run(name, audio_config(), 3.0)
        assert all(not m.beat and m.tempo_bpm is None and m.beat_timestamp is None for m in messages)


def test_tone_has_fixed_dominant_frequency() -> None:
    freqs: set[float] = {m.dominant_frequency for m in _run("tone", audio_config(tone_hz=440.0), 1.0)}
    assert len(freqs) == 1
    assert abs(freqs.pop() - 440.0) < 47.0


def test_sweep_frequency_rises() -> None:
    messages: list[AudioMessage] = _run("sweep", audio_config(), 9.0)
    assert messages[-1].dominant_frequency > messages[0].dominant_frequency * 5


def test_beat_locks_in_then_reports_stable_tempo_and_beats_at_bpm() -> None:
    cfg = audio_config(bpm=120.0, lock_in_s=4.0)
    messages: list[AudioMessage] = _run("beat", cfg, 16.0)
    early = messages[: int(4.0 * cfg.rate_hz)]
    assert all(m.tempo_bpm is None and not m.beat for m in early)
    late = messages[int(4.0 * cfg.rate_hz):]
    assert all(m.tempo_bpm == 120.0 for m in late)
    beats: list[float] = [m.beat_timestamp for m in late if m.beat_timestamp is not None]
    assert abs(len(beats) - 24) <= 1
    assert np.allclose(np.diff(beats), 0.5, atol=1e-6)
    assert all(m.beat == (m.beat_timestamp is not None) for m in messages)


def test_beat_message_is_nearest_to_beat_time() -> None:
    cfg = audio_config(bpm=120.0)
    for i, m in enumerate(_run("beat", cfg, 10.0)):
        if m.beat_timestamp is not None:
            assert abs(i * cfg.dt - (m.beat_timestamp - cfg.start_epoch_s)) <= cfg.dt / 2 + 1e-9


def test_beat_rms_pulses_at_beats() -> None:
    messages: list[AudioMessage] = _run("beat", audio_config(bpm=120.0), 6.0)
    on_beat: list[float] = [m.rms for m in messages if m.is_peak]
    assert on_beat
    assert min(on_beat) > 3 * min(m.rms for m in messages)


def test_tempo_change_drops_tempo_to_null_then_new_bpm() -> None:
    cfg = audio_config(bpm=120.0, tempo_changes=(TempoChange(90.0, 10.0),), lock_in_s=3.0)
    messages: list[AudioMessage] = _run("beat", cfg, 20.0)
    assert messages[int(9.0 * cfg.rate_hz)].tempo_bpm == 120.0
    assert messages[int(11.0 * cfg.rate_hz)].tempo_bpm is None
    assert messages[int(15.0 * cfg.rate_hz)].tempo_bpm == 90.0


def test_jitter_is_seeded_and_moves_beats() -> None:
    jittered = audio_config(beat_jitter_ms=20.0, seed=5)
    assert np.array_equal(beat_times(jittered)[:50], beat_times(audio_config(beat_jitter_ms=20.0, seed=5))[:50])
    assert not np.allclose(beat_times(jittered)[:50], beat_times(audio_config())[:50])


def test_parse_tempo_change() -> None:
    assert parse_tempo_change("90@12.5") == TempoChange(90.0, 12.5)
    with pytest.raises(ValueError, match="BPM@SECONDS"):
        parse_tempo_change("90")


def test_same_seed_same_stream() -> None:
    cfg = audio_config()
    assert _run("beat", cfg, 2.0, seed=1) == _run("beat", cfg, 2.0, seed=1)

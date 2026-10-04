"""Pure audio scenarios `(t, rng) -> AudioMessage` producing contract-shaped audio_metrics messages.

Every scenario emits the beat fields. Only `beat` produces beats: it places them on a (possibly jittered, possibly
tempo-changing) grid, reports `tempo_bpm` as null while the simulated tracker is locking in, and never reports a
beat while the tempo is null (the schema requires a tempo whenever `beat` is true).
"""
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import numpy as np
import numpy.typing as npt

from cobeart.simulator.models import AudioMessage, SpectrumConfig

Vector = npt.NDArray[np.float64]
AudioScenario = Callable[[float, np.random.Generator], AudioMessage]

NOISE_FRACTION: float = 0.02
ENVELOPE_REPLAY_S: float = 1.0
BEAT_PULSE_TAU_S: float = 0.12
BEAT_BASE_RMS: float = 0.03
BEAT_PULSE_RMS: float = 0.30
BEAT_FREQ_HZ: float = 110.0
PEAK_THRESHOLD_RMS: float = 0.12
MIN_PEAK_GAP_S: float = 0.3
ONSET_DECAY_S: float = 0.04
CREST_FACTOR: float = 2.5
SWEEP_PERIOD_S: float = 10.0
SWEEP_RANGE_HZ: tuple[float, float] = (100.0, 4000.0)


@dataclass(frozen=True, slots=True)
class TempoChange:
    """A tempo change to `bpm` at `at_s` seconds."""
    bpm: float
    at_s: float


@dataclass(frozen=True, slots=True)
class AudioConfig:
    """Parameters of the simulated audio stream (defaults match today's audiocapture settings)."""
    rate_hz: float = 100.0
    sample_rate: int = 48000
    chunk_size: int = 1024
    spectrum_bins: int = 128
    spectrum_history: int = 16
    freq_min: float = 20.0
    freq_max: float = 20000.0
    tone_hz: float = 440.0
    bpm: float = 120.0
    beat_jitter_ms: float = 0.0
    tempo_changes: tuple[TempoChange, ...] = ()
    lock_in_s: float = 4.0
    horizon_s: float = 3600.0
    seed: int = 0
    start_epoch_s: float = 1_800_000_000.0

    @property
    def dt(self) -> float:
        return 1.0 / self.rate_hz


def parse_tempo_change(text: str) -> TempoChange:
    """Parse 'BPM@SECONDS'."""
    bpm_text, sep, at_text = text.partition("@")
    if not sep:
        raise ValueError(f"Tempo change must look like BPM@SECONDS, got {text!r}")
    return TempoChange(bpm=float(bpm_text), at_s=float(at_text))


def beat_times(cfg: AudioConfig) -> Vector:
    """Sorted beat times in seconds from stream start, following the tempo changes, with seeded jitter."""
    changes: list[TempoChange] = sorted(cfg.tempo_changes, key=lambda c: c.at_s)
    times: list[float] = []
    t: float = 0.0
    bpm: float = cfg.bpm
    pending: list[TempoChange] = list(changes)
    while t < cfg.horizon_s:
        times.append(t)
        t += 60.0 / bpm
        while pending and t >= pending[0].at_s:
            bpm = pending.pop(0).bpm
    grid: Vector = np.array(times)
    jitter: Vector = np.random.default_rng(cfg.seed).normal(0.0, cfg.beat_jitter_ms / 1000.0, size=grid.shape)
    return np.sort(np.maximum(grid + jitter, 0.0))


def _locked_tempo(t: float, cfg: AudioConfig) -> float | None:
    """Tempo reported at time t: null during lock-in at start and after each tempo change."""
    if t < cfg.lock_in_s:
        return None
    bpm: float = cfg.bpm
    for change in sorted(cfg.tempo_changes, key=lambda c: c.at_s):
        if t >= change.at_s:
            if t < change.at_s + cfg.lock_in_s:
                return None
            bpm = change.bpm
    return bpm


def _log_bins(cfg: AudioConfig) -> tuple[Vector, float]:
    f_max: float = min(cfg.freq_max, cfg.sample_rate / 2)
    return np.geomspace(cfg.freq_min, f_max, cfg.spectrum_bins), f_max


def _db_unit(rms: float) -> float:
    return float(np.clip((20.0 * math.log10(max(rms, 1e-6)) + 60.0) / 60.0, 0.0, 1.0))


def _quantize(freq: float, cfg: AudioConfig) -> float:
    resolution: float = cfg.sample_rate / cfg.chunk_size
    return float(np.clip(round(freq / resolution) * resolution, 0.0, cfg.sample_rate / 2))


@dataclass(frozen=True, slots=True)
class _Signal:
    """Noise-free instantaneous signal description."""
    rms: float
    freq: float


SignalFn = Callable[[float], _Signal]


def _silence_signal(cfg: AudioConfig, beats: Vector) -> SignalFn:
    return lambda t: _Signal(rms=0.0, freq=0.0)


def _tone_signal(cfg: AudioConfig, beats: Vector) -> SignalFn:
    return lambda t: _Signal(rms=0.2, freq=cfg.tone_hz)


def _sweep_signal(cfg: AudioConfig, beats: Vector) -> SignalFn:
    low, high = SWEEP_RANGE_HZ

    def signal(t: float) -> _Signal:
        frac: float = (t % SWEEP_PERIOD_S) / SWEEP_PERIOD_S
        return _Signal(rms=0.2, freq=low * (high / low) ** frac)

    return signal


def _since_last_beat(t: float, beats: Vector) -> float:
    idx: int = int(np.searchsorted(beats, t, side="right")) - 1
    return t - float(beats[idx]) if idx >= 0 else math.inf


def _beat_signal(cfg: AudioConfig, beats: Vector) -> SignalFn:
    def signal(t: float) -> _Signal:
        pulse: float = math.exp(-_since_last_beat(t, beats) / BEAT_PULSE_TAU_S)
        return _Signal(rms=BEAT_BASE_RMS + BEAT_PULSE_RMS * pulse, freq=BEAT_FREQ_HZ)

    return signal


SIGNALS: dict[str, Callable[[AudioConfig, Vector], SignalFn]] = {
    "silence": _silence_signal,
    "tone": _tone_signal,
    "beat": _beat_signal,
    "sweep": _sweep_signal,
}


def _envelope(t: float, signal: SignalFn, cfg: AudioConfig) -> float:
    """Fast-attack (0.7), slow-release (0.95) smoothing of rms_db, replayed over the last second."""
    steps: int = int(ENVELOPE_REPLAY_S * cfg.rate_hz)
    env: float = 0.0
    for k in range(steps, -1, -1):
        level: float = _db_unit(signal(t - k * cfg.dt).rms)
        coeff: float = 0.7 if level > env else 0.95
        env = coeff * env + (1.0 - coeff) * level
    return env


def _spectrum(t: float, signal: SignalFn, cfg: AudioConfig) -> tuple[tuple[float, ...], ...]:
    """History rows (oldest first): a Gaussian bump in log-frequency at the dominant frequency."""
    bins, _ = _log_bins(cfg)
    log_bins: Vector = np.log10(bins)
    rows: list[tuple[float, ...]] = []
    for row in range(cfg.spectrum_history):
        sig: _Signal = signal(t - (cfg.spectrum_history - 1 - row) * cfg.dt)
        if sig.rms <= 0.0 or sig.freq <= 0.0:
            rows.append(tuple(0.0 for _ in bins))
            continue
        level: float = _db_unit(sig.rms)
        bump: Vector = level * np.exp(-0.5 * ((log_bins - math.log10(sig.freq)) / 0.08) ** 2)
        rows.append(tuple(float(v) for v in np.clip(bump, 0.0, 1.0)))
    return tuple(rows)


def _audio_message(t: float, rng: np.random.Generator, cfg: AudioConfig, name: str, beats: Vector) -> AudioMessage:
    signal: SignalFn = SIGNALS[name](cfg, beats)
    current: _Signal = signal(t)
    noise: float = 1.0 + NOISE_FRACTION * float(rng.standard_normal())
    noisy_rms: float = max(0.0, current.rms * noise)
    peak: float = min(noisy_rms * CREST_FACTOR, 1.0)
    freq: float = _quantize(current.freq, cfg)
    zcr: float = min(2.0 * freq / cfg.sample_rate, 1.0)

    window_start: float = t - cfg.dt / 2
    window_end: float = t + cfg.dt / 2
    beat_in_window: bool = name == "beat" and bool(np.any((beats >= window_start) & (beats < window_end)))
    tempo: float | None = _locked_tempo(t, cfg) if name == "beat" else None
    beat: bool = beat_in_window and tempo is not None
    beat_time: float | None = None
    if beat:
        idx: int = int(np.searchsorted(beats, window_start, side="left"))
        beat_time = cfg.start_epoch_s + float(beats[idx])

    # Peak and onset follow the audible beat grid regardless of tracker lock-in.
    gap_ok: bool = True
    if beat_in_window:
        idx_w: int = int(np.searchsorted(beats, window_start, side="left"))
        gap_ok = idx_w == 0 or float(beats[idx_w] - beats[idx_w - 1]) >= MIN_PEAK_GAP_S
    since: float = _since_last_beat(t, beats) if name == "beat" else math.inf
    onset_strength: float = min(5.0, 4.0 * math.exp(-since / ONSET_DECAY_S)) if name == "beat" else 0.0
    is_peak: bool = beat_in_window and gap_ok
    peak_intensity: float = max(0.0, (noisy_rms - PEAK_THRESHOLD_RMS) / PEAK_THRESHOLD_RMS) if name == "beat" else 0.0

    _, f_max = _log_bins(cfg)
    return AudioMessage(
        rms=noisy_rms,
        peak=peak,
        zcr=zcr,
        dominant_frequency=freq,
        rms_db=_db_unit(noisy_rms),
        rms_envelope=_envelope(t, signal, cfg),
        is_peak=is_peak,
        peak_intensity=peak_intensity,
        is_onset=onset_strength > 1.0,
        onset_strength=onset_strength,
        spectrum_2d=_spectrum(t, signal, cfg),
        spectrum_config=SpectrumConfig(
            width=cfg.spectrum_bins, height=cfg.spectrum_history, freq_min=cfg.freq_min, freq_max=cfg.freq_max
        ),
        beat=beat,
        tempo_bpm=tempo,
        beat_timestamp=beat_time,
    )


def build_audio_scenario(name: str, cfg: AudioConfig) -> AudioScenario:
    """Bind a named audio scenario to its config, yielding the `(t, rng) -> AudioMessage` callable."""
    if name not in SIGNALS:
        raise ValueError(f"Unknown audio scenario {name!r}, choose from {sorted(SIGNALS)}")
    beats: Vector = beat_times(cfg) if name == "beat" else np.empty(0)
    return partial(_audio_message, cfg=cfg, name=name, beats=beats)

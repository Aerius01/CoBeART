"""Frozen payload models mirroring contract/frame.schema.json and contract/audio_metrics.schema.json."""
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION: int = 1


@dataclass(frozen=True, slots=True)
class RigidBody:
    """One tracked body in arena coordinates (mm, unit quaternion, mm/s, deg/s)."""
    ID: int
    x: int
    y: int
    z: int
    qx: float
    qy: float
    qz: float
    qw: float
    vx: float
    vy: float
    vz: float
    wx: float
    wy: float
    wz: float
    abs_vel: float
    norm_abs_vel: float


@dataclass(frozen=True, slots=True)
class Frame:
    """A motion frame as sent by the producer on /ingest (never carries audio)."""
    timestamp: float  # epoch milliseconds
    rigidbodies: tuple[RigidBody, ...]
    schemaVersion: int = SCHEMA_VERSION
    type: str = "optitrack"

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready dict with the field set the contract defines."""
        return {
            "schemaVersion": self.schemaVersion,
            "type": self.type,
            "timestamp": self.timestamp,
            "rigidbodies": [asdict(body) for body in self.rigidbodies],
        }


@dataclass(frozen=True, slots=True)
class SpectrumConfig:
    """Shape and frequency range of spectrum_2d."""
    width: int
    height: int
    freq_min: float
    freq_max: float


@dataclass(frozen=True, slots=True)
class AudioMessage:
    """An audio_metrics message as sent by the producer on /audio (no timestamp, the hub adds it)."""
    rms: float
    peak: float
    zcr: float
    dominant_frequency: float
    rms_db: float
    rms_envelope: float
    is_peak: bool
    peak_intensity: float
    is_onset: bool
    onset_strength: float
    spectrum_2d: tuple[tuple[float, ...], ...]
    spectrum_config: SpectrumConfig
    beat: bool
    tempo_bpm: float | None
    beat_timestamp: float | None  # epoch seconds
    schemaVersion: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready dict with the field set the contract defines."""
        payload: dict[str, Any] = asdict(self)
        payload["spectrum_2d"] = [list(row) for row in self.spectrum_2d]
        return payload

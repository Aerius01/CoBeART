"""Frozen payload models mirroring contract/pose_frame.schema.json and contract/audio_metrics.schema.json."""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from cobeart.settings.config import Role

SCHEMA_VERSION: int = 2


@dataclass(frozen=True, slots=True)
class PoseBody:
    """One tracked body's pose in arena coordinates (integer mm, unit quaternion). Velocities are the hub's job."""
    x: int
    y: int
    z: int
    qx: float
    qy: float
    qz: float
    qw: float


@dataclass(frozen=True, slots=True)
class PoseFrame:
    """A pose_frame as sent by the producer on /ingest: bodies keyed by role, clocked by scenario time."""
    source_time_s: float  # scenario time in seconds, from 0
    bodies: Mapping[Role, PoseBody]
    schemaVersion: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready dict with the field set the contract defines."""
        return {
            "schemaVersion": self.schemaVersion,
            "source_time_s": self.source_time_s,
            "bodies": {role: asdict(body) for role, body in self.bodies.items()},
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

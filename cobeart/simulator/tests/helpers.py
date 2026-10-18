"""Config builders for simulator tests, taking arena, rates and roles from the real config/cobeart.yaml."""
from dataclasses import replace
from typing import Any

from cobeart.settings.config import Settings, load_settings
from cobeart.simulator.audio import AudioConfig
from cobeart.simulator.scenarios import MotionConfig

SETTINGS: Settings = load_settings({})


def motion_config(num_bodies: int = 4, **overrides: Any) -> MotionConfig:
    """MotionConfig with `num_bodies` bodies from the configured roles, with fields overridden by keyword."""
    return replace(MotionConfig.from_settings(SETTINGS, num_bodies=num_bodies), **overrides)


def audio_config(**overrides: Any) -> AudioConfig:
    """AudioConfig with the sample rate and chunk size from the config, overridden by keyword."""
    base: AudioConfig = AudioConfig(sample_rate=SETTINGS.audio.sample_rate, chunk_size=SETTINGS.audio.chunk_size)
    return replace(base, **overrides)

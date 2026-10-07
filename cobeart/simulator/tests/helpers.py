"""Config builders for simulator tests, taking arena, rates and IDs from the real config/cobeart.yaml."""
from dataclasses import replace
from typing import Any

from cobeart.settings.config import Settings, load_settings
from cobeart.simulator.audio import AudioConfig
from cobeart.simulator.scenarios import MotionConfig, load_body_map

SETTINGS: Settings = load_settings({})


def motion_config(**overrides: Any) -> MotionConfig:
    """MotionConfig at 240 Hz with 4 bodies at epoch 1.8e12 ms, with fields overridden by keyword."""
    base: MotionConfig = MotionConfig.from_settings(
        SETTINGS, load_body_map(), rate_hz=240.0, num_bodies=4, start_epoch_ms=1.8e12
    )
    return replace(base, **overrides)


def audio_config(**overrides: Any) -> AudioConfig:
    """AudioConfig with the sample rate and chunk size from the config, overridden by keyword."""
    base: AudioConfig = AudioConfig(sample_rate=SETTINGS.audio.sample_rate, chunk_size=SETTINGS.audio.chunk_size)
    return replace(base, **overrides)

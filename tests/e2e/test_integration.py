"""End-to-end: simulator (in process) -> real hub -> /viewer subscriber."""
import time

import socketio
from jsonschema import Draft202012Validator

from cobeart.simulator.__main__ import main
from cobeart.simulator.audio import build_audio_scenario
from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.scenarios import build_motion_scenario
from cobeart.simulator.tests.helpers import audio_config, motion_config
from tests.e2e.harness import FrameMessage, Hub, Viewer, assert_all_valid, body_ids, frames_until_marker

DELIVERY_TIMEOUT_S: float = 30.0
ORBIT_IDS: set[int] = {0, 1, 2, 3}


def _assert_frames(received: list[FrameMessage], frame_validator: Draft202012Validator) -> None:
    assert len(received) > 50
    assert any("audio" in frame for frame in received), "no frame carried merged audio"
    assert_all_valid(received, frame_validator)
    assert set(body_ids(received[-1])) == ORBIT_IDS


def test_simulator_frames_with_merged_audio_reach_viewer(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator
) -> None:
    motion_cfg = motion_config(rate_hz=120.0, start_epoch_ms=time.time() * 1000)
    audio_cfg = audio_config(start_epoch_s=time.time(), lock_in_s=0.5)
    simulator = Simulator(
        client=socketio.Client(),
        motion=build_motion_scenario("orbit", motion_cfg),
        audio=build_audio_scenario("beat", audio_cfg),
        run=RunConfig(url=shared_hub.url, motion_rate_hz=120.0, audio_rate_hz=100.0, seed=0, duration_s=2.0),
    )
    sent: dict[str, int] = simulator.run()

    assert sent["frame"] > 200 and sent["audio_metrics"] > 150
    received: list[FrameMessage] = frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S)
    assert len(received) <= sent["frame"]
    _assert_frames(received, frame_validator)


def test_cli_streams_to_hub(shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator) -> None:
    argv: list[str] = [
        "--scenario", "orbit", "--audio", "beat", "--url", shared_hub.url, "--duration", "2", "--rate", "120",
    ]
    main(argv)

    received: list[FrameMessage] = frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S)
    assert len(received) <= 240  # 2 s at 120 Hz
    _assert_frames(received, frame_validator)

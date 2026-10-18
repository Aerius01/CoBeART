"""End-to-end: simulator (in process) -> real hub -> /viewer subscriber, frames and audio as separate streams."""
import time

import socketio
from jsonschema import Draft202012Validator

from cobeart.simulator.__main__ import main
from cobeart.simulator.audio import build_audio_scenario
from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.tests.helpers import audio_config
from tests.e2e.harness import (
    AudioMessage, FrameMessage, Hub, Viewer, assert_all_valid, frames_until_marker, make_scenario,
)

DELIVERY_TIMEOUT_S: float = 30.0
MIN_AUDIO_MESSAGES: int = 50


def _assert_frames_without_audio(received: list[FrameMessage], frame_validator: Draft202012Validator) -> None:
    assert len(received) > 50
    assert_all_valid(received, frame_validator)
    assert not any("audio" in frame for frame in received), "a frame carried audio; audio has its own event now"
    assert all(frame["bodies"] for frame in received)


def test_simulator_frames_and_audio_reach_viewer_separately(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator,
    audio_validator: Draft202012Validator,
) -> None:
    simulator = Simulator(
        client=socketio.Client(),
        motion=make_scenario("orbit"),
        audio=build_audio_scenario("beat", audio_config(start_epoch_s=time.time(), lock_in_s=0.5)),
        run=RunConfig(url=shared_hub.url, motion_rate_hz=120.0, audio_rate_hz=100.0, seed=0, duration_s=2.0),
    )
    sent: dict[str, int] = simulator.run()

    sent_frames: int = sum(count for event, count in sent.items() if event != "audio_metrics")  # event name is 6.2's
    assert sent_frames > 200 and sent["audio_metrics"] > 150
    received: list[FrameMessage] = frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S)
    assert len(received) <= sent_frames
    _assert_frames_without_audio(received, frame_validator)

    audio: list[AudioMessage] = shared_viewer.wait_for_audio_count(MIN_AUDIO_MESSAGES, DELIVERY_TIMEOUT_S)
    assert len(audio) <= sent["audio_metrics"]
    assert_all_valid(audio, audio_validator, "audio")


def test_cli_streams_to_hub(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator,
    audio_validator: Draft202012Validator,
) -> None:
    argv: list[str] = [
        "--scenario", "orbit", "--audio", "beat", "--url", shared_hub.url, "--duration", "2", "--rate", "120",
    ]
    main(argv)

    received: list[FrameMessage] = frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S)
    assert len(received) <= 240  # 2 s at 120 Hz
    _assert_frames_without_audio(received, frame_validator)
    audio: list[AudioMessage] = shared_viewer.wait_for_audio_count(MIN_AUDIO_MESSAGES, DELIVERY_TIMEOUT_S)
    assert_all_valid(audio, audio_validator, "audio")


def test_audio_without_motion_arrives_with_no_frames(
    shared_hub: Hub, shared_viewer: Viewer, audio_validator: Draft202012Validator
) -> None:
    main(["--scenario", "none", "--audio", "beat", "--url", shared_hub.url, "--duration", "2"])

    audio: list[AudioMessage] = shared_viewer.wait_for_audio_count(MIN_AUDIO_MESSAGES, DELIVERY_TIMEOUT_S)
    assert_all_valid(audio, audio_validator, "audio")
    assert frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S) == [], "audio alone made frames"


def test_motion_without_audio_has_no_audio_anywhere(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator
) -> None:
    main(["--scenario", "orbit", "--audio", "none", "--url", shared_hub.url, "--duration", "2", "--rate", "120"])

    received: list[FrameMessage] = frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S)
    _assert_frames_without_audio(received, frame_validator)
    assert shared_viewer.audios() == [], "audio reached the viewer although none was sent"

"""Socket-level simulator against the real hub: stale audio, fault rejection and rigid-body ID integrity."""
import time
from typing import Any

import numpy as np
import pytest
import socketio
from jsonschema import Draft202012Validator

from cobeart.settings.config import Settings
from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.models import Frame
from cobeart.simulator.scenarios import MISSING_CYCLE_S, MISSING_DURATION_S, Scenario, build_motion_scenario
from cobeart.simulator.tests.helpers import motion_config
from tests.e2e.harness import (
    FrameMessage, Hub, Viewer, assert_all_valid, body_ids, frames_until_marker, run_python, wait_until,
)

DELIVERY_TIMEOUT_S: float = 30.0
SIMULATOR_TIMEOUT_S: float = 60.0
MOTION_RATE_HZ: float = 60.0
AUDIO_STOP_AFTER_S: float = 1.0
STALE_RUN_S: float = 2.5  # motion keeps going for 1.5 s after the audio stops, well past audio_max_age_ms
MIN_FRAMES_PER_PHASE: int = 10


def _simulator_args(url: str, *extra: str) -> list[str]:
    return ["-m", "cobeart.simulator", "--url", url, "--rate", str(MOTION_RATE_HZ), "--seed", "0", *extra]


def test_merged_audio_goes_stale_after_audio_stops(
    fresh_hub: Hub, fresh_viewer: Viewer, settings: Settings, frame_validator: Draft202012Validator
) -> None:
    max_age_ms: float = settings.hub.audio_max_age_ms
    run_python(_simulator_args(
        fresh_hub.url, "--scenario", "orbit", "--audio", "tone", "--duration", str(STALE_RUN_S),
        "--audio-stop-after", str(AUDIO_STOP_AFTER_S),
    ), SIMULATOR_TIMEOUT_S)
    frames: list[FrameMessage] = frames_until_marker(fresh_hub.url, fresh_viewer, DELIVERY_TIMEOUT_S)
    assert_all_valid(frames, frame_validator)

    # Judged on hub timestamps only: the frame's receipt time against the merged audio's receipt time.
    latest_audio_ms: float | None = None
    audio_gone: bool = False
    with_audio: int = 0
    without_audio_after_audio: int = 0
    for index, frame in enumerate(frames):
        if "audio" in frame:
            audio_ms: float = frame["audio"]["timestamp"]
            assert not audio_gone, f"frame {index}: audio came back after going stale, but the simulator stopped it"
            assert frame["timestamp"] - audio_ms <= max_age_ms, f"frame {index} merged audio older than {max_age_ms} ms"
            assert latest_audio_ms is None or audio_ms >= latest_audio_ms, f"frame {index}: merged audio went back"
            latest_audio_ms = audio_ms
            with_audio += 1
        elif latest_audio_ms is not None:
            # Dropped exactly when stale: the previous frame still had it, so it vanished at the first frame
            # stamped more than audio_max_age_ms after the last audio the hub accepted.
            age_ms: float = frame["timestamp"] - latest_audio_ms
            assert age_ms > max_age_ms, f"frame {index} lost the audio while it was only {age_ms:.0f} ms old"
            audio_gone = True
            without_audio_after_audio += 1

    assert with_audio >= MIN_FRAMES_PER_PHASE, f"only {with_audio} frames carried merged audio"
    assert without_audio_after_audio >= MIN_FRAMES_PER_PHASE, (
        f"only {without_audio_after_audio} frames after the audio went stale"
    )


# What the hub logs for each fault: schema rejects for both message kinds, or a dropped connection for NaN.
FAULT_LOG_LINES: dict[str, tuple[str, ...]] = {
    "malformed": ("Rejected frame payload", "Rejected audio_metrics payload"),
    "wrong-version": ("Rejected frame payload: schemaVersion mismatch",
                      "Rejected audio_metrics payload: schemaVersion mismatch"),
    "nan": ("payload: undecodable packet",),
}


@pytest.mark.parametrize("fault", sorted(FAULT_LOG_LINES))
def test_faulty_payloads_never_reach_viewer(fault: str, fresh_hub: Hub, fresh_viewer: Viewer) -> None:
    run_python(_simulator_args(
        fresh_hub.url, "--scenario", "orbit", "--audio", "tone", "--duration", "1", "--fault", fault,
    ), SIMULATOR_TIMEOUT_S)
    for line in FAULT_LOG_LINES[fault]:
        wait_until(lambda: line in fresh_hub.log_text(), DELIVERY_TIMEOUT_S, f"hub log line {line!r}")

    health: dict[str, Any] = fresh_hub.health()
    assert health["lastFrameAge"] is None and health["lastAudioAge"] is None, f"hub accepted a message: {health}"
    # The marker is a valid frame sent after the faulty stream: it alone arriving shows the viewer was live.
    assert frames_until_marker(fresh_hub.url, fresh_viewer, DELIVERY_TIMEOUT_S) == []


def _recording(scenario: Scenario, sent: list[list[dict[str, Any]]], offset_s: float = 0.0) -> Scenario:
    """The scenario shifted by `offset_s`, recording the rigid bodies of every frame it produces."""
    def produce(t: float, rng: np.random.Generator) -> Frame:
        frame: Frame = scenario(t + offset_s, rng)
        sent.append(frame.to_payload()["rigidbodies"])
        return frame
    return produce


def _stream(hub: Hub, viewer: Viewer, name: str, duration_s: float, offset_s: float = 0.0,
            ) -> tuple[list[list[dict[str, Any]]], list[FrameMessage]]:
    """Run motion scenario `name` in process; return the bodies sent per frame and the frames the viewer got."""
    sent: list[list[dict[str, Any]]] = []
    cfg = motion_config(rate_hz=MOTION_RATE_HZ, start_epoch_ms=time.time() * 1000.0)
    Simulator(
        client=socketio.Client(),
        motion=_recording(build_motion_scenario(name, cfg), sent, offset_s),
        audio=None,
        run=RunConfig(url=hub.url, motion_rate_hz=MOTION_RATE_HZ, audio_rate_hz=100.0, seed=0, duration_s=duration_s),
    ).run()
    return sent, frames_until_marker(hub.url, viewer, DELIVERY_TIMEOUT_S)


def _assert_bodies_as_sent(sent: list[list[dict[str, Any]]], received: list[FrameMessage]) -> None:
    """The viewer got the sent frames in order, each body list unchanged (IDs, order and values)."""
    assert len(received) >= len(sent) // 2, f"viewer got {len(received)} of {len(sent)} frames"
    assert len(received) <= len(sent)
    for index, frame in enumerate(received):
        ids: list[int] = body_ids(frame)
        assert len(ids) == len(set(ids)), f"frame {index}: duplicated ID in {ids}"
        assert frame["rigidbodies"] == sent[index], f"frame {index}: bodies differ from what the simulator sent"


def test_shuffled_ids_arrive_intact(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator
) -> None:
    sent, received = _stream(shared_hub, shared_viewer, "shuffled", duration_s=1.0)
    assert_all_valid(received, frame_validator)
    _assert_bodies_as_sent(sent, received)
    expected: set[int] = set(range(motion_config().num_bodies))
    assert all(set(body_ids(frame)) == expected for frame in received)
    orders: set[tuple[int, ...]] = {tuple(body_ids(frame)) for frame in received}
    assert len(orders) > 1, "body order never changed, so the shuffle was lost on the way"


def test_missing_body_ids_arrive_intact(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator
) -> None:
    # Start half a second before the first gap (the left hand drops out at MISSING_CYCLE_S - MISSING_DURATION_S).
    gap_start_s: float = MISSING_CYCLE_S - MISSING_DURATION_S
    sent, received = _stream(shared_hub, shared_viewer, "missing-body", duration_s=1.0, offset_s=gap_start_s - 0.5)
    assert_all_valid(received, frame_validator)
    _assert_bodies_as_sent(sent, received)

    cfg = motion_config()
    everyone: set[int] = {cfg.left_hand_id, cfg.right_hand_id, cfg.head_id}
    without_left: set[int] = everyone - {cfg.left_hand_id}
    id_sets: list[set[int]] = [set(body_ids(frame)) for frame in received]
    assert all(ids in (everyone, without_left) for ids in id_sets), f"unexpected ID sets: {id_sets}"
    first_gap: int = id_sets.index(without_left)
    assert first_gap >= MIN_FRAMES_PER_PHASE, "the left hand dropped out too early"
    assert id_sets[:first_gap] == [everyone] * first_gap
    assert id_sets[first_gap:] == [without_left] * (len(id_sets) - first_gap), "the left hand came back mid-gap"
    assert len(id_sets) - first_gap >= MIN_FRAMES_PER_PHASE, "too few frames inside the gap"

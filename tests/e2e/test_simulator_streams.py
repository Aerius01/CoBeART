"""Socket-level simulator against the real hub: fault rejection, missing roles, hub velocities, no replay."""
import math
import time
from typing import Any

import numpy as np
import pytest
import socketio
from jsonschema import Draft202012Validator

from cobeart.settings.config import Settings
from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.scenarios import MISSING_CYCLE_S, MISSING_DURATION_S, Scenario
from tests.contract.schemas import load_fixture
from tests.e2e.harness import (
    FrameMessage, Hub, Viewer, assert_all_valid, emit_once, frames_until_marker, make_scenario, roles_of,
    run_python, scenario_payload, start_python, wait_until,
)

DELIVERY_TIMEOUT_S: float = 30.0
SIMULATOR_TIMEOUT_S: float = 60.0
MOTION_RATE_HZ: float = 60.0
MIN_FRAMES_PER_PHASE: int = 10
QUIET_WAIT_S: float = 1.0  # how long "a viewer receives nothing" is observed
AUDIO_QUIET_MS: float = 300.0


def _simulator_args(url: str, *extra: str) -> list[str]:
    return ["-m", "cobeart.simulator", "--url", url, "--rate", str(MOTION_RATE_HZ), "--seed", "0", *extra]


# What the hub logs for each fault: a schema reject, or a dropped connection for NaN. The reject wording is
# WP 6.1's; only the shared prefix is matched so the event name inside the message does not matter.
FAULT_LOG_LINES: dict[str, tuple[str, ...]] = {
    "malformed": ("Rejected",),
    "wrong-version": ("Rejected",),
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
    assert fresh_viewer.audios() == [], "a faulty audio message reached the viewer"
    # The marker is a valid pose_frame sent after the faulty stream: it alone arriving shows the viewer was live.
    assert frames_until_marker(fresh_hub.url, fresh_viewer, DELIVERY_TIMEOUT_S) == []


def test_v1_payloads_never_reach_viewer(fresh_hub: Hub, fresh_viewer: Viewer) -> None:
    v1_pose_frame: dict[str, Any] = {**load_fixture("pose_frame"), "schemaVersion": 1}
    v1_frame: dict[str, Any] = {  # the v1 `frame` message of the old /ingest contract
        "schemaVersion": 1, "type": "optitrack", "timestamp": time.time() * 1000.0,
        "rigidbodies": [{"ID": 0, "x": 0, "y": 0, "z": 1000, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
                         "vx": 0.0, "vy": 0.0, "vz": 0.0, "wx": 0.0, "wy": 0.0, "wz": 0.0,
                         "abs_vel": 0.0, "norm_abs_vel": 0.0}],
    }
    v1_audio: dict[str, Any] = {**load_fixture("audio_metrics"), "schemaVersion": 1}
    emit_once(fresh_hub.url, "pose_frame", v1_pose_frame)
    emit_once(fresh_hub.url, "frame", v1_frame)
    emit_once(fresh_hub.url, "audio_metrics", v1_audio, namespace="/audio")
    wait_until(lambda: "Rejected" in fresh_hub.log_text(), DELIVERY_TIMEOUT_S, "the hub to reject a v1 payload")

    health: dict[str, Any] = fresh_hub.health()
    assert health["lastFrameAge"] is None and health["lastAudioAge"] is None, f"hub accepted a v1 message: {health}"
    assert fresh_viewer.audios() == []
    assert frames_until_marker(fresh_hub.url, fresh_viewer, DELIVERY_TIMEOUT_S) == []


def _offset(scenario: Scenario, offset_s: float) -> Scenario:
    """The scenario shifted by `offset_s` in time."""
    return lambda t, rng: scenario(t + offset_s, rng)


def _recording(scenario: Scenario, sent: list[dict[str, Any]]) -> Scenario:
    """The scenario, recording the payload of every frame it produces."""
    def produce(t: float, rng: np.random.Generator) -> Any:
        frame = scenario(t, rng)
        sent.append(frame.to_payload())  # the one other place that depends on the Frame model (see scenario_payload)
        return frame
    return produce


def test_missing_role_is_absent_from_affected_frames(
    shared_hub: Hub, shared_viewer: Viewer, frame_validator: Draft202012Validator
) -> None:
    # Start half a second before the first gap (the left hand drops out at MISSING_CYCLE_S - MISSING_DURATION_S).
    gap_start_s: float = MISSING_CYCLE_S - MISSING_DURATION_S
    sent: list[dict[str, Any]] = []
    scenario: Scenario = _recording(_offset(make_scenario("missing-body", MOTION_RATE_HZ), gap_start_s - 0.5), sent)
    Simulator(
        client=socketio.Client(), motion=scenario, audio=None,
        run=RunConfig(url=shared_hub.url, motion_rate_hz=MOTION_RATE_HZ, audio_rate_hz=100.0, seed=0, duration_s=1.0),
    ).run()
    received: list[FrameMessage] = frames_until_marker(shared_hub.url, shared_viewer, DELIVERY_TIMEOUT_S)
    assert_all_valid(received, frame_validator)
    assert len(sent) // 2 <= len(received) <= len(sent)

    for index, frame in enumerate(received):  # positions arrive as sent, under the role they were sent with
        for role, body in frame["bodies"].items():
            pose: dict[str, Any] = sent[index]["bodies"][role]
            assert (body["x"], body["y"], body["z"]) == (pose["x"], pose["y"], pose["z"]), f"frame {index} {role}"

    everyone: set[str] = {"left_hand", "right_hand", "head"}
    without_left: set[str] = everyone - {"left_hand"}
    role_sets: list[set[str]] = [roles_of(frame) for frame in received]
    assert all(roles in (everyone, without_left) for roles in role_sets), f"unexpected role sets: {role_sets}"
    first_gap: int = role_sets.index(without_left)
    assert first_gap >= MIN_FRAMES_PER_PHASE, "the left hand dropped out too early"
    assert role_sets[:first_gap] == [everyone] * first_gap
    assert role_sets[first_gap:] == [without_left] * (len(role_sets) - first_gap), "the left hand came back mid-gap"
    assert len(role_sets) - first_gap >= MIN_FRAMES_PER_PHASE, "too few frames inside the gap"


ORBIT_RATE_HZ: float = 20.0  # slow enough that rounding positions to whole mm costs the speed under 4 %
ORBIT_DURATION_S: float = 4.0
ORBIT_TOLERANCE: float = 0.05
ORBIT_SAMPLE_DT_S: float = 0.1


def _analytic_speeds(scenario: Scenario) -> dict[str, float]:
    """Per role, the horizontal speed 2*pi*r/T of a circling body, read off the scenario's own positions.

    The radius is the distance from the arena centre; the period follows from the angle swept in a short step.
    """
    start: float = 1.0
    first = scenario_payload(scenario, start)["bodies"]
    second = scenario_payload(scenario, start + ORBIT_SAMPLE_DT_S)["bodies"]
    speeds: dict[str, float] = {}
    for role, a in first.items():
        b: dict[str, Any] = second[role]
        radius: float = math.hypot(a["x"], a["y"])
        swept: float = math.atan2(b["y"], b["x"]) - math.atan2(a["y"], a["x"])
        swept = (swept + math.pi) % math.tau - math.pi
        period_s: float = math.tau * ORBIT_SAMPLE_DT_S / abs(swept)
        speeds[role] = math.tau * radius / period_s
    return speeds


def test_orbit_speed_matches_the_analytic_circle(
    fresh_hub: Hub, fresh_viewer: Viewer, settings: Settings, frame_validator: Draft202012Validator
) -> None:
    scenario: Scenario = make_scenario("orbit", ORBIT_RATE_HZ)
    expected: dict[str, float] = _analytic_speeds(scenario)
    assert len(expected) > 1 and all(speed > 100.0 for speed in expected.values()), expected
    Simulator(
        client=socketio.Client(), motion=scenario, audio=None,
        run=RunConfig(url=fresh_hub.url, motion_rate_hz=ORBIT_RATE_HZ, audio_rate_hz=100.0, seed=0,
                      duration_s=ORBIT_DURATION_S),
    ).run()
    received: list[FrameMessage] = frames_until_marker(fresh_hub.url, fresh_viewer, DELIVERY_TIMEOUT_S)
    assert_all_valid(received, frame_validator)

    settled: list[FrameMessage] = received[settings.metrics.history_window + 1:]  # history window is full
    assert len(settled) >= 20, f"only {len(settled)} frames after the history window filled"
    for role, speed in expected.items():
        measured: list[float] = [frame["bodies"][role]["abs_vel"] for frame in settled]
        mean: float = sum(measured) / len(measured)
        assert mean == pytest.approx(speed, rel=ORBIT_TOLERANCE), f"{role}: mean abs_vel {mean:.1f} vs {speed:.1f}"
        assert max(abs(value - speed) for value in measured) <= 2 * ORBIT_TOLERANCE * speed, f"{role}: outlier"


def test_viewer_after_a_stopped_stream_receives_nothing(fresh_hub: Hub, fresh_viewer: Viewer) -> None:
    run_python(_simulator_args(
        fresh_hub.url, "--scenario", "orbit", "--audio", "tone", "--duration", "1",
    ), SIMULATOR_TIMEOUT_S)
    frames_until_marker(fresh_hub.url, fresh_viewer, DELIVERY_TIMEOUT_S)
    wait_until(lambda: (fresh_hub.health()["lastAudioAge"] or 0.0) >= AUDIO_QUIET_MS, DELIVERY_TIMEOUT_S,
               "the audio stream to go quiet")

    late: Viewer = Viewer(fresh_hub.url)  # never calls start_recording, so anything replayed would be kept
    late.connect()
    try:
        time.sleep(QUIET_WAIT_S)
    finally:
        late.disconnect()
    assert late.frames() == [], f"a late viewer was sent {len(late.frames())} frames"
    assert late.audios() == [], f"a late viewer was sent {len(late.audios())} audio messages"


def test_viewer_joining_mid_stream_gets_nothing_older_than_its_connection(
    fresh_hub: Hub, fresh_viewer: Viewer
) -> None:
    process = start_python(_simulator_args(
        fresh_hub.url, "--scenario", "orbit", "--audio", "tone", "--duration", "4",
    ))
    late: Viewer = Viewer(fresh_hub.url)
    try:
        fresh_viewer.wait_for_count(MIN_FRAMES_PER_PHASE, DELIVERY_TIMEOUT_S)
        connect_ms: float = time.time() * 1000.0 - 1.0  # the hub stamps whole ms, so allow 1 ms
        late.connect()
        late.wait_for_count(MIN_FRAMES_PER_PHASE, DELIVERY_TIMEOUT_S)
        late.wait_for_audio_count(MIN_FRAMES_PER_PHASE, DELIVERY_TIMEOUT_S)
    finally:
        late.disconnect()
        process.kill()
        process.communicate()
    stamps: list[float] = [message["timestamp"] for message in (*late.frames(), *late.audios())]
    assert min(stamps) >= connect_ms, f"the hub sent a message {connect_ms - min(stamps):.0f} ms older than the join"

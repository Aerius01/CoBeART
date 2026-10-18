"""The real Python motion pipeline against the real hub: NatNet-style source -> FramePipeline -> PayloadSender ->
hub (which derives the velocities) -> /viewer, in process and through the `start_client --simulate` CLI."""
import math
import signal
import subprocess
import threading
from dataclasses import dataclass

import pytest
from jsonschema import Draft202012Validator

from cobeart.input.optitrack.start_client import (
    DEFAULT_SYNTHETIC_PATHS, FrameListener, RigidBodyListener, arena_to_optitrack,
)
from cobeart.input.optitrack.transform import ArenaPose
from cobeart.settings.config import Settings
from tests.e2e.harness import (
    LOG_TAIL_CHARS, BodyMessage, FrameMessage, Hub, Viewer, assert_all_valid, build_motion_pipeline, roles_of,
    start_python, wait_until,
)

DELIVERY_TIMEOUT_S: float = 30.0
CONNECT_TIMEOUT_S: float = 15.0
EXIT_TIMEOUT_S: float = 15.0
BODY_ID: int = 0
SPEED_MM_S: float = 1300.0  # along arena +x
YAW_RATE_DEG_S: float = 90.0  # about arena +z
START_MM: tuple[float, float, float] = (-500.0, 250.0, 1200.0)
MOCAP_EPOCH_S: float = 100.0  # NatNet timestamps count seconds since Motive started, not wall time
REL_TOLERANCE: float = 1e-6
SPEED_TOLERANCE: float = 0.03  # positions are whole mm on the wire, so per-frame velocity carries rounding noise


@dataclass(frozen=True, slots=True)
class MoveThenStop:
    """One body moving at constant speed and yaw rate for `moving_frames` frames, then holding still."""
    rate_hz: float
    moving_frames: int
    still_frames: int

    @property
    def total_frames(self) -> int:
        return 1 + self.moving_frames + self.still_frames

    def arena_pose(self, frame: int) -> ArenaPose:
        t: float = min(frame, self.moving_frames) / self.rate_hz
        yaw: float = math.radians(YAW_RATE_DEG_S * t)
        x, y, z = START_MM
        return ArenaPose(
            position=(x + SPEED_MM_S * t, y, z), orientation=(0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2))
        )


class ScriptedNatNetSource:
    """MotionSource (see start_client.MotionSource) that plays a MoveThenStop script as OptiTrack-native poses.

    `run()` plays every frame synchronously on the caller's thread with scripted NatNet timestamps, so what the
    metrics see is fixed by the script, not by scheduling.
    """

    def __init__(self, script: MoveThenStop) -> None:
        self.new_frame_listener: FrameListener | None = None
        self.rigid_body_listener: RigidBodyListener | None = None
        self._script: MoveThenStop = script

    def run(self) -> bool:
        if self.new_frame_listener is None or self.rigid_body_listener is None:
            raise RuntimeError("ScriptedNatNetSource listeners must be set before run()")
        for frame in range(self._script.total_frames):
            position, rotation = arena_to_optitrack(self._script.arena_pose(frame))
            self.rigid_body_listener(BODY_ID, position, rotation, True)
            self.new_frame_listener({"frame_number": frame, "timestamp": MOCAP_EPOCH_S + frame / self._script.rate_hz})
        return True

    def shutdown(self) -> None:
        """Nothing to stop: run() returns once the script is played."""


def test_body_that_stops_reaches_zero_norm_velocity(
    fresh_hub: Hub, fresh_viewer: Viewer, settings: Settings, frame_validator: Draft202012Validator
) -> None:
    window: int = settings.metrics.history_window
    role: str = settings.tracking.roles[BODY_ID]
    # Mocap frames at the package rate, so the sender's rate gate passes every frame and frame k is viewer frame k.
    script = MoveThenStop(rate_hz=settings.tracking.package_framerate, moving_frames=4 * window,
                          still_frames=2 * window)
    failure = threading.Event()
    sender, pipeline = build_motion_pipeline(settings, fresh_hub.url, failure)
    source = ScriptedNatNetSource(script)
    pipeline.attach(source)
    sender.connect()
    try:
        wait_until(sender.is_connected, CONNECT_TIMEOUT_S, f"PayloadSender to connect to {fresh_hub.url}")
        source.run()
        frames: list[FrameMessage] = fresh_viewer.wait_for_count(script.total_frames, DELIVERY_TIMEOUT_S)
    finally:
        source.shutdown()
        sender.stop()
    assert not failure.is_set(), "the motion pipeline reported a failure"
    assert len(frames) == script.total_frames
    assert_all_valid(frames, frame_validator)

    bodies: list[BodyMessage] = []
    for index, frame in enumerate(frames):
        assert roles_of(frame) == {role}, f"frame {index}: {sorted(roles_of(frame))}"
        bodies.append(frame["bodies"][role])
    for index, body in enumerate(bodies):
        expected: ArenaPose = script.arena_pose(index)
        assert abs(body["x"] - expected.position[0]) <= 1 and abs(body["y"] - expected.position[1]) <= 1, (
            f"frame {index}: position ({body['x']}, {body['y']}) is not the scripted {expected.position}"
        )

    norm: float = SPEED_MM_S / settings.metrics.max_vel
    stop: int = script.moving_frames  # last frame that moved; frame stop + j has stood still for j frames
    assert bodies[0]["norm_abs_vel"] == 0.0, "a body's first frame has no velocity yet"
    moving: list[BodyMessage] = bodies[1:stop + 1]
    assert sum(body["abs_vel"] for body in moving) / len(moving) == pytest.approx(SPEED_MM_S, rel=SPEED_TOLERANCE)
    for index, body in enumerate(moving, start=1):
        assert body["wz"] == pytest.approx(YAW_RATE_DEG_S, rel=SPEED_TOLERANCE), f"frame {index}"
        assert body["norm_abs_vel"] > 0.0, f"frame {index} while moving"
    for still in range(1, script.still_frames + 1):
        body = bodies[stop + still]
        assert (body["abs_vel"], body["wx"], body["wy"], body["wz"]) == (0.0, 0.0, 0.0, 0.0), f"still frame {still}"
        if still < window:  # held at the window maximum until a full window of stillness
            assert norm * 0.5 < body["norm_abs_vel"] < norm * 1.5, f"still frame {still}: {body['norm_abs_vel']}"
        else:
            assert body["norm_abs_vel"] == 0.0, f"still frame {still}: expected 0 from still frame {window} on"


SIMULATE_FRAMES: int = 100


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_simulate_cli_streams_and_stops_cleanly(
    stop_signal: signal.Signals, shared_hub: Hub, shared_viewer: Viewer, settings: Settings,
    frame_validator: Draft202012Validator,
) -> None:
    expected_roles: set[str] = {settings.tracking.roles[path.body_id] for path in DEFAULT_SYNTHETIC_PATHS}
    process: subprocess.Popen[str] = start_python(
        ["-m", "cobeart.input.optitrack.start_client", "--simulate", "--url", shared_hub.url]
    )
    try:
        frames: list[FrameMessage] = shared_viewer.wait_for_count(SIMULATE_FRAMES, DELIVERY_TIMEOUT_S)
        process.send_signal(stop_signal)
        _, stderr = process.communicate(timeout=EXIT_TIMEOUT_S)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 0, f"exit code {process.returncode}:\n{stderr[-LOG_TAIL_CHARS:]}"
    assert "Traceback" not in stderr, stderr[-LOG_TAIL_CHARS:]
    assert_all_valid(frames, frame_validator)
    assert all(roles_of(frame) == expected_roles for frame in frames), "role keys differ from tracking.roles"

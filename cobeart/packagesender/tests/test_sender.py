import logging
import math
import threading
import time
from collections.abc import Callable

import numpy as np
import pytest
from socketio.exceptions import BadNamespaceError

from cobeart.optitrackclient.transform import ArenaPose
from cobeart.packagesender.metrics import MetricsTracker
from cobeart.packagesender.sender import INGEST_NAMESPACE, PayloadSender, StrictJson
from cobeart.packagesender.tests.contract import RecordingClient, frame_validator

MOCAP_HZ: float = 240.0
EPOCH_S: float = 1_800_000_000.0


def make_sender(
    client: RecordingClient, framerate: float = MOCAP_HZ, failure: threading.Event | None = None, **kwargs: float
) -> PayloadSender:
    return PayloadSender(client, MetricsTracker(), "http://127.0.0.1:1", framerate,
                         failure=failure if failure is not None else threading.Event(),
                         wall_clock=lambda: EPOCH_S, **kwargs)


def connected_sender(framerate: float = MOCAP_HZ) -> tuple[PayloadSender, RecordingClient]:
    client = RecordingClient()
    sender = make_sender(client, framerate)
    client.connect("http://127.0.0.1:1", ["websocket"], [INGEST_NAMESPACE], True, 1.0)
    return sender, client


def pose(x: float, y: float, z: float = 1000.0, yaw_rad: float = 0.0) -> ArenaPose:
    return ArenaPose(position=(x, y, z), orientation=(0.0, 0.0, math.sin(yaw_rad / 2), math.cos(yaw_rad / 2)))


def wait_for(condition: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline: float = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.005)


def test_emitted_frames_validate_against_contract() -> None:
    sender, client = connected_sender()
    validator = frame_validator()
    for k in range(5):
        t: float = k / MOCAP_HZ
        sender.handle_frame(t, {0: pose(100.0 * k, -50.0 * k, yaw_rad=0.1 * k), 3: pose(-2999.6, 2999.4, 0.0)})
    assert len(client.emitted) == 5
    for event, frame, namespace in client.emitted:
        assert (event, namespace) == ("frame", INGEST_NAMESPACE)
        validator.validate(frame)
        assert frame["schemaVersion"] == 1
        assert frame["timestamp"] == EPOCH_S * 1000.0
        assert {rb["ID"] for rb in frame["rigidbodies"]} == {0, 3}
    assert all(isinstance(rb["x"], int) for _, frame, _ in client.emitted for rb in frame["rigidbodies"])


def test_empty_frame_validates() -> None:
    sender, client = connected_sender()
    sender.handle_frame(0.0, {})
    frame_validator().validate(client.emitted[0][1])


@pytest.mark.parametrize("timestamp_jitter_s", [0.0, 0.001])
def test_constant_motion_gives_constant_velocity_with_jitter_and_lower_send_rate(timestamp_jitter_s: float) -> None:
    rng = np.random.default_rng(3)
    send_hz = 100.0
    vx, vy = 1200.0, -500.0  # mm/s
    wall: list[float] = [EPOCH_S]
    client = RecordingClient()
    sender = PayloadSender(client, MetricsTracker(), "http://127.0.0.1:1", send_hz, threading.Event(),
                           wall_clock=lambda: wall[0])
    client.connect("http://127.0.0.1:1", ["websocket"], [INGEST_NAMESPACE], True, 1.0)
    n_frames = int(3 * MOCAP_HZ)
    for k in range(n_frames):
        # Arrival alternates 2 ms / 6 ms (wall clock); mocap timestamps optionally jitter too, poses follow them.
        wall[0] += 0.002 if k % 2 else 0.006
        t: float = k / MOCAP_HZ + float(rng.uniform(-timestamp_jitter_s, timestamp_jitter_s))
        sender.handle_frame(t, {1: pose(vx * t, vy * t)})
    speeds = [frame["rigidbodies"][0]["abs_vel"] for _, frame, _ in client.emitted[1:]]
    assert speeds == pytest.approx([math.hypot(vx, vy)] * len(speeds), rel=1e-9)
    assert len(client.emitted) == pytest.approx(3 * send_hz, abs=2)


def test_send_gate_resyncs_when_mocap_clock_restarts() -> None:
    sender, client = connected_sender(framerate=100.0)
    sender.handle_frame(500.0, {})
    sender.handle_frame(0.0, {})  # Motive restarted
    sender.handle_frame(0.001, {})  # within one send period of the restart
    assert len(client.emitted) == 2


def test_metrics_update_on_frames_the_gate_skips() -> None:
    sender, client = connected_sender(framerate=1.0)
    for k in range(int(MOCAP_HZ) + 1):
        t = k / MOCAP_HZ
        sender.handle_frame(t, {0: pose(500.0 * t * t, 0.0)})  # accelerating: speed 1000 * t mm/s
    # Frames at t = 0 and t = 1 s are sent. The second differentiates against the previous mocap frame
    # (about 1000 mm/s), not against the last sent frame (500 mm/s average).
    assert len(client.emitted) == 2
    assert client.emitted[1][1]["rigidbodies"][0]["abs_vel"] == pytest.approx(1000.0, rel=0.01)


def test_frames_while_disconnected_are_dropped_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingClient()
    sender = make_sender(client)
    with caplog.at_level(logging.WARNING):
        for k in range(5):
            assert sender.handle_frame(k / MOCAP_HZ, {0: pose(0.0, 0.0)}) is None
    assert client.emitted == []
    assert sum("Dropping frames" in r.message for r in caplog.records) == 1


def test_emit_failure_logged_once_per_disconnect_episode(caplog: pytest.LogCaptureFixture) -> None:
    sender, client = connected_sender()
    client.emit_error = BadNamespaceError("/ingest is not a connected namespace.")
    with caplog.at_level(logging.WARNING):
        for k in range(5):
            assert sender.handle_frame(k / MOCAP_HZ, {}) is None
        client.drop()
        client.emit_error = None
        client.connect("http://127.0.0.1:1", ["websocket"], [INGEST_NAMESPACE], True, 1.0)
        client.emit_error = BadNamespaceError("/ingest is not a connected namespace.")
        for k in range(5, 10):
            sender.handle_frame(k / MOCAP_HZ, {})
    assert sum("emit failed" in r.message for r in caplog.records) == 2


def test_reconnect_loop_logs_unreachable_once_then_connects_and_stops(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingClient(connect_failures=4)
    sender = make_sender(client, reconnect_interval_s=0.01)
    with caplog.at_level(logging.INFO):
        sender.connect()
        wait_for(sender.is_connected)
        assert client.connect_calls == 5
        client.drop()
        wait_for(sender.is_connected)
        sender.stop()
    assert client.connect_calls == 6
    assert not client.connected
    assert sum("unreachable" in r.message for r in caplog.records) == 1


def test_connect_twice_raises() -> None:
    sender = make_sender(RecordingClient(), reconnect_interval_s=0.01)
    sender.connect()
    with pytest.raises(RuntimeError):
        sender.connect()
    sender.stop()


def test_invalid_framerate_rejected() -> None:
    with pytest.raises(ValueError):
        make_sender(RecordingClient(), framerate=0.0)


class UnusableUrlClient(RecordingClient):
    def connect(
        self, url: str, transports: list[str], namespaces: list[str], wait: bool, wait_timeout: float
    ) -> None:
        self.connect_calls += 1
        raise ValueError(f"Invalid URL {url!r}")


class HangingClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.release: threading.Event = threading.Event()

    def connect(
        self, url: str, transports: list[str], namespaces: list[str], wait: bool, wait_timeout: float
    ) -> None:
        self.connect_calls += 1
        self.release.wait()  # a blackholed address


def test_unexpected_reconnect_error_sets_failure_without_retrying(caplog: pytest.LogCaptureFixture) -> None:
    client = UnusableUrlClient()
    failure = threading.Event()
    sender = make_sender(client, failure=failure, reconnect_interval_s=0.01)
    with caplog.at_level(logging.ERROR):
        sender.connect()
        assert failure.wait(2.0)
        time.sleep(0.05)
        sender.stop()
    assert client.connect_calls == 1
    assert any("Reconnect loop" in r.message and r.exc_info for r in caplog.records)


def test_stop_does_not_block_on_a_hanging_connect(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cobeart.packagesender.sender.STOP_JOIN_TIMEOUT_S", 0.1)
    client = HangingClient()
    sender = make_sender(client, reconnect_interval_s=0.01)
    sender.connect()
    wait_for(lambda: client.connect_calls == 1)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        sender.stop()
    assert time.monotonic() - started < 1.0
    assert any("still blocked" in r.message for r in caplog.records)
    client.release.set()


def test_non_finite_timestamp_is_dropped_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    sender, client = connected_sender()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert sender.handle_frame(float("nan"), {0: pose(0.0, 0.0)}) is None
        assert sender.handle_frame(0.0, {0: pose(0.0, 0.0)}) is not None  # gate state was not poisoned
        assert sender.handle_frame(1 / MOCAP_HZ, {0: pose(1.0, 0.0)}) is not None
    assert len(client.emitted) == 2
    assert sum("corrupt" in r.message for r in caplog.records) == 1


@pytest.mark.filterwarnings("ignore:overflow encountered:RuntimeWarning")
@pytest.mark.parametrize("bad_x", [float("nan"), float("inf"), 1e308])
def test_non_finite_body_values_are_dropped_not_emitted(bad_x: float, caplog: pytest.LogCaptureFixture) -> None:
    # 1e308 is finite, but jumping between -1e308 and 1e308 overflows the velocity to inf on every frame.
    sender, client = connected_sender()
    sender.handle_frame(0.0, {0: pose(-1e308, 0.0)})
    with caplog.at_level(logging.WARNING):
        for k in range(1, 4):
            assert sender.handle_frame(k / MOCAP_HZ, {0: pose(bad_x if k % 2 else -bad_x, 0.0)}) is None
    assert len(client.emitted) == 1
    assert sum("corrupt" in r.message for r in caplog.records) == 1


def test_nan_pose_does_not_poison_later_velocities() -> None:
    sender, client = connected_sender()
    sender.handle_frame(0.0, {0: pose(0.0, 0.0)})
    assert sender.handle_frame(1 / MOCAP_HZ, {0: pose(float("nan"), 0.0)}) is None
    frame = sender.handle_frame(2 / MOCAP_HZ, {0: pose(2000.0 / MOCAP_HZ, 0.0)})
    assert frame is not None
    assert frame["rigidbodies"][0]["abs_vel"] == pytest.approx(1000.0)


def test_strict_json_refuses_nan() -> None:
    assert StrictJson.loads(StrictJson.dumps({"a": 1.5}, separators=(",", ":"))) == {"a": 1.5}
    with pytest.raises(ValueError):
        StrictJson.dumps({"a": float("nan")})

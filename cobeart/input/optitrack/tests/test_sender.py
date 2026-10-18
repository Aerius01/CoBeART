import logging
import math
import threading
import time
from collections.abc import Callable

import pytest
from socketio.exceptions import BadNamespaceError

from cobeart.common.strict_json import StrictJson
from cobeart.input.optitrack.transform import ArenaPose
from cobeart.input.optitrack.sender import INGEST_NAMESPACE, PayloadSender
from cobeart.input.optitrack.tests.contract import RecordingClient
from tests.contract.schemas import contract_validator

MOCAP_HZ: float = 240.0


def make_sender(
    client: RecordingClient, framerate: float = MOCAP_HZ, failure: threading.Event | None = None, **kwargs: float
) -> PayloadSender:
    return PayloadSender(client, "http://127.0.0.1:1", framerate,
                         failure=failure if failure is not None else threading.Event(), **kwargs)


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


def test_emitted_pose_frames_validate_against_contract() -> None:
    sender, client = connected_sender()
    validator = contract_validator("pose_frame")
    for k in range(5):
        t: float = k / MOCAP_HZ
        sender.handle_frame(
            t, {"right_hand": pose(100.0 * k, -50.0 * k, yaw_rad=0.1 * k), "chest_front": pose(-2999.6, 2999.4, 0.0)}
        )
    assert len(client.emitted) == 5
    for k, (event, frame, namespace) in enumerate(client.emitted):
        assert (event, namespace) == ("pose_frame", INGEST_NAMESPACE)
        validator.validate(frame)
        assert frame["schemaVersion"] == 2
        assert frame["source_time_s"] == k / MOCAP_HZ
        assert set(frame["bodies"]) == {"right_hand", "chest_front"}
    assert all(isinstance(body[axis], int)
               for _, frame, _ in client.emitted for body in frame["bodies"].values() for axis in "xyz")


def test_empty_pose_frame_is_sent_and_validates() -> None:
    sender, client = connected_sender()
    sender.handle_frame(0.0, {})
    assert client.emitted[0][1]["bodies"] == {}
    contract_validator("pose_frame").validate(client.emitted[0][1])


def test_pose_frames_carry_no_derived_fields() -> None:
    sender, client = connected_sender()
    sender.handle_frame(0.0, {"head": pose(1.0, 2.0)})
    assert set(client.emitted[0][1]["bodies"]["head"]) == {"x", "y", "z", "qx", "qy", "qz", "qw"}


def test_send_rate_gate_limits_emissions_on_the_mocap_clock() -> None:
    send_hz: float = 100.0
    sender, client = connected_sender(framerate=send_hz)
    for k in range(int(3 * MOCAP_HZ)):
        sender.handle_frame(k / MOCAP_HZ, {"left_hand": pose(10.0 * k, 0.0)})
    assert len(client.emitted) == pytest.approx(3 * send_hz, abs=2)
    times: list[float] = [frame["source_time_s"] for _, frame, _ in client.emitted]
    assert times == sorted(times)


def test_send_gate_resyncs_when_mocap_clock_restarts() -> None:
    sender, client = connected_sender(framerate=100.0)
    sender.handle_frame(500.0, {})
    sender.handle_frame(0.0, {})  # Motive restarted
    sender.handle_frame(0.001, {})  # within one send period of the restart
    assert len(client.emitted) == 2


def test_frames_while_disconnected_are_dropped_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingClient()
    sender = make_sender(client)
    with caplog.at_level(logging.WARNING):
        for k in range(5):
            assert sender.handle_frame(k / MOCAP_HZ, {"left_hand": pose(0.0, 0.0)}) is None
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
    monkeypatch.setattr("cobeart.input.optitrack.sender.STOP_JOIN_TIMEOUT_S", 0.1)
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


@pytest.mark.parametrize("bad_time_s", [float("nan"), float("inf"), -0.5])
def test_invalid_timestamp_is_dropped_and_logged_once(bad_time_s: float, caplog: pytest.LogCaptureFixture) -> None:
    sender, client = connected_sender()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert sender.handle_frame(bad_time_s, {"left_hand": pose(0.0, 0.0)}) is None
        assert sender.handle_frame(0.0, {"left_hand": pose(0.0, 0.0)}) is not None  # gate state was not poisoned
        assert sender.handle_frame(1 / MOCAP_HZ, {"left_hand": pose(1.0, 0.0)}) is not None
    assert len(client.emitted) == 2
    assert sum("corrupt" in r.message for r in caplog.records) == 1


@pytest.mark.parametrize("bad_x", [float("nan"), float("inf")])
def test_non_finite_body_values_are_dropped_not_emitted(bad_x: float, caplog: pytest.LogCaptureFixture) -> None:
    sender, client = connected_sender()
    sender.handle_frame(0.0, {"left_hand": pose(0.0, 0.0)})
    with caplog.at_level(logging.WARNING):
        for k in range(1, 4):
            assert sender.handle_frame(k / MOCAP_HZ, {"left_hand": pose(bad_x, 0.0)}) is None
    assert len(client.emitted) == 1
    assert sum("corrupt" in r.message for r in caplog.records) == 1


def test_strict_json_refuses_nan() -> None:
    assert StrictJson.loads(StrictJson.dumps({"a": 1.5}, separators=(",", ":"))) == {"a": 1.5}
    with pytest.raises(ValueError):
        StrictJson.dumps({"a": float("nan")})

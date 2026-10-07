import logging
import math
import threading
import time

import pytest

from cobeart.optitrackclient.start_client import (
    CirclePath,
    FramePipeline,
    SyntheticNatNetSource,
    arena_pose_on_circle,
    arena_to_optitrack,
    parse_args,
    run_simulated,
)
from cobeart.optitrackclient.transform import pose_to_arena
from cobeart.packagesender.sender import INGEST_NAMESPACE, PayloadSender
from cobeart.packagesender.tests.contract import RecordingClient, frame_validator, make_tracker
from cobeart.settings.config import load_settings

SETTINGS = load_settings({})
PATH = CirclePath(body_id=2, radius_mm=1500.0, period_s=6.0, height_mm=1200.0, phase_rad=0.3)


def build_pipeline(
    send_hz: float = 240.0, failure: threading.Event | None = None
) -> tuple[SyntheticNatNetSource, RecordingClient]:
    failure = failure if failure is not None else threading.Event()
    client = RecordingClient()
    sender = PayloadSender(client, make_tracker(), "http://127.0.0.1:1", send_hz, failure)
    client.connect("http://127.0.0.1:1", ["websocket"], [INGEST_NAMESPACE], True, 1.0)
    source = SyntheticNatNetSource([PATH], rate_hz=240.0)
    FramePipeline(sender, max_num_objects=10, failure=failure).attach(source)
    return source, client


@pytest.mark.parametrize("url", ["not a url", "localhost:3000", "http://[::1", "ftp://host", "http://", "http://h:x"])
def test_bad_hub_url_rejected_at_parse(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--simulate", "--url", url], SETTINGS)
    assert exc.value.code == 2
    assert repr(url) in capsys.readouterr().err


@pytest.mark.parametrize("url", ["http://127.0.0.1:3202", "https://hub.local", "http://[::1]:3000"])
def test_good_hub_url_accepted(url: str) -> None:
    assert parse_args(["--simulate", "--url", url], SETTINGS).url == url


def test_callback_exception_sets_failure_instead_of_killing_the_thread(caplog: pytest.LogCaptureFixture) -> None:
    failure = threading.Event()
    source, client = build_pipeline(failure=failure)
    client.emit_error = ValueError("Out of range float values are not JSON compliant")
    with caplog.at_level(logging.ERROR):
        source.step()
        source.step()  # later frames are ignored once failed, not re-raised
    assert failure.is_set()
    assert sum("Frame callback failed" in r.message and r.exc_info is not None for r in caplog.records) == 1


def test_run_simulated_exits_non_zero_when_the_pipeline_fails() -> None:
    failure = threading.Event()
    source, client = build_pipeline(failure=failure)
    client.emit_error = ValueError("Out of range float values are not JSON compliant")
    started = time.monotonic()
    with pytest.raises(SystemExit) as exc:
        run_simulated(source, failure)
    assert exc.value.code not in (0, None)
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("t", [0.0, 0.7, 2.9, 4.4])
def test_optitrack_native_pose_round_trips_through_the_real_transform(t: float) -> None:
    expected = arena_pose_on_circle(PATH, t)
    position, rotation = arena_to_optitrack(expected)
    assert position[1] == pytest.approx(PATH.height_mm / 1000.0)  # OptiTrack is Y-up, metres
    actual = pose_to_arena(position, rotation)
    assert actual.position == pytest.approx(expected.position)
    assert actual.orientation == pytest.approx(expected.orientation)


def test_synthetic_body_faces_its_direction_of_travel() -> None:
    pose = arena_pose_on_circle(PATH, 1.0)
    qx, qy, qz, qw = pose.orientation
    # forward = q applied to (0, 1, 0)
    forward = (2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw))
    theta = PATH.phase_rad + 2 * math.pi / PATH.period_s
    assert forward == pytest.approx((-math.sin(theta), math.cos(theta), 0.0))


def test_known_speed_survives_transform_metrics_and_sender() -> None:
    source, client = build_pipeline(send_hz=60.0)
    for _ in range(480):  # 2 s of mocap
        source.step()
    validator = frame_validator()
    frames = [frame for _, frame, _ in client.emitted]
    assert len(frames) == pytest.approx(120, abs=1)
    for frame in frames:
        validator.validate(frame)
    speeds = [frame["rigidbodies"][0]["abs_vel"] for frame in frames[1:]]
    assert speeds == pytest.approx([PATH.speed_mm_s] * len(speeds), rel=1e-3)
    # Constant turn rate about +z (counterclockwise): wz = 360 / period deg/s.
    assert frames[-1]["rigidbodies"][0]["wz"] == pytest.approx(360.0 / PATH.period_s, rel=1e-3)
    assert frames[-1]["rigidbodies"][0]["z"] == int(PATH.height_mm)


def test_run_emits_frames_on_a_thread_until_shutdown() -> None:
    source, client = build_pipeline()
    source.run()
    deadline = time.monotonic() + 2.0
    while len(client.emitted) < 24:
        assert time.monotonic() < deadline, "synthetic source produced no frames"
        time.sleep(0.01)
    source.shutdown()
    count = len(client.emitted)
    time.sleep(0.05)
    assert len(client.emitted) == count


def test_step_without_listeners_raises() -> None:
    with pytest.raises(RuntimeError):
        SyntheticNatNetSource([PATH], rate_hz=240.0).step()

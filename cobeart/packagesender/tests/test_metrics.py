import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from cobeart.packagesender.metrics import BodyMetrics, MetricsTracker


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now: float = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def quat(rotation: Rotation) -> tuple[float, float, float, float]:
    qx, qy, qz, qw = rotation.as_quat()
    return (float(qx), float(qy), float(qz), float(qw))


def test_first_sighting_returns_zeros(clock: FakeClock) -> None:
    q = quat(Rotation.from_euler("x", 30, degrees=True))
    m: BodyMetrics = MetricsTracker(clock=clock).update(1, 5, 6, 7, *q)
    assert np.array_equal(m.velocity, np.zeros(3))
    assert np.array_equal(m.angular_velocity, np.zeros(3))
    assert m.abs_velocity == 0.0
    assert m.norm_abs_velocity == 0.0
    assert np.array_equal(m.position, [5, 6, 7])
    assert np.array_equal(m.orientation, q)  # (qx, qy, qz, qw)


def test_velocity_math_with_fake_clock(clock: FakeClock) -> None:
    tracker = MetricsTracker(max_vel=1000.0, max_gap_s=10.0, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
    clock.advance(0.5)
    m = tracker.update(1, 300, 400, 50, *quat(Rotation.from_rotvec([0, 0, 10], degrees=True)))
    assert np.array_equal(m.velocity, [600.0, 800.0, 100.0])
    assert np.allclose(m.angular_velocity, [0.0, 0.0, 20.0])
    assert m.abs_velocity == 1000.0
    assert m.norm_abs_velocity == 1.0


def test_zero_time_diff_gives_zero_velocity(clock: FakeClock) -> None:
    tracker = MetricsTracker(clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
    m = tracker.update(1, 10, 10, 10, *quat(Rotation.from_euler("z", 45, degrees=True)))
    assert np.array_equal(m.velocity, np.zeros(3))
    assert np.array_equal(m.angular_velocity, np.zeros(3))
    assert m.abs_velocity == 0.0


@pytest.mark.parametrize("flip_sign", [False, True])
def test_constant_rotation_about_z_across_heading_wrap(clock: FakeClock, flip_sign: bool) -> None:
    rate = 90.0  # deg/s
    dt = 0.25
    tracker = MetricsTracker(clock=clock)
    for step in range(20):  # 0 to 427.5 degrees, crossing +-180 and 360
        q = quat(Rotation.from_euler("z", rate * dt * step, degrees=True))
        if flip_sign and step % 2:
            q = (-q[0], -q[1], -q[2], -q[3])  # same orientation, opposite sign
        m = tracker.update(1, 0, 0, 0, *q)
        if step > 0:
            assert np.allclose(m.angular_velocity, [0.0, 0.0, rate]), f"step {step}"
        clock.advance(dt)


def test_angular_velocity_is_in_world_frame(clock: FakeClock) -> None:
    # A body leaned 40 degrees about x, then turning about world z, reports pure wz.
    lean = Rotation.from_euler("x", 40, degrees=True)
    tracker = MetricsTracker(clock=clock)
    tracker.update(1, 0, 0, 0, *quat(lean))
    clock.advance(0.1)
    m = tracker.update(1, 0, 0, 0, *quat(Rotation.from_euler("z", -6, degrees=True) * lean))
    assert np.allclose(m.angular_velocity, [0.0, 0.0, -60.0])


def test_angular_velocity_matches_scipy_rotvec(clock: FakeClock) -> None:
    rng = np.random.default_rng(7)
    tracker = MetricsTracker(clock=clock)
    prev = Rotation.random(random_state=rng)
    tracker.update(1, 0, 0, 0, *quat(prev))
    for _ in range(50):
        delta = Rotation.from_rotvec(rng.normal(scale=0.3, size=3))
        now = delta * prev
        clock.advance(0.02)
        m = tracker.update(1, 0, 0, 0, *quat(now))
        assert np.allclose(m.angular_velocity, np.degrees(delta.as_rotvec()) / 0.02)
        prev = now


def test_norm_velocity_zeroes_only_after_full_window(clock: FakeClock) -> None:
    window = 4
    tracker = MetricsTracker(max_vel=1000.0, window_length=window, max_gap_s=10.0, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
    # A single movement sample is held as the window max, then decays to zero once pushed out.
    clock.advance(1.0)
    moving = tracker.update(1, 500, 0, 0, 0, 0, 0, 1)
    assert moving.norm_abs_velocity == 0.5
    held: list[float] = []
    for _ in range(window):
        clock.advance(1.0)
        held.append(tracker.update(1, 500, 0, 0, 0, 0, 0, 1).norm_abs_velocity)
    assert held == [0.5] * (window - 1) + [0.0]
    # Still zero after a further full window of zeros.
    clock.advance(1.0)
    assert tracker.update(1, 500, 0, 0, 0, 0, 0, 1).norm_abs_velocity == 0.0


def test_new_still_body_never_reports_speed(clock: FakeClock) -> None:
    tracker = MetricsTracker(window_length=4, clock=clock)
    for _ in range(10):
        m = tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
        assert m.norm_abs_velocity == 0.0
        clock.advance(0.01)


def test_gap_longer_than_max_gap_resets_to_first_sighting(clock: FakeClock) -> None:
    tracker = MetricsTracker(max_vel=1000.0, max_gap_s=0.25, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
    clock.advance(0.1)
    assert tracker.update(1, 100, 0, 0, 0, 0, 0, 1).abs_velocity == pytest.approx(1000.0)
    clock.advance(0.3)
    m = tracker.update(1, 5000, 0, 0, *quat(Rotation.from_euler("z", 90, degrees=True)))
    assert np.array_equal(m.velocity, np.zeros(3))
    assert np.array_equal(m.angular_velocity, np.zeros(3))
    assert m.norm_abs_velocity == 0.0


def test_gap_below_max_gap_computes_velocity(clock: FakeClock) -> None:
    tracker = MetricsTracker(max_vel=1000.0, max_gap_s=0.25, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
    clock.advance(0.2)
    m = tracker.update(1, 100, 0, 0, *quat(Rotation.from_euler("z", 18, degrees=True)))
    assert np.allclose(m.velocity, [500.0, 0.0, 0.0])
    assert np.allclose(m.angular_velocity, [0.0, 0.0, 90.0])


def test_interleaved_bodies_do_not_share_smoothing(clock: FakeClock) -> None:
    window = 3
    tracker = MetricsTracker(max_vel=1000.0, window_length=window, max_gap_s=10.0, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0, 1)
    tracker.update(2, 0, 0, 0, 0, 0, 0, 1)
    last_fast: BodyMetrics | None = None
    last_still: BodyMetrics | None = None
    for i in range(1, 2 * window + 1):
        clock.advance(1.0)
        last_fast = tracker.update(1, 1000 * i, 0, 0, 0, 0, 0, 1)  # 1000 mm/s
        last_still = tracker.update(2, 0, 0, 0, 0, 0, 0, 1)
    # Shared history would let body 1's motion keep body 2 above zero.
    assert last_fast is not None and last_still is not None
    assert last_fast.norm_abs_velocity == 1.0
    assert last_still.norm_abs_velocity == 0.0

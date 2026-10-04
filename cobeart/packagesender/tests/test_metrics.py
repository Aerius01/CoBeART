import numpy as np
import pytest

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


def test_first_sighting_returns_zeros(clock: FakeClock) -> None:
    m: BodyMetrics = MetricsTracker(clock=clock).update(1, 5, 6, 7, 1, 2, 3)
    assert np.array_equal(m.velocity, np.zeros(3))
    assert np.array_equal(m.angular_velocity, np.zeros(3))
    assert m.abs_velocity == 0.0
    assert m.norm_abs_velocity == 0.0
    assert np.array_equal(m.position, [5, 6, 7])
    assert np.array_equal(m.orientation, [1, 3, 2])  # (roll, pitch, yaw)


def test_velocity_math_with_fake_clock(clock: FakeClock) -> None:
    tracker = MetricsTracker(max_vel=1000.0, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0)
    clock.advance(0.5)
    m = tracker.update(1, 300, 400, 50, 10, 20, 30)
    assert np.array_equal(m.velocity, [600.0, 800.0, 100.0])
    assert np.array_equal(m.angular_velocity, [20.0, 60.0, 40.0])  # roll, pitch, yaw
    assert m.abs_velocity == 1000.0
    assert m.norm_abs_velocity == 1.0


def test_zero_time_diff_gives_zero_velocity(clock: FakeClock) -> None:
    tracker = MetricsTracker(clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0)
    m = tracker.update(1, 10, 10, 10, 1, 1, 1)
    assert np.array_equal(m.velocity, np.zeros(3))
    assert m.abs_velocity == 0.0


def test_norm_velocity_zeroes_only_after_full_window(clock: FakeClock) -> None:
    window = 4
    tracker = MetricsTracker(max_vel=1000.0, window_length=window, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0)
    # Window starts full of ones, so stationary frames hold 1.0 until all ones are pushed out.
    results: list[float] = []
    for _ in range(window + 1):
        clock.advance(1.0)
        results.append(tracker.update(1, 0, 0, 0, 0, 0, 0).norm_abs_velocity)
    assert results[: window - 1] == [1.0] * (window - 1)
    assert results[window - 1] == 0.0
    assert results[window] == 0.0
    # A single movement sample is held as the window max, then decays to zero.
    clock.advance(1.0)
    moving = tracker.update(1, 500, 0, 0, 0, 0, 0)
    assert moving.norm_abs_velocity == 0.5
    held: list[float] = []
    for _ in range(window):
        clock.advance(1.0)
        held.append(tracker.update(1, 500, 0, 0, 0, 0, 0).norm_abs_velocity)
    assert held == [0.5] * (window - 1) + [0.0]


def test_interleaved_bodies_do_not_share_smoothing(clock: FakeClock) -> None:
    window = 3
    tracker = MetricsTracker(max_vel=1000.0, window_length=window, clock=clock)
    tracker.update(1, 0, 0, 0, 0, 0, 0)
    tracker.update(2, 0, 0, 0, 0, 0, 0)
    last_fast: BodyMetrics | None = None
    last_still: BodyMetrics | None = None
    for i in range(1, 2 * window + 1):
        clock.advance(1.0)
        last_fast = tracker.update(1, 1000 * i, 0, 0, 0, 0, 0)  # 1000 mm/s
        last_still = tracker.update(2, 0, 0, 0, 0, 0, 0)
    # Shared history would let body 1's motion keep body 2 above zero.
    assert last_fast is not None and last_still is not None
    assert last_fast.norm_abs_velocity == 1.0
    assert last_still.norm_abs_velocity == 0.0

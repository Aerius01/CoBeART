"""Per-body velocity metrics derived from successive OptiTrack poses."""
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.float64]

DEFAULT_MAX_GAP_S: float = 0.25
DEFAULT_MIN_DT_S: float = 0.0005


def _zeros() -> Vector:
    return np.zeros(3)


@dataclass(frozen=True, slots=True, eq=False)
class BodyMetrics:
    """Immutable metrics snapshot for one body at one frame."""
    id: int
    position: Vector
    orientation: Vector  # unit quaternion (qx, qy, qz, qw), arena axes
    velocity: Vector  # [mm/s]
    angular_velocity: Vector  # (wx, wy, wz) [degrees/s], arena axes
    abs_velocity: float  # [mm/s], xy plane
    norm_abs_velocity: float  # [0..1]


@dataclass(slots=True)
class BodyState:
    """Mutable tracker-internal state for one body."""
    position: Vector
    orientation: Vector
    timestamp: float
    norm_vel_history: deque[float]
    last_metrics: BodyMetrics = field(init=False)


def calc_abs_velocity(vx: float, vy: float) -> float:
    """Absolute velocity from x and y components."""
    return math.hypot(vx, vy)


def normalize_abs_velocity(abs_vel: float, max_vel: float) -> float:
    """Normalize absolute velocity to 0-1 against a maximum expected velocity."""
    return min(abs_vel / max_vel, 1.0)


def calc_angular_velocity(q_prev: Vector, q_now: Vector, time_diff: float) -> Vector:
    """World-frame angular velocity (deg/s) as the rotation vector of q_now * q_prev^-1 over time_diff."""
    ax, ay, az, aw = (float(c) for c in q_now)
    # Conjugate of q_prev is its inverse for a unit quaternion.
    bx, by, bz, bw = (-float(q_prev[0]), -float(q_prev[1]), -float(q_prev[2]), float(q_prev[3]))
    dw: float = aw * bw - ax * bx - ay * by - az * bz
    dx: float = aw * bx + ax * bw + ay * bz - az * by
    dy: float = aw * by - ax * bz + ay * bw + az * bx
    dz: float = aw * bz + ax * by - ay * bx + az * bw
    if dw < 0.0:
        # q and -q are the same rotation; take the short way round.
        dw, dx, dy, dz = -dw, -dx, -dy, -dz
    sin_half: float = math.sqrt(dx * dx + dy * dy + dz * dz)
    if sin_half < 1e-12:
        return _zeros()
    angle: float = 2.0 * math.atan2(sin_half, dw)
    scale: float = math.degrees(angle) / (sin_half * time_diff)
    return np.array([dx * scale, dy * scale, dz * scale])


class MetricsTracker:
    """Computes velocities per body, each with its own smoothing window.

    Timestamps are supplied by the caller (the mocap frame clock), so dt is capture time, not arrival time.
    Not thread-safe: call from one thread, or serialize calls.
    """

    def __init__(
        self,
        max_vel: float,
        window_length: int,
        max_gap_s: float = DEFAULT_MAX_GAP_S,
        min_dt_s: float = DEFAULT_MIN_DT_S,
    ) -> None:
        self._max_vel: float = max_vel
        self._window_length: int = window_length
        self._max_gap_s: float = max_gap_s
        self._min_dt_s: float = min_dt_s
        self._bodies: dict[int, BodyState] = {}

    def update(
        self, id: int, timestamp: float, x: float, y: float, z: float, qx: float, qy: float, qz: float, qw: float
    ) -> BodyMetrics:
        """Record an arena pose (mm, unit quaternion) at `timestamp` (s) and return the body's current metrics."""
        position: Vector = np.array([x, y, z])
        orientation: Vector = np.array([qx, qy, qz, qw])

        state: BodyState | None = self._bodies.get(id)
        time_diff: float = timestamp - state.timestamp if state is not None else 0.0
        if state is None or time_diff < 0.0 or time_diff > self._max_gap_s:
            # First sighting, return after a dropout, or the clock went backwards (Motive restart or take loop):
            # start fresh. The window starts full of zeros so a newly appearing still body does not report speed.
            state = BodyState(
                position=position,
                orientation=orientation,
                timestamp=timestamp,
                norm_vel_history=deque([0.0] * self._window_length, maxlen=self._window_length),
            )
            state.last_metrics = BodyMetrics(
                id=id,
                position=position,
                orientation=orientation,
                velocity=_zeros(),
                angular_velocity=_zeros(),
                abs_velocity=0.0,
                norm_abs_velocity=0.0,
            )
            self._bodies[id] = state
            return state.last_metrics

        if time_diff < self._min_dt_s:
            # Too close to the reference pose to divide by: report the new pose with the previous velocities and
            # keep the reference, so the next frame differentiates over the full interval.
            prev: BodyMetrics = state.last_metrics
            return BodyMetrics(
                id=id,
                position=position,
                orientation=orientation,
                velocity=prev.velocity,
                angular_velocity=prev.angular_velocity,
                abs_velocity=prev.abs_velocity,
                norm_abs_velocity=prev.norm_abs_velocity,
            )

        velocity: Vector = (position - state.position) / time_diff
        angular_velocity: Vector = calc_angular_velocity(state.orientation, orientation, time_diff)
        abs_velocity: float = calc_abs_velocity(velocity[0], velocity[1])
        state.norm_vel_history.append(normalize_abs_velocity(abs_velocity, self._max_vel))
        # Zero only after a full window of zeros, otherwise hold the window maximum.
        norm_abs_velocity: float = (
            0.0 if all(v == 0 for v in state.norm_vel_history) else max(state.norm_vel_history)
        )

        state.position = position
        state.orientation = orientation
        state.timestamp = timestamp
        state.last_metrics = BodyMetrics(
            id=id,
            position=position,
            orientation=orientation,
            velocity=velocity,
            angular_velocity=angular_velocity,
            abs_velocity=abs_velocity,
            norm_abs_velocity=norm_abs_velocity,
        )
        return state.last_metrics

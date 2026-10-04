"""Per-body velocity metrics derived from successive OptiTrack poses."""
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

logger: logging.Logger = logging.getLogger(__name__)

Vector = npt.NDArray[np.float64]

DEFAULT_MAX_VEL: float = 13000.0
DEFAULT_WINDOW_LENGTH: int = 15


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
    return float(np.linalg.norm([vx, vy]))


def normalize_abs_velocity(abs_vel: float, max_vel: float = DEFAULT_MAX_VEL) -> float:
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
    """Computes velocities per body, each with its own smoothing window."""

    def __init__(
        self,
        max_vel: float = DEFAULT_MAX_VEL,
        window_length: int = DEFAULT_WINDOW_LENGTH,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._max_vel: float = max_vel
        self._window_length: int = window_length
        self._clock: Callable[[], float] = clock
        self._bodies: dict[int, BodyState] = {}

    def update(
        self, id: int, x: float, y: float, z: float, qx: float, qy: float, qz: float, qw: float
    ) -> BodyMetrics:
        """Record an arena pose (mm, unit quaternion) for a body and return its current metrics."""
        now: float = self._clock()
        position: Vector = np.array([x, y, z])
        orientation: Vector = np.array([qx, qy, qz, qw])

        state: BodyState | None = self._bodies.get(id)
        if state is None:
            # Window starts full of ones so norm velocity is not zeroed before real data arrives.
            state = BodyState(
                position=position,
                orientation=orientation,
                timestamp=now,
                norm_vel_history=deque([1.0] * self._window_length, maxlen=self._window_length),
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

        time_diff: float = now - state.timestamp
        if time_diff > 0:
            velocity: Vector = (position - state.position) / time_diff
            angular_velocity: Vector = calc_angular_velocity(state.orientation, orientation, time_diff)
        else:
            velocity = _zeros()
            angular_velocity = _zeros()

        abs_velocity: float = calc_abs_velocity(velocity[0], velocity[1])
        state.norm_vel_history.append(normalize_abs_velocity(abs_velocity, self._max_vel))
        # Zero only after a full window of zeros, otherwise hold the window maximum.
        norm_abs_velocity: float = (
            0.0 if all(v == 0 for v in state.norm_vel_history) else max(state.norm_vel_history)
        )

        state.position = position
        state.orientation = orientation
        state.timestamp = now
        state.last_metrics = BodyMetrics(
            id=id,
            position=position,
            orientation=orientation,
            velocity=velocity,
            angular_velocity=angular_velocity,
            abs_velocity=abs_velocity,
            norm_abs_velocity=norm_abs_velocity,
        )
        logger.debug("metrics for ID %s: %s", id, state.last_metrics)
        return state.last_metrics

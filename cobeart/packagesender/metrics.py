"""Per-body velocity metrics derived from successive OptiTrack poses."""
import logging
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
    orientation: Vector  # (roll, pitch, yaw) degrees
    velocity: Vector  # [mm/s]
    angular_velocity: Vector  # [degrees/s]
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
        self, id: int, x: float, y: float, z: float, roll: float, yaw: float, pitch: float
    ) -> BodyMetrics:
        """Record a pose for a body and return its current metrics."""
        now: float = self._clock()
        position: Vector = np.array([x, y, z])
        orientation: Vector = np.array([roll, pitch, yaw])

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
            angular_velocity: Vector = (orientation - state.orientation) / time_diff
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

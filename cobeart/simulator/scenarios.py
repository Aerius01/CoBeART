"""Pure motion scenarios `(t, rng) -> PoseFrame`, produced directly in contract space (integer mm, quaternions).

Orientation follows contract D5: arena axes, identity faces +y with up = +z, forward lean = asin(-forward.z).
A body orientation is Rz(yaw) * Rx(-lean). Bodies are keyed by role and carry pose only: the hub derives velocities.
`source_time_s` is the scenario time `t`.
"""
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

import numpy as np
import numpy.typing as npt
from scipy.spatial.transform import Rotation

from cobeart.settings.config import Range, Role, Settings
from cobeart.simulator.models import PoseBody, PoseFrame

Vector = npt.NDArray[np.float64]
Scenario = Callable[[float, np.random.Generator], PoseFrame]
PhaseClock = Callable[[float], float]

LEFT_HAND: Role = "left_hand"
RIGHT_HAND: Role = "right_hand"
HEAD: Role = "head"
CLAP_PERIOD_S: float = 4.0
CLAP_MIN_SEPARATION_MM: float = 100.0
CLAP_MAX_SEPARATION_MM: float = 1200.0
MISSING_CYCLE_S: float = 6.0
MISSING_DURATION_S: float = 2.0
STILL_STOP_S: float = 3.0
EDGE_MARGIN_XY_MM: float = 300.0
EDGE_MARGIN_Z_MM: float = 200.0


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionConfig:
    """Arena and role parameters; build with `from_settings` so they come from config/cobeart.yaml.

    `roles` are the roles of the simulated bodies, in order: body `i` of the orbit, still and edge scenarios is
    `roles[i]`. The hands and missing-body scenarios always use left_hand, right_hand and head.
    """
    arena_x: Range
    arena_y: Range
    arena_z: Range
    roles: tuple[Role, ...]

    @classmethod
    def from_settings(cls, settings: Settings, num_bodies: int) -> "MotionConfig":
        """Config with the arena from `settings` and the first `num_bodies` of `tracking.roles` (by Motive ID)."""
        configured: tuple[Role, ...] = tuple(role for _, role in sorted(settings.tracking.roles.items()))
        if not 1 <= num_bodies <= len(configured):
            raise ValueError(
                f"num_bodies must be in 1..{len(configured)} (the roles in tracking.roles), got {num_bodies}"
            )
        missing: set[Role] = {LEFT_HAND, RIGHT_HAND, HEAD} - set(configured)
        if missing:
            raise ValueError(f"tracking.roles must map {sorted(missing)} for the hands scenarios")
        return cls(
            arena_x=settings.arena.x,
            arena_y=settings.arena.y,
            arena_z=settings.arena.z,
            roles=configured[:num_bodies],
        )


@dataclass(frozen=True, slots=True)
class BodyPath:
    """Analytic trajectory: position over a time array, and (yaw, lean) in radians at a scalar time."""
    role: Role
    position: Callable[[Vector], Vector]  # (n,) seconds -> (n, 3) mm
    orientation: Callable[[float], tuple[float, float]]


def _rotation(yaw: float, lean: float) -> Rotation:
    return Rotation.from_euler("ZX", [yaw, -lean])


def _body_state(path: BodyPath, t: float) -> PoseBody:
    """Contract pose for one path at time t."""
    quat: Vector = _rotation(*path.orientation(t)).as_quat()  # scalar last
    if quat[3] < 0:
        quat = -quat
    position: Vector = path.position(np.array([t]))[0]
    return PoseBody(
        x=round(position[0]), y=round(position[1]), z=round(position[2]),
        qx=float(quat[0]), qy=float(quat[1]), qz=float(quat[2]), qw=float(quat[3]),
    )


def _frame(paths: Sequence[BodyPath], t: float) -> PoseFrame:
    return PoseFrame(source_time_s=t, bodies={path.role: _body_state(path, t) for path in paths})


def _nod_lean(t: float) -> float:
    """Forward lean in radians oscillating at 0.5 Hz around a slight forward tilt."""
    return math.radians(10.0 + 20.0 * math.sin(2 * math.pi * 0.5 * t))


def _orbit_paths(cfg: MotionConfig, clock: PhaseClock = float) -> list[BodyPath]:
    """N bodies on concentric circles; the head body (if present) nods. `clock` maps t to phase time."""
    def make(i: int) -> BodyPath:
        radius: float = 900.0 + 250.0 * i
        omega: float = 0.8 / (1.0 + 0.25 * i) * (1 if i % 2 == 0 else -1)
        offset: float = 2 * math.pi * i / len(cfg.roles)
        height: float = 900.0 + 150.0 * i

        def position(t: Vector) -> Vector:
            phi: Vector = omega * np.array([clock(s) for s in t]) + offset
            return np.stack([radius * np.cos(phi), radius * np.sin(phi), np.full_like(phi, height)], axis=1)

        def orientation(t: float) -> tuple[float, float]:
            # Heading follows the direction of travel: forward = (-sin yaw, cos yaw).
            phase: float = clock(t)
            yaw: float = omega * phase + offset + (0.0 if omega > 0 else math.pi)
            return yaw, _nod_lean(phase) if cfg.roles[i] == HEAD else 0.0

        return BodyPath(role=cfg.roles[i], position=position, orientation=orientation)

    return [make(i) for i in range(len(cfg.roles))]


def clap_separation(t: float) -> float:
    """Distance in mm between the hands at time t; minimal at multiples of CLAP_PERIOD_S."""
    half: float = (1.0 - math.cos(2 * math.pi * t / CLAP_PERIOD_S)) / 2.0
    return CLAP_MIN_SEPARATION_MM + (CLAP_MAX_SEPARATION_MM - CLAP_MIN_SEPARATION_MM) * half


def clap_times(duration_s: float) -> list[float]:
    """Times in [0, duration] at which the hands are closest."""
    return [k * CLAP_PERIOD_S for k in range(int(duration_s // CLAP_PERIOD_S) + 1)]


def _hand_paths() -> list[BodyPath]:
    def hand(role: Role, side: float) -> BodyPath:
        def position(t: Vector) -> Vector:
            sep: Vector = CLAP_MIN_SEPARATION_MM + (CLAP_MAX_SEPARATION_MM - CLAP_MIN_SEPARATION_MM) * (
                (1.0 - np.cos(2 * np.pi * t / CLAP_PERIOD_S)) / 2.0
            )
            height: Vector = 1300.0 + 150.0 * np.sin(2 * np.pi * t / (CLAP_PERIOD_S * 2))
            return np.stack([side * sep / 2.0, np.full_like(t, 400.0), height], axis=1)

        def orientation(t: float) -> tuple[float, float]:
            return math.radians(-side * 20.0) * math.sin(2 * math.pi * t / CLAP_PERIOD_S), 0.0

        return BodyPath(role=role, position=position, orientation=orientation)

    head: BodyPath = BodyPath(
        role=HEAD,
        position=lambda t: np.stack([np.zeros_like(t), np.full_like(t, -300.0), np.full_like(t, 1700.0)], axis=1),
        orientation=lambda t: (0.0, _nod_lean(t)),
    )
    return [hand(LEFT_HAND, -1.0), hand(RIGHT_HAND, 1.0), head]


def orbit(t: float, rng: np.random.Generator, cfg: MotionConfig) -> PoseFrame:
    """N bodies circling the arena, the head body nodding."""
    return _frame(_orbit_paths(cfg), t)


def hands(t: float, rng: np.random.Generator, cfg: MotionConfig) -> PoseFrame:
    """Left and right hands with a periodic clap within 200 mm, plus a nodding head."""
    return _frame(_hand_paths(), t)


def still(t: float, rng: np.random.Generator, cfg: MotionConfig) -> PoseFrame:
    """Orbiting bodies that come to rest at STILL_STOP_S, so norm velocity decays to zero."""
    return _frame(_orbit_paths(cfg, clock=lambda s: min(s, STILL_STOP_S)), t)


def missing_body(t: float, rng: np.random.Generator, cfg: MotionConfig) -> PoseFrame:
    """Hands scenario where one hand (alternating) drops out for MISSING_DURATION_S every MISSING_CYCLE_S."""
    cycle: int = int(t // MISSING_CYCLE_S)
    in_gap: bool = (t % MISSING_CYCLE_S) >= MISSING_CYCLE_S - MISSING_DURATION_S
    dropped: Role = LEFT_HAND if cycle % 2 == 0 else RIGHT_HAND
    paths: list[BodyPath] = [p for p in _hand_paths() if not (in_gap and p.role == dropped)]
    return _frame(paths, t)


def edge(t: float, rng: np.random.Generator, cfg: MotionConfig) -> PoseFrame:
    """Bodies sweep to the arena bounds and slightly past them (300 mm in x/y, 200 mm in z)."""
    x_amp: float = (cfg.arena_x[1] - cfg.arena_x[0]) / 2 + EDGE_MARGIN_XY_MM
    y_amp: float = (cfg.arena_y[1] - cfg.arena_y[0]) / 2 + EDGE_MARGIN_XY_MM
    z_amp: float = (cfg.arena_z[1] - cfg.arena_z[0]) / 2 + EDGE_MARGIN_Z_MM
    x_mid: float = (cfg.arena_x[1] + cfg.arena_x[0]) / 2
    y_mid: float = (cfg.arena_y[1] + cfg.arena_y[0]) / 2
    z_mid: float = (cfg.arena_z[1] + cfg.arena_z[0]) / 2

    def make(i: int) -> BodyPath:
        fx, fy, fz = 0.15 + 0.03 * i, 0.11 + 0.02 * i, 0.07 + 0.01 * i
        phase: float = i * math.pi / 3

        def position(s: Vector) -> Vector:
            return np.stack([
                x_mid + x_amp * np.sin(2 * np.pi * fx * s + phase),
                y_mid + y_amp * np.sin(2 * np.pi * fy * s + 2 * phase),
                z_mid + z_amp * np.sin(2 * np.pi * fz * s + 3 * phase),
            ], axis=1)

        return BodyPath(role=cfg.roles[i], position=position, orientation=lambda s: (0.5 * s + phase, 0.0))

    return _frame([make(i) for i in range(len(cfg.roles))], t)


MOTION_SCENARIOS: dict[str, Callable[[float, np.random.Generator, MotionConfig], PoseFrame]] = {
    "orbit": orbit,
    "hands": hands,
    "still": still,
    "missing-body": missing_body,
    "edge": edge,
}


def build_motion_scenario(name: str, cfg: MotionConfig) -> Scenario:
    """Bind a named scenario to its config, yielding the `(t, rng) -> PoseFrame` callable."""
    if name not in MOTION_SCENARIOS:
        raise ValueError(f"Unknown motion scenario {name!r}, choose from {sorted(MOTION_SCENARIOS)}")
    return partial(MOTION_SCENARIOS[name], cfg=cfg)

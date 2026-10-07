"""Pure motion scenarios `(t, rng) -> Frame`, produced directly in contract space (mm, quaternions, deg/s).

Orientation follows contract D5: arena axes, identity faces +y with up = +z, forward lean = asin(-forward.z).
A body orientation is Rz(yaw) * Rx(-lean). Velocities are finite differences over one frame period, computed
here independently of cobeart.packagesender.metrics so they can serve as a reference.
"""
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy.spatial.transform import Rotation

from cobeart.settings.config import Range, Settings
from cobeart.simulator.models import Frame, RigidBody

Vector = npt.NDArray[np.float64]
Scenario = Callable[[float, np.random.Generator], Frame]
PhaseClock = Callable[[float], float]

BODY_MAP_PATH: Path = Path(__file__).resolve().parents[2] / "cobeart-app" / "public" / "body_map.json"
CLAP_PERIOD_S: float = 4.0
CLAP_MIN_SEPARATION_MM: float = 100.0
CLAP_MAX_SEPARATION_MM: float = 1200.0
MISSING_CYCLE_S: float = 6.0
MISSING_DURATION_S: float = 2.0
STILL_STOP_S: float = 3.0
EDGE_MARGIN_XY_MM: float = 300.0
EDGE_MARGIN_Z_MM: float = 200.0


def load_body_map(path: Path = BODY_MAP_PATH) -> dict[str, int]:
    """Name to rigid-body ID mapping shared with the frontend."""
    body_map: dict[str, int] = json.loads(path.read_text())
    return body_map


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionConfig:
    """Arena, rate and body-ID parameters; build with `from_settings` so they come from config/cobeart.yaml."""
    rate_hz: float
    num_bodies: int
    arena_x: Range
    arena_y: Range
    arena_z: Range
    max_num_objects: int
    max_vel: float
    history_window: int
    start_epoch_ms: float
    left_hand_id: int
    right_hand_id: int
    head_id: int

    @property
    def dt(self) -> float:
        return 1.0 / self.rate_hz

    @classmethod
    def from_settings(
        cls, settings: Settings, body_map: dict[str, int], rate_hz: float, num_bodies: int, start_epoch_ms: float
    ) -> "MotionConfig":
        """Config with the arena, tracking and metrics values from `settings` and the IDs from body_map.json."""
        return cls(
            rate_hz=rate_hz,
            num_bodies=num_bodies,
            arena_x=settings.arena.x,
            arena_y=settings.arena.y,
            arena_z=settings.arena.z,
            max_num_objects=settings.tracking.max_num_objects,
            max_vel=settings.metrics.max_vel,
            history_window=settings.metrics.history_window,
            start_epoch_ms=start_epoch_ms,
            left_hand_id=body_map["left_hand"],
            right_hand_id=body_map["right_hand"],
            head_id=body_map["head"],
        )


@dataclass(frozen=True, slots=True)
class BodyPath:
    """Analytic trajectory: position over a time array, and (yaw, lean) in radians at a scalar time."""
    id: int
    position: Callable[[Vector], Vector]  # (n,) seconds -> (n, 3) mm
    orientation: Callable[[float], tuple[float, float]]


def _rotation(yaw: float, lean: float) -> Rotation:
    return Rotation.from_euler("ZX", [yaw, -lean])


def _body_state(path: BodyPath, t: float, cfg: MotionConfig) -> RigidBody:
    """Contract rigid body for one path at time t."""
    dt: float = cfg.dt
    times: Vector = t - dt * np.arange(cfg.history_window + 1)
    positions: Vector = path.position(times)
    velocities: Vector = (positions[:-1] - positions[1:]) / dt  # newest first
    horizontal: Vector = np.hypot(velocities[:, 0], velocities[:, 1])
    norm: float = float(min(horizontal.max() / cfg.max_vel, 1.0))

    rot_now: Rotation = _rotation(*path.orientation(t))
    rot_prev: Rotation = _rotation(*path.orientation(t - dt))
    omega: Vector = np.degrees((rot_now * rot_prev.inv()).as_rotvec()) / dt
    quat: Vector = rot_now.as_quat()  # scalar last
    if quat[3] < 0:
        quat = -quat
    position: Vector = positions[0]
    velocity: Vector = velocities[0]
    return RigidBody(
        ID=path.id,
        x=round(position[0]), y=round(position[1]), z=round(position[2]),
        qx=float(quat[0]), qy=float(quat[1]), qz=float(quat[2]), qw=float(quat[3]),
        vx=float(velocity[0]), vy=float(velocity[1]), vz=float(velocity[2]),
        wx=float(omega[0]), wy=float(omega[1]), wz=float(omega[2]),
        abs_vel=float(horizontal[0]),
        norm_abs_vel=norm,
    )


def _frame(paths: Sequence[BodyPath], t: float, cfg: MotionConfig) -> Frame:
    return Frame(
        timestamp=cfg.start_epoch_ms + t * 1000.0,
        rigidbodies=tuple(_body_state(path, t, cfg) for path in paths),
    )


def _nod_lean(t: float) -> float:
    """Forward lean in radians oscillating at 0.5 Hz around a slight forward tilt."""
    return math.radians(10.0 + 20.0 * math.sin(2 * math.pi * 0.5 * t))


def _orbit_paths(cfg: MotionConfig, clock: PhaseClock = float) -> list[BodyPath]:
    """N bodies on concentric circles; the head body (if present) nods. `clock` maps t to phase time."""
    if not 1 <= cfg.num_bodies <= cfg.max_num_objects:
        raise ValueError(f"num_bodies must be in 1..{cfg.max_num_objects}, got {cfg.num_bodies}")

    def make(i: int) -> BodyPath:
        radius: float = 900.0 + 250.0 * i
        omega: float = 0.8 / (1.0 + 0.25 * i) * (1 if i % 2 == 0 else -1)
        offset: float = 2 * math.pi * i / cfg.num_bodies
        height: float = 900.0 + 150.0 * i

        def position(t: Vector) -> Vector:
            phi: Vector = omega * np.array([clock(s) for s in t]) + offset
            return np.stack([radius * np.cos(phi), radius * np.sin(phi), np.full_like(phi, height)], axis=1)

        def orientation(t: float) -> tuple[float, float]:
            # Heading follows the direction of travel: forward = (-sin yaw, cos yaw).
            phase: float = clock(t)
            yaw: float = omega * phase + offset + (0.0 if omega > 0 else math.pi)
            return yaw, _nod_lean(phase) if i == cfg.head_id else 0.0

        return BodyPath(id=i, position=position, orientation=orientation)

    return [make(i) for i in range(cfg.num_bodies)]


def clap_separation(t: float) -> float:
    """Distance in mm between the hands at time t; minimal at multiples of CLAP_PERIOD_S."""
    half: float = (1.0 - math.cos(2 * math.pi * t / CLAP_PERIOD_S)) / 2.0
    return CLAP_MIN_SEPARATION_MM + (CLAP_MAX_SEPARATION_MM - CLAP_MIN_SEPARATION_MM) * half


def clap_times(duration_s: float) -> list[float]:
    """Times in [0, duration] at which the hands are closest."""
    return [k * CLAP_PERIOD_S for k in range(int(duration_s // CLAP_PERIOD_S) + 1)]


def _hand_paths(cfg: MotionConfig) -> list[BodyPath]:
    def hand(hand_id: int, side: float) -> BodyPath:
        def position(t: Vector) -> Vector:
            sep: Vector = CLAP_MIN_SEPARATION_MM + (CLAP_MAX_SEPARATION_MM - CLAP_MIN_SEPARATION_MM) * (
                (1.0 - np.cos(2 * np.pi * t / CLAP_PERIOD_S)) / 2.0
            )
            height: Vector = 1300.0 + 150.0 * np.sin(2 * np.pi * t / (CLAP_PERIOD_S * 2))
            return np.stack([side * sep / 2.0, np.full_like(t, 400.0), height], axis=1)

        def orientation(t: float) -> tuple[float, float]:
            return math.radians(-side * 20.0) * math.sin(2 * math.pi * t / CLAP_PERIOD_S), 0.0

        return BodyPath(id=hand_id, position=position, orientation=orientation)

    head: BodyPath = BodyPath(
        id=cfg.head_id,
        position=lambda t: np.stack([np.zeros_like(t), np.full_like(t, -300.0), np.full_like(t, 1700.0)], axis=1),
        orientation=lambda t: (0.0, _nod_lean(t)),
    )
    return [hand(cfg.left_hand_id, -1.0), hand(cfg.right_hand_id, 1.0), head]


def orbit(t: float, rng: np.random.Generator, cfg: MotionConfig) -> Frame:
    """N bodies circling the arena, the head body nodding."""
    return _frame(_orbit_paths(cfg), t, cfg)


def hands(t: float, rng: np.random.Generator, cfg: MotionConfig) -> Frame:
    """Left and right hands with a periodic clap within 200 mm, plus a nodding head."""
    return _frame(_hand_paths(cfg), t, cfg)


def still(t: float, rng: np.random.Generator, cfg: MotionConfig) -> Frame:
    """Orbiting bodies that come to rest at STILL_STOP_S, so norm velocity decays to zero."""
    return _frame(_orbit_paths(cfg, clock=lambda s: min(s, STILL_STOP_S)), t, cfg)


def missing_body(t: float, rng: np.random.Generator, cfg: MotionConfig) -> Frame:
    """Hands scenario where one hand (alternating) drops out for MISSING_DURATION_S every MISSING_CYCLE_S."""
    cycle: int = int(t // MISSING_CYCLE_S)
    in_gap: bool = (t % MISSING_CYCLE_S) >= MISSING_CYCLE_S - MISSING_DURATION_S
    dropped: int = cfg.left_hand_id if cycle % 2 == 0 else cfg.right_hand_id
    paths: list[BodyPath] = [p for p in _hand_paths(cfg) if not (in_gap and p.id == dropped)]
    return _frame(paths, t, cfg)


def shuffled(t: float, rng: np.random.Generator, cfg: MotionConfig) -> Frame:
    """Orbit scenario with the rigid bodies in random order each frame."""
    paths: list[BodyPath] = _orbit_paths(cfg)
    order: list[int] = [int(i) for i in rng.permutation(len(paths))]
    return _frame([paths[i] for i in order], t, cfg)


def edge(t: float, rng: np.random.Generator, cfg: MotionConfig) -> Frame:
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

        return BodyPath(id=i, position=position, orientation=lambda s: (0.5 * s + phase, 0.0))

    return _frame([make(i) for i in range(cfg.num_bodies)], t, cfg)


MOTION_SCENARIOS: dict[str, Callable[[float, np.random.Generator, MotionConfig], Frame]] = {
    "orbit": orbit,
    "hands": hands,
    "still": still,
    "missing-body": missing_body,
    "shuffled": shuffled,
    "edge": edge,
}


def build_motion_scenario(name: str, cfg: MotionConfig) -> Scenario:
    """Bind a named scenario to its config, yielding the `(t, rng) -> Frame` callable."""
    if name not in MOTION_SCENARIOS:
        raise ValueError(f"Unknown motion scenario {name!r}, choose from {sorted(MOTION_SCENARIOS)}")
    return partial(MOTION_SCENARIOS[name], cfg=cfg)

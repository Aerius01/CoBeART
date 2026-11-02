"""Decline rule of the Glade grass (GP roadmap, Phase 2, G10 to G14): pure functions, stdlib only.

A plant has a vitality v (1 healthy, 0 gone), independent of its growth. Each leaf has a health h (1 healthy,
0 dead) from its turnover senescence and from v, and one look maps h onto the leaf's healthy shape. The same
rule runs in grass.lpy's preview, in Phase 3's Blender generator and, mirrored in GLSL, in Phase 5's shader.
The field names of DeclineParams are the extern names of grass.lpy, so an export's `params` builds it.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields

MAX_DEATH_OFFSET = 0.95  # caps a leaf's death point below v = 1, so h_decline stays defined


@dataclass(frozen=True)
class DeclineParams:
    V_DEAD: float               # vitality at which every leaf is dead; the plant fades below it (no revival)
    DECLINE_AGE_SPREAD: float   # how much earlier (in v) the oldest leaves die than the youngest
    DECLINE_AGE_REF: float      # leaf age (P) at which the age spread is full
    DECLINE_RESIDUAL: float     # size of a dead leaf relative to its healthy size
    SHRIVEL_FRACTION: float     # final fraction of health over which a leaf shrivels to the residual size
    ANGLE_SENESCENT_DEG: float  # extra angle from the tiller axis at health 0
    SHRIVEL_ANGLE_DEG: float    # further extra angle reached at the end of the shrivel
    SHRIVEL_ARCH_RAD: float     # extra arch reached at the end of the shrivel
    DECLINE_SPLAY_DEG: float    # outward lean a tiller reaches at V_DEAD

    def __post_init__(self) -> None:
        for f in fields(self):
            _finite(f.name, getattr(self, f.name))
        if not 0.0 < self.V_DEAD < 1.0:
            raise ValueError(f"V_DEAD must be in (0, 1), got {self.V_DEAD}")
        if self.DECLINE_AGE_REF <= 0.0:
            raise ValueError(f"DECLINE_AGE_REF must be positive, got {self.DECLINE_AGE_REF}")
        if not 0.0 <= self.DECLINE_RESIDUAL <= 1.0:
            raise ValueError(f"DECLINE_RESIDUAL must be in [0, 1], got {self.DECLINE_RESIDUAL}")
        if not 0.0 < self.SHRIVEL_FRACTION <= 1.0:
            raise ValueError(f"SHRIVEL_FRACTION must be in (0, 1], got {self.SHRIVEL_FRACTION}")

    @classmethod
    def from_mapping(cls, names: Mapping[str, object]) -> DeclineParams:
        """Build from a mapping of extern names (grass.lpy globals or an export's `params`)."""
        missing = [f.name for f in fields(cls) if f.name not in names]
        if missing:
            raise ValueError(f"decline parameters missing: {missing}; declare them as externs in the model")
        values: dict[str, float] = {}
        for f in fields(cls):
            value = names[f.name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"decline parameter {f.name} must be a number, got {value!r}")
            values[f.name] = float(value)
        return cls(**values)


@dataclass(frozen=True)
class Look:
    size_scale: float       # factor on the healthy sheath length, blade length and width
    extra_angle_rad: float  # added to the healthy angle from the tiller axis
    extra_arch_rad: float   # added to the healthy arch
    browning: float         # 0 green, 1 brown


def _finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _unit(name: str, value: float) -> float:
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {value}")
    return value


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def _smoothstep(x: float) -> float:
    x = _clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def death_point(age: float, decline_draw: float, p: DeclineParams) -> float:
    """Vitality at which a leaf of this age dies; older leaves die at higher v, all by V_DEAD."""
    if not (math.isfinite(age) and age >= 0.0):
        raise ValueError(f"age must be a finite number >= 0, got {age}")
    _finite("decline_draw", decline_draw)
    offset = _clamp(p.DECLINE_AGE_SPREAD * min(age / p.DECLINE_AGE_REF, 1.0) + decline_draw, 0.0, MAX_DEATH_OFFSET)
    return p.V_DEAD + (1.0 - p.V_DEAD) * offset


def decline_health(v: float, age: float, decline_draw: float, p: DeclineParams) -> float:
    """A leaf's health from plant vitality alone: 1 at v = 1, 0 at and below its death point."""
    _unit("v", v)
    v_k = death_point(age, decline_draw, p)
    return _clamp((v - v_k) / (1.0 - v_k), 0.0, 1.0)


def health(senescence: float, v: float, age: float, decline_draw: float, p: DeclineParams) -> float:
    """A leaf's health: the worse of its turnover senescence and the plant's decline."""
    _unit("senescence", senescence)
    return min(1.0 - senescence, decline_health(v, age, decline_draw, p))


def look(h: float, p: DeclineParams) -> Look:
    """Deformation of a leaf's healthy shape at health h; identity at h = 1."""
    _unit("h", h)
    shrivel = _smoothstep(1.0 - h / p.SHRIVEL_FRACTION)
    return Look(
        size_scale=1.0 + (p.DECLINE_RESIDUAL - 1.0) * shrivel,
        extra_angle_rad=math.radians(p.ANGLE_SENESCENT_DEG * (1.0 - h) + p.SHRIVEL_ANGLE_DEG * shrivel),
        extra_arch_rad=p.SHRIVEL_ARCH_RAD * shrivel,
        browning=1.0 - h,
    )


def plant_fade(v: float, p: DeclineParams) -> float:
    """Whole-plant fade: 0 down to V_DEAD, then 1 at v = 0."""
    _unit("v", v)
    return _clamp((p.V_DEAD - v) / p.V_DEAD, 0.0, 1.0)


def tiller_lean_rad(v: float, p: DeclineParams) -> float:
    """Extra outward splay of a tiller (not the main shoot), full at V_DEAD."""
    _unit("v", v)
    return math.radians(p.DECLINE_SPLAY_DEG) * _smoothstep((1.0 - v) / (1.0 - p.V_DEAD))


def opacity(leaf_fade: float, v: float, p: DeclineParams) -> float:
    """Drawn opacity of a leaf: its own turnover fade times the plant fade."""
    _unit("leaf_fade", leaf_fade)
    return (1.0 - leaf_fade) * (1.0 - plant_fade(v, p))

"""Decline rule of the Glade grass (GP roadmap, Phase 2, G10 to G14; per vertex, Phase 3, G23 and G32): pure
functions, stdlib only.

A plant has a vitality v (1 healthy, 0 gone), independent of its growth. Each leaf has a health h (1 healthy,
0 dead) from its turnover senescence and from v, and one look maps h onto the leaf's healthy shape. The same
rule runs in grass.lpy's preview, in Phase 3's Blender generator and, mirrored in GLSL, in Phase 5's shader.
declined_leaf is the whole composition for one leaf, so consumers call it rather than rebuilding it.
declined_vertex is the same rule applied to one vertex of the healthy mesh, the Python reference of the shader:
exact for the collar, sizes, angle and tiller lean, and a closed-form approximation of the extra arch.
The field names of DeclineParams are the extern names of grass.lpy, so an export's `params` builds it.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields

MAX_DEATH_OFFSET = 0.95  # caps a leaf's death point below v = 1, so h_decline stays defined
EPS = 1e-9

Vec3 = tuple[float, float, float]
UP: Vec3 = (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class DeclineParams:
    V_DEAD: float               # vitality at which every leaf is dead; the plant fades below it (no revival)
    DECLINE_AGE_SPREAD: float   # how much earlier (in v) the oldest leaves die than the youngest
    DECLINE_AGE_REF: float      # leaf age (P) at which the age spread is full
    DECLINE_JITTER: float       # scale of a leaf's unit decline_draw on its death point
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
        if self.DECLINE_AGE_SPREAD < 0.0:
            raise ValueError(f"DECLINE_AGE_SPREAD must be >= 0, got {self.DECLINE_AGE_SPREAD}")
        if self.DECLINE_AGE_REF <= 0.0:
            raise ValueError(f"DECLINE_AGE_REF must be positive, got {self.DECLINE_AGE_REF}")
        if self.DECLINE_JITTER < 0.0:
            raise ValueError(f"DECLINE_JITTER must be >= 0, got {self.DECLINE_JITTER}")
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


@dataclass(frozen=True)
class DeclinedLeaf:
    attach: Vec3            # collar: tiller base plus the (leaned) tiller axis times the drawn sheath length
    direction: Vec3         # unit initial blade direction in world space
    tiller_axis: Vec3       # unit tiller axis after the lean
    sheath_length_m: float
    blade_length_m: float
    blade_width_m: float
    arch_rad: float
    browning: float         # 0 green, 1 brown; colours sheath and blade alike
    opacity: float          # 1 opaque, 0 gone


KIND_SHEATH, KIND_BLADE = 0, 1
TIME_TOLERANCE = 1e-6  # sample times and birth times are rounded, so a leaf born at the sample time may be "early"
UNIT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class VertexInputs:
    """Decline inputs of one healthy-mesh vertex: the G29 decline attributes, one to one, plus the healthy arch."""
    kind: int               # KIND_SHEATH or KIND_BLADE
    s: float                # 0..1 along the organ, base to tip
    collar: Vec3            # the leaf's healthy collar (its attach)
    side: Vec3              # unit axis of the leaf's angle and arch, through the collar (healthy frame)
    tiller_base: Vec3
    tiller_axis: Vec3       # unit healthy tiller axis
    tiller_azimuth: float   # the tiller's azimuth_rad; it leans about (-sin az, cos az, 0)
    tiller: int             # 0 is the main shoot, which never leans
    birth_time: float
    decline_draw: float
    senescence: float
    fade: float
    arch_rad: float         # the leaf's healthy arch at the built sample (unused by sheath vertices)

    def __post_init__(self) -> None:
        for name in ("kind", "tiller"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"vertex {name} must be an int, got {value!r}")
        if self.kind not in (KIND_SHEATH, KIND_BLADE):
            raise ValueError(f"vertex kind must be {KIND_SHEATH} (sheath) or {KIND_BLADE} (blade), got {self.kind}")
        if self.tiller < 0:
            raise ValueError(f"vertex tiller must be >= 0, got {self.tiller}")
        for name in ("collar", "side", "tiller_base", "tiller_axis"):
            object.__setattr__(self, name, _vec3("vertex", {name: getattr(self, name)}, name))
        for name in ("side", "tiller_axis"):
            n = math.sqrt(sum(c * c for c in getattr(self, name)))
            if abs(n - 1.0) > UNIT_TOLERANCE:
                raise ValueError(f"vertex {name} must be a unit vector, got {getattr(self, name)} (length {n})")
        for name in ("tiller_azimuth", "birth_time", "arch_rad"):
            _number("vertex", {name: getattr(self, name)}, name)
        for name in ("s", "senescence", "fade"):
            _unit(name, getattr(self, name))
        if not (math.isfinite(self.decline_draw) and -1.0 <= self.decline_draw <= 1.0):
            raise ValueError(f"decline_draw must be in [-1, 1], got {self.decline_draw}")


@dataclass(frozen=True)
class DeclinedVertex:
    position: Vec3
    browning: float  # 0 green, 1 brown
    opacity: float   # 1 opaque, 0 gone


# Vector helpers (world space, Z up).

def unit(v: Sequence[float]) -> Vec3:
    """v normalised; +Z for a zero vector."""
    n = math.sqrt(sum(c * c for c in v))
    if n <= EPS:
        return UP
    return (v[0] / n, v[1] / n, v[2] / n)


def cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def rotate(v: Sequence[float], axis: Sequence[float], angle: float) -> Vec3:
    """Rodrigues rotation of v about a unit axis."""
    c, s = math.cos(angle), math.sin(angle)
    k = cross(axis, v)
    dot = sum(axis[i] * v[i] for i in range(3))
    return (v[0] * c + k[0] * s + axis[0] * dot * (1 - c),
            v[1] * c + k[1] * s + axis[1] * dot * (1 - c),
            v[2] * c + k[2] * s + axis[2] * dot * (1 - c))


def dir_from(azimuth: float, from_vertical: float) -> Vec3:
    """Unit vector at this azimuth, tilted from +Z by from_vertical."""
    s = math.sin(from_vertical)
    return (math.cos(azimuth) * s, math.sin(azimuth) * s, math.cos(from_vertical))


def _add(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scaled(a: Sequence[float], f: float) -> Vec3:
    return (a[0] * f, a[1] * f, a[2] * f)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def to_tiller_frame(v: Sequence[float], tiller_axis: Sequence[float]) -> Vec3:
    """Apply the minimal rotation taking +Z onto the tiller axis (identity for a vertical axis)."""
    k = cross(UP, tiller_axis)
    s = math.sqrt(sum(c * c for c in k))
    if s < EPS:
        return (v[0], v[1], v[2])
    return rotate(v, (k[0] / s, k[1] / s, k[2] / s), math.atan2(s, tiller_axis[2]))


# Validation helpers.

def _finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _unit(name: str, value: float) -> float:
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {value}")
    return value


def _number(fields_of: str, mapping: Mapping[str, object], name: str) -> float:
    if name not in mapping:
        raise ValueError(f"{fields_of} field {name!r} is missing")
    value = mapping[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{fields_of} field {name!r} must be a finite number, got {value!r}")
    return float(value)


def _vec3(fields_of: str, mapping: Mapping[str, object], name: str) -> Vec3:
    value = mapping.get(name)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{fields_of} field {name!r} must be 3 numbers, got {value!r}")
    x, y, z = (_number(fields_of, {name: c}, name) for c in value)
    return (x, y, z)


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def _smoothstep(x: float) -> float:
    x = _clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


# The rule.

def death_point(age: float, decline_draw: float, p: DeclineParams) -> float:
    """Vitality at which a leaf of this age dies; older leaves die at higher v, all by V_DEAD."""
    if not (math.isfinite(age) and age >= 0.0):
        raise ValueError(f"age must be a finite number >= 0, got {age}")
    if not (math.isfinite(decline_draw) and -1.0 <= decline_draw <= 1.0):
        raise ValueError(f"decline_draw must be in [-1, 1], got {decline_draw}")
    offset = _clamp(p.DECLINE_AGE_SPREAD * min(age / p.DECLINE_AGE_REF, 1.0) + p.DECLINE_JITTER * decline_draw,
                    0.0, MAX_DEATH_OFFSET)
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


def declined_leaf(leaf: Mapping[str, object], tiller: Mapping[str, object], v: float,
                  p: DeclineParams) -> DeclinedLeaf:
    """A leaf as drawn at vitality v, from its organ-table fields (plus `age`) and its tiller's fields.

    Health (G11) deforms the healthy sizes, angle and arch. A tiller other than the main shoot (leaf["tiller"]
    == 0) leans by tiller_lean_rad (G14): a rigid rotation of the tiller and its leaves about the horizontal
    axis (-sin az, cos az, 0) through the tiller base, az being the tiller's azimuth, which is the same as
    growing its splay. The leaf's azimuth and angle stay measured in the leaned tiller's frame.
    """
    tiller_id = leaf.get("tiller")
    if isinstance(tiller_id, bool) or not isinstance(tiller_id, int):
        raise ValueError(f"leaf field 'tiller' must be an int, got {tiller_id!r}")
    h = health(_number("leaf", leaf, "senescence"), v, _number("leaf", leaf, "age"),
               _number("leaf", leaf, "decline_draw"), p)
    lk = look(h, p)
    base = _vec3("tiller", tiller, "base")
    if tiller_id == 0:
        axis = unit(_vec3("tiller", tiller, "direction"))
    else:
        axis = dir_from(_number("tiller", tiller, "azimuth_rad"),
                        _number("tiller", tiller, "splay_rad") + tiller_lean_rad(v, p))
    sheath = _number("leaf", leaf, "sheath_length_m") * lk.size_scale
    angle = _number("leaf", leaf, "angle_from_axis_rad") + lk.extra_angle_rad
    return DeclinedLeaf(
        attach=(base[0] + axis[0] * sheath, base[1] + axis[1] * sheath, base[2] + axis[2] * sheath),
        direction=unit(to_tiller_frame(dir_from(_number("leaf", leaf, "azimuth_rad"), angle), axis)),
        tiller_axis=axis,
        sheath_length_m=sheath,
        blade_length_m=_number("leaf", leaf, "blade_length_m") * lk.size_scale,
        blade_width_m=_number("leaf", leaf, "blade_width_m") * lk.size_scale,
        arch_rad=_number("leaf", leaf, "arch_rad") + lk.extra_arch_rad,
        browning=lk.browning,
        opacity=opacity(_number("leaf", leaf, "fade"), v, p),
    )


def chord_factor(arch: float, s: float) -> float:
    """Chord length from the collar to fraction s of a blade bent by arch * u^2, relative to the arc length.

    Second order in the bend: 1 - 2 arch^2 s^4 / 45 (the variance of the bend angle over [0, s] is
    4 arch^2 s^4 / 45). Accurate to well under 1% for arch up to about 2 rad.
    """
    f = 1.0 - 2.0 * arch * arch * s ** 4 / 45.0
    if f <= 0.0:
        raise ValueError(f"arch {arch} at s = {s} is beyond the chord approximation (factor {f})")
    return f


def declined_vertex(position: Sequence[float], vin: VertexInputs, time: float, v: float,
                    p: DeclineParams) -> DeclinedVertex:
    """One healthy-mesh vertex as drawn at vitality v and growth time `time` (G32, G23).

    A sheath vertex scales its component along the tiller axis, from the tiller base, by the size scale. A blade
    vertex moves with the scaled collar, and its offset from the collar is scaled by the size scale and by the
    chord shortening of the extra arch, then rotated about `side` through the collar by the extra angle plus
    extra_arch * s^2 / 3 (the mean extra bend up to s). A tiller other than the main shoot then leans rigidly
    about its base, as in declined_leaf.
    """
    pos = _vec3("vertex", {"position": position}, "position")
    if not math.isfinite(time) or time < vin.birth_time - TIME_TOLERANCE:
        raise ValueError(f"time must be finite and >= the leaf's birth_time {vin.birth_time}, got {time}")
    age = max(time - vin.birth_time, 0.0)
    lk = look(health(vin.senescence, v, age, vin.decline_draw, p), p)
    base, axis = vin.tiller_base, vin.tiller_axis
    rel = _sub(pos, base)
    if vin.kind == KIND_SHEATH:
        drawn = _add(pos, _scaled(axis, _dot(rel, axis) * (lk.size_scale - 1.0)))
    else:
        collar = _add(base, _scaled(_sub(vin.collar, base), lk.size_scale))
        k = lk.size_scale * chord_factor(vin.arch_rad + lk.extra_arch_rad, vin.s) / chord_factor(vin.arch_rad, vin.s)
        offset = _scaled(_sub(pos, vin.collar), k)
        drawn = _add(collar, rotate(offset, vin.side, lk.extra_angle_rad + lk.extra_arch_rad * vin.s ** 2 / 3.0))
    if vin.tiller != 0:
        lean_axis = (-math.sin(vin.tiller_azimuth), math.cos(vin.tiller_azimuth), 0.0)
        drawn = _add(base, rotate(_sub(drawn, base), lean_axis, tiller_lean_rad(v, p)))
    return DeclinedVertex(position=drawn, browning=lk.browning, opacity=opacity(vin.fade, v, p))

"""Glade grass mesh from an organ table (GP roadmap, Phase 3): pure Python, stdlib only, no bpy.

Builds the healthy plant at one sample time of an export of ../lpy/grass.lpy (export_organs.py, schema v2). Each
leaf is one continuous surface (G35): its sheath wraps the tiller axis from the tiller base up to the collar, and its
blade continues the wrap above the collar, leaves the collar along the tiller axis, turns to its angle and arches in
its leaf plane (G22) while the wrap opens to the curled blade. The cross-section curl and the blade outline are copied
from the approved reference, ../assets/build_grass_plant.py (G21), which stays frozen because the wind demo loads its
GLB.
Decline is not baked: every vertex carries what the shader (and decline.declined_vertex) needs to apply it (G29).

Fixed topology (G28): every leaf has the same vertex count and order whatever its size and state, so builds at
different sample times with the same lifetime list share one vertex order and one face list. A leaf that is not
born yet at the built time, or collapsed (fade 1), is built degenerate: all its vertices at its first-seen attach.

Units are metres and radians, Z up.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_LPY_DIR = str(Path(__file__).resolve().parent.parent / "lpy")  # decline.py, the one home of the vector helpers
if _LPY_DIR not in sys.path:
    sys.path.insert(0, _LPY_DIR)

from decline import (  # noqa: E402
    KIND_BLADE, KIND_SHEATH, VertexInputs, cross, rotate, smoothstep, to_tiller_frame, unit)

SCHEMA_VERSION = 2
SAMPLE_TIME_TOLERANCE = 1e-6  # how close a requested time must be to a sample time

Vec3 = tuple[float, float, float]
UV = tuple[float, float]
Quad = tuple[int, int, int, int]

MATERIAL_SHEATH, MATERIAL_BLADE = 0, 1

# Per-vertex attributes (G29), by their names in PlantMesh; the glTF name is gltf_name(name), e.g. "_TILLER_AZ".
VECTOR_ATTRIBUTES: tuple[str, ...] = ("pivot", "axis", "collar", "side", "tiller_base", "tiller_axis")
SCALAR_ATTRIBUTES: tuple[str, ...] = ("s", "stem_t", "organ", "phase", "kind", "tiller_az", "tiller", "birth", "draw",
                                      "sen", "fade", "arch")
# Attributes that change with the build time (per stage in a VAT) and those fixed for a leaf's lifetime (G34).
STAGE_ATTRIBUTES: tuple[str, ...] = ("pivot", "axis", "collar", "s", "stem_t", "arch", "sen", "fade")
STATIC_ATTRIBUTES: tuple[str, ...] = tuple(n for n in (*VECTOR_ATTRIBUTES, *SCALAR_ATTRIBUTES)
                                           if n not in STAGE_ATTRIBUTES)


class OrganTableError(ValueError):
    """Raised when an organ table is not a valid schema v2 export or a build request does not fit it."""


@dataclass(frozen=True)
class GeometryParams:
    """Every constant of the mesh code; the curl and outline defaults are the reference plant's."""
    blade_segments: int = 16             # quads along a blade (blade_segments + 1 vertex rows, the first at the collar)
    cross_segments: int = 8              # quads across a leaf (cross_segments + 1 vertices per row)
    sheath_rows: int = 3                 # vertex rows of the sheath below the collar row (from the tiller base up)
    sheath_taper: float = 1.2            # wrap radius at the tiller base relative to the collar radius
    collar_open: float = 0.2             # blade fraction over which the collar wrap opens to the blade curl (G35)
    collar_bend: float = 0.15            # blade fraction over which the midrib turns from the tiller axis to its angle
    sheath_width_factor: float = 1.3     # a wrap's arc length is at most this times the blade width (G36: split)
    blade_row_power: float = 1.6         # blade row i sits at s = (i / blade_segments)^power, dense near emergence
    curl_base_rad: float = 0.35          # cross-section arc at the tip: 0.35 + 2.6 * (1 - s)^1.5
    curl_gain_rad: float = 2.6
    curl_power: float = 1.5
    outline_base: float = 0.5            # width profile: w * min(1, 0.5 + 1.6 s) * (1 - s)^0.6
    outline_rise: float = 1.6
    outline_taper: float = 0.6
    min_width_m: float = 0.0004          # floor of the width profile near the tip
    rolled_turn: float = 0.97            # the wrap (sheath, collar, rolled blade) is this fraction of a full turn
    roll_transition: float = 0.3         # blade fraction over which a rolled blade opens (G24)
    twist_range_rad: float = 0.5         # per-leaf cross-section twist at the tip, uniform in [-r, r]
    sheath_radius_min_m: float = 0.0008  # sheath radius at age 0 (before the size factor), G25
    sheath_radius_per_age_m: float = 0.00025
    sheath_radius_max_m: float = 0.0025

    def __post_init__(self) -> None:
        for name in ("blade_segments", "cross_segments", "sheath_rows"):
            if getattr(self, name) < 1:
                raise ValueError(f"GeometryParams.{name} must be >= 1, got {getattr(self, name)}")
        for name in ("collar_open", "collar_bend"):
            if not 0.0 < getattr(self, name) <= 1.0:
                raise ValueError(f"GeometryParams.{name} must be in (0, 1], got {getattr(self, name)}")
        if self.sheath_taper < 1.0:
            raise ValueError(f"GeometryParams.sheath_taper must be >= 1 (wider at the base), got {self.sheath_taper}")
        for name in ("sheath_width_factor", "blade_row_power"):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"GeometryParams.{name} must be positive, got {getattr(self, name)}")
        if not 0.0 < self.rolled_turn < 1.0:
            raise ValueError(f"GeometryParams.rolled_turn must be in (0, 1), got {self.rolled_turn}")
        if self.roll_transition <= 0.0:
            raise ValueError(f"GeometryParams.roll_transition must be positive, got {self.roll_transition}")
        if not 0.0 < self.sheath_radius_min_m <= self.sheath_radius_max_m:
            raise ValueError("GeometryParams needs 0 < sheath_radius_min_m <= sheath_radius_max_m, got "
                             f"{self.sheath_radius_min_m} and {self.sheath_radius_max_m}")
        if self.sheath_radius_per_age_m < 0.0:
            raise ValueError(f"GeometryParams.sheath_radius_per_age_m must be >= 0, got {self.sheath_radius_per_age_m}")

    @property
    def blade_vertex_count(self) -> int:
        return (self.blade_segments + 1) * (self.cross_segments + 1)

    @property
    def sheath_vertex_count(self) -> int:
        return self.sheath_rows * (self.cross_segments + 1)

    @property
    def leaf_vertex_count(self) -> int:
        return self.sheath_vertex_count + self.blade_vertex_count


@dataclass(frozen=True)
class Leaf:
    """One leaf of a sample; field names are the organ table's, so dataclasses.asdict gives decline.py's mapping."""
    id: str
    tiller: int
    rank: int
    birth_time: float
    age: float
    state: str
    attach: Vec3
    direction: Vec3
    azimuth_rad: float
    angle_from_axis_rad: float
    sheath_length_m: float
    blade_length_m: float
    blade_width_m: float
    unroll: float
    arch_rad: float
    senescence: float
    fade: float
    decline_draw: float


@dataclass(frozen=True)
class Tiller:
    """One tiller of a sample (organ-table fields; fixed at birth)."""
    id: str
    tiller: int
    rank: int
    birth_time: float
    base: Vec3
    direction: Vec3
    azimuth_rad: float
    splay_rad: float


@dataclass(frozen=True)
class Sample:
    time: float
    leaves: Mapping[str, Leaf]      # by id, in (birth_time, id) order
    tillers: Mapping[int, Tiller]   # by tiller index


@dataclass(frozen=True)
class OrganTable:
    model: str
    seed: int
    params: Mapping[str, object]   # every extern of the model, e.g. for decline.DeclineParams.from_mapping
    dt: float
    t_end: float
    samples: tuple[Sample, ...]

    @property
    def sample_times(self) -> tuple[float, ...]:
        return tuple(s.time for s in self.samples)


@dataclass(frozen=True)
class LeafRange:
    """Vertex index ranges [start, stop) of one leaf's sheath and blade in a PlantMesh.

    The sheath range is the sheath_rows rows below the collar; the blade range starts with the collar row, which the
    sheath's top faces share (one continuous surface, G35).
    """
    id: str
    sheath: tuple[int, int]
    blade: tuple[int, int]


@dataclass(frozen=True)
class PlantMesh:
    """The plant at one sample, ready for bpy's Mesh.from_pydata(positions, [], faces).

    - positions: one Vec3 per vertex.
    - faces: quads of vertex indices; Blender polygon i is faces[i].
    - face_uvs: per loop, face_uvs[i][k] is the UV of faces[i][k] (flatten in face order for the UV layer).
    - face_materials: MATERIAL_SHEATH (0) or MATERIAL_BLADE (1) per face.
    - vectors / scalars: one list per G29 attribute, keyed by VECTOR_ATTRIBUTES / SCALAR_ATTRIBUTES, one value per
      vertex (Blender 'POINT' domain, 'FLOAT_VECTOR' or 'FLOAT'); the glTF name is gltf_name(key).
    - leaves: per built leaf, in mesh order, its vertex ranges.

    Vertex order per leaf: one grid of rows of cross_segments + 1 vertices, first the sheath_rows sheath rows from
    the tiller base up, then the blade_segments + 1 blade rows from the collar to the tip. Faces are wound so their
    normals point to the underside: outward on the sheath and the collar wrap, down on an arched blade at rest.
    """
    positions: tuple[Vec3, ...]
    faces: tuple[Quad, ...]
    face_uvs: tuple[tuple[UV, UV, UV, UV], ...]
    face_materials: tuple[int, ...]
    vectors: Mapping[str, tuple[Vec3, ...]]
    scalars: Mapping[str, tuple[float, ...]]
    leaves: tuple[LeafRange, ...]


def gltf_name(name: str) -> str:
    """glTF custom attribute name of a PlantMesh attribute key (e.g. "tiller_az" -> "_TILLER_AZ")."""
    return "_" + name.upper()


# Loading.

def _vec(where: str, value: object) -> Vec3:
    if not isinstance(value, list) or len(value) != 3 or not all(_is_number(c) for c in value):
        raise OrganTableError(f"{where} must be a list of 3 finite numbers, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _num(where: str, value: object) -> float:
    if not _is_number(value):
        raise OrganTableError(f"{where} must be a finite number, got {value!r}")
    return float(value)  # type: ignore[arg-type]


def _int(where: str, value: object) -> int:
    """An integral number (an int, or a float with no fractional part) as an int; anything else raises."""
    number = _num(where, value)
    if not number.is_integer():
        raise OrganTableError(f"{where} must be an integer, got {value!r}")
    return int(number)


def _field(organ: Mapping[str, object], name: str, time: float) -> object:
    if name not in organ:
        raise OrganTableError(f"organ {organ.get('id')!r} at t={time}: field {name!r} is missing")
    return organ[name]


def _leaf(organ: Mapping[str, object], time: float) -> Leaf:
    def num(name: str) -> float:
        return _num(f"leaf {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    def vec(name: str) -> Vec3:
        return _vec(f"leaf {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    def integer(name: str) -> int:
        return _int(f"leaf {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    return Leaf(id=str(_field(organ, "id", time)), tiller=integer("tiller"), rank=integer("rank"),
                birth_time=num("birth_time"), age=num("age"), state=str(_field(organ, "state", time)),
                attach=vec("attach"), direction=vec("direction"), azimuth_rad=num("azimuth_rad"),
                angle_from_axis_rad=num("angle_from_axis_rad"), sheath_length_m=num("sheath_length_m"),
                blade_length_m=num("blade_length_m"), blade_width_m=num("blade_width_m"), unroll=num("unroll"),
                arch_rad=num("arch_rad"), senescence=num("senescence"), fade=num("fade"),
                decline_draw=num("decline_draw"))


def _tiller(organ: Mapping[str, object], time: float) -> Tiller:
    def num(name: str) -> float:
        return _num(f"tiller {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    def integer(name: str) -> int:
        return _int(f"tiller {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    def vec(name: str) -> Vec3:
        return _vec(f"tiller {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    return Tiller(id=str(_field(organ, "id", time)), tiller=integer("tiller"), rank=integer("rank"),
                  birth_time=num("birth_time"), base=vec("base"), direction=vec("direction"),
                  azimuth_rad=num("azimuth_rad"), splay_rad=num("splay_rad"))


def parse_table(document: Mapping[str, object]) -> OrganTable:
    """An OrganTable from a decoded export; raises OrganTableError naming the bad field."""
    if document.get("schema_version") != SCHEMA_VERSION:
        raise OrganTableError(f"organ table schema_version must be {SCHEMA_VERSION}, got "
                              f"{document.get('schema_version')!r}; re-export with lpy/export_organs.py")
    for key in ("model", "seed", "params", "dt", "t_end", "samples"):
        if key not in document:
            raise OrganTableError(f"organ table field {key!r} is missing")
    params = document["params"]
    if not isinstance(params, dict):
        raise OrganTableError(f"organ table field 'params' must be an object, got {type(params).__name__}")
    seed = document["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise OrganTableError(f"organ table field 'seed' must be an int, got {seed!r}")
    raw_samples = document["samples"]
    if not isinstance(raw_samples, list) or not raw_samples:
        raise OrganTableError("organ table field 'samples' must be a non-empty list")
    samples = []
    for raw in raw_samples:
        if not isinstance(raw, dict):
            raise OrganTableError(f"each organ table sample must be an object, got {type(raw).__name__}")
        time = _num("sample time", raw.get("time"))
        organs = raw.get("organs", [])
        if not isinstance(organs, list):
            raise OrganTableError(f"sample at t={time}: 'organs' must be a list, got {type(organs).__name__}")
        leaves: dict[str, Leaf] = {}
        tillers: dict[int, Tiller] = {}
        for organ in organs:
            if not isinstance(organ, dict):
                raise OrganTableError(f"sample at t={time}: each organ must be an object, got {type(organ).__name__}")
            kind = organ.get("kind")
            if kind == "leaf":
                leaf = _leaf(organ, time)
                leaves[leaf.id] = leaf
            elif kind == "tiller":
                tiller = _tiller(organ, time)
                tillers[tiller.tiller] = tiller
            else:
                raise OrganTableError(f"organ {organ.get('id')!r} at t={time}: kind must be leaf or tiller, "
                                      f"got {kind!r}")
        ordered = dict(sorted(leaves.items(), key=lambda kv: (kv[1].birth_time, kv[0])))
        for leaf in ordered.values():
            if leaf.tiller not in tillers:
                raise OrganTableError(f"leaf {leaf.id} at t={time}: its tiller {leaf.tiller} is not in the sample")
        samples.append(Sample(time=time, leaves=ordered, tillers=tillers))
    return OrganTable(model=str(document["model"]), seed=seed, params=dict(params),
                      dt=_num("dt", document["dt"]), t_end=_num("t_end", document["t_end"]), samples=tuple(samples))


def load_table(path: Path) -> OrganTable:
    """Read an export of lpy/export_organs.py (schema v2)."""
    return parse_table(json.loads(Path(path).read_text(encoding="utf-8")))


def lifetime_leaves(table: OrganTable) -> list[str]:
    """Every leaf id the plant ever has (union over samples), in (birth_time, id) order."""
    births: dict[str, float] = {}
    for sample in table.samples:
        for leaf in sample.leaves.values():
            births.setdefault(leaf.id, leaf.birth_time)
    return sorted(births, key=lambda lid: (births[lid], lid))


def sample_at(table: OrganTable, time: float) -> Sample:
    """The sample at this time (within SAMPLE_TIME_TOLERANCE); the error names the nearest sample times otherwise."""
    for sample in table.samples:
        if abs(sample.time - time) <= SAMPLE_TIME_TOLERANCE:
            return sample
    nearest = sorted(table.sample_times, key=lambda t: abs(t - time))[:2]
    raise OrganTableError(f"time {time} is not a sample time of the organ table; nearest samples: {sorted(nearest)}")


# Profiles (copied from the reference, build_grass_plant.py).

def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def curl_angle(s: float, p: GeometryParams) -> float:
    """Cross-section arc angle: rolled at the base as if it had wrapped the stem, near flat at the tip."""
    return p.curl_base_rad + p.curl_gain_rad * (1.0 - s) ** p.curl_power


def leaf_width(w: float, s: float, p: GeometryParams) -> float:
    """Narrow base, widest at about a third, long taper to the tip."""
    return max(w * min(1.0, p.outline_base + s * p.outline_rise) * (1.0 - s) ** p.outline_taper, p.min_width_m)


def unroll_blend(s: float, unroll: float, p: GeometryParams) -> float:
    """How open the blade is at fraction s: 0 rolled, 1 the mature reference profile (G24).

    The opening window [a, a + T] (T = roll_transition) slides from beyond the tip (a = 1, unroll 0: rolled
    everywhere) to below the collar (a = -T, unroll 1: open everywhere), so the rolled part shrinks from the tip down.
    """
    a = 1.0 - unroll * (1.0 + p.roll_transition)
    return smoothstep((s - a) / p.roll_transition)


def sheath_radius(age: float, elongation_age: float, p: GeometryParams) -> float:
    """Sheath tube radius at this leaf age (G25): grows with age, so older sheaths enclose younger ones."""
    size = smoothstep(age / elongation_age)
    return min(p.sheath_radius_min_m + p.sheath_radius_per_age_m * age, p.sheath_radius_max_m) * size


def leaf_side(leaf: Leaf, tiller: Tiller) -> Vec3:
    """The leaf's arch axis (G22): the normal of its leaf plane, (-sin az, cos az, 0) in the tiller's frame."""
    az = leaf.azimuth_rad
    return unit(to_tiller_frame((-math.sin(az), math.cos(az), 0.0), tiller.direction))


def _leaf_draws(seed: int, lid: str, p: GeometryParams) -> tuple[float, float]:
    """Twist and wind phase of a leaf, each from its own stream so neither shifts the other (G22)."""
    twist = random.Random(f"twist:{seed}:{lid}").uniform(-p.twist_range_rad, p.twist_range_rad)
    phase = random.Random(f"phase:{seed}:{lid}").random()
    return twist, phase


def collar_open_blend(s: float, unroll: float, p: GeometryParams) -> float:
    """How open the leaf is at blade fraction s (G35): 0 the wrap, 1 the reference curl and outline."""
    return min(smoothstep(s / p.collar_open), unroll_blend(s, unroll, p))


def midrib_direction(tiller_axis: Vec3, side: Vec3, angle: float, arch: float, s: float, p: GeometryParams) -> Vec3:
    """Blade midrib tangent at fraction s (G35): along the tiller axis at the collar, turned to `angle` by
    collar_bend, plus the arch: rotate(tiller_axis, side, angle * smoothstep(s / collar_bend) + arch * s^2)."""
    return rotate(tiller_axis, side, angle * smoothstep(s / p.collar_bend) + arch * s * s)


# Building.

Row = list[Vec3]


def _add(a: Sequence[float], b: Sequence[float], k: float = 1.0) -> Vec3:
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


def _arc_row(midrib: Vec3, d: Vec3, wside: Vec3, theta: float, radius: float, p: GeometryParams) -> Row:
    """One cross-section: an arc of angle theta and this radius through the midrib point, across the tangent d,
    curling toward the upper side cross(wside, d); a wrap of rolled_turn turns is centred `radius` from the midrib."""
    nrm = unit(cross(wside, d))
    row = []
    for j in range(p.cross_segments + 1):
        phi = (j / p.cross_segments - 0.5) * theta
        row.append(_add(_add(midrib, wside, radius * math.sin(phi)), nrm, radius * (1.0 - math.cos(phi))))
    return row


@dataclass(frozen=True)
class LeafRows:
    """One leaf's grid of positions: sheath rows (tiller base up, below the collar) and blade rows (emergence point to
    tip), cross_segments + 1 positions each; the midrib is column cross_segments // 2."""
    sheath: tuple[Row, ...]
    blade: tuple[Row, ...]


def blade_row_fractions(p: GeometryParams) -> tuple[float, ...]:
    """Free-blade fraction s of each blade row (G36): (i / blade_segments)^blade_row_power, dense near emergence."""
    return tuple((i / p.blade_segments) ** p.blade_row_power for i in range(p.blade_segments + 1))


def wrap_angle(radius: float, blade_width: float, p: GeometryParams) -> float:
    """Arc angle of a wrap of this radius (G36): rolled_turn of a full turn, or less (a split sheath) where that
    arc would be longer than sheath_width_factor times the blade width."""
    closed = 2.0 * math.pi * p.rolled_turn
    cap = p.sheath_width_factor * blade_width
    return closed if radius * closed <= cap else cap / radius


def emergence_heights(sample: Sample) -> dict[str, float]:
    """Height along its tiller axis at which each leaf's blade leaves the enclosing sheaths (G36).

    h_e = max(own, max_k lerp(own, h_k, 1 - fade_k)) over the older leaves k of the same tiller that are not
    collapsed, own and h_k being collar heights (sheath lengths); weighting by 1 - fade lets the emergence point ease
    down while an enclosing leaf fades instead of jumping when it collapses.
    """
    heights: dict[str, float] = {}
    for leaf in sample.leaves.values():
        own = leaf.sheath_length_m
        h = own
        for older in sample.leaves.values():
            if older.tiller == leaf.tiller and older.birth_time < leaf.birth_time and older.fade < 1.0:
                h = max(h, lerp(own, older.sheath_length_m, 1.0 - older.fade))
        heights[leaf.id] = h
    return heights


def leaf_rows(leaf: Leaf, tiller: Tiller, collar_radius: float, twist: float, p: GeometryParams,
              degenerate_at: Vec3 | None = None, emergence_m: float | None = None) -> LeafRows:
    """The continuous surface of one leaf (G35, G36) from its organ-table values, its tiller and its collar wrap radius.

    Reads leaf.attach (the collar on the tiller axis), sheath_length_m, azimuth_rad, angle_from_axis_rad,
    blade_length_m, blade_width_m, unroll and arch_rad, and tiller.base and tiller.direction, so a declined leaf is
    rebuilt by passing the declined values, its declined collar radius and the leaned tiller. emergence_m is the
    emergence height along the tiller axis (emergence_heights; default the own collar). Every row is an arc through
    the midrib that curls toward the tiller axis, of at most wrap_angle (a split sheath where the wrap would be wider
    than the blade allows):
    - sheath row k (height fraction k / sheath_rows of the own sheath) is the wrap of radius collar_radius *
      lerp(sheath_taper, 1, k / sheath_rows) around the tiller axis, its midrib on the outer wall at the leaf's azimuth;
    - the blade runs straight up the axis, wrapped at collar_radius, from the collar to the emergence point (the band
      from the last sheath row to blade row 0, at most the blade's length), then over its free length: blade row i
      sits at free fraction s_i (blade_row_fractions), the midrib follows midrib_direction(s) from the outer wall
      (midpoint rule between rows), the cross-section is twisted by twist * s and opens from the wrap to the reference
      curl and outline by collar_open_blend b: arc angle lerp(wrap, curl, b), radius lerp(collar_radius, curl radius,
      b^2) (the margins first slide around the stem), the arc no wider than lerp(wrap width, blade width, b).
    With degenerate_at every position is that point.
    """
    axis = unit(tiller.direction)
    side = leaf_side(leaf, tiller)
    out = cross(side, axis)  # toward the leaf's azimuth, perpendicular to the tiller axis
    inward = (-side[0], -side[1], -side[2])  # the width direction at the collar (the reference's d x Z)
    width = leaf.blade_width_m

    def place(row: Row) -> Row:
        return [degenerate_at] * len(row) if degenerate_at is not None else row

    sheath = []
    for k in range(p.sheath_rows):
        f = k / p.sheath_rows
        radius = collar_radius * lerp(p.sheath_taper, 1.0, f)
        midrib = _add(_add(tiller.base, axis, leaf.sheath_length_m * f), out, radius)
        sheath.append(place(_arc_row(midrib, axis, inward, wrap_angle(radius, width, p), radius, p)))

    own = leaf.sheath_length_m
    enclosed = min(max((own if emergence_m is None else emergence_m) - own, 0.0), leaf.blade_length_m)
    free = leaf.blade_length_m - enclosed
    wrap = wrap_angle(collar_radius, width, p)
    path = _add(_add(leaf.attach, axis, enclosed), out, collar_radius)
    fractions = blade_row_fractions(p)
    blade = []
    for i, s in enumerate(fractions):
        d = midrib_direction(axis, side, leaf.angle_from_axis_rad, leaf.arch_rad, s, p)
        wside = rotate(inward, d, twist * s)
        blend = collar_open_blend(s, leaf.unroll, p)
        theta = lerp(wrap, curl_angle(s, p), blend)
        blade_width = leaf_width(width, s, p)
        # The radius eases late, so the opening margins first slide around the stem instead of flaring out, but the
        # arc never gets wider than the straight blend from the wrap's width to the blade's.
        radius = lerp(collar_radius, blade_width / curl_angle(s, p), blend * blend)
        if theta > 0.0:  # a zero-width (collapsed) leaf has no arc to bound
            radius = min(radius, lerp(wrap * collar_radius, blade_width, blend) / theta)
        blade.append(place(_arc_row(path, d, wside, theta, radius, p)))
        if i < p.blade_segments:
            s_mid = 0.5 * (s + fractions[i + 1])  # midpoint rule between rows
            path = _add(path, midrib_direction(axis, side, leaf.angle_from_axis_rad, leaf.arch_rad, s_mid, p),
                        free * (fractions[i + 1] - s))
    if free <= 0.0:  # the whole blade is still inside older sheaths: it is the wrap at its tip, zero area
        blade = [blade[0]] * len(blade)
    return LeafRows(sheath=tuple(sheath), blade=tuple(blade))


@dataclass
class _Builder:
    """Mutable accumulator local to one build_plant call."""
    positions: list[Vec3] = field(default_factory=list)
    faces: list[Quad] = field(default_factory=list)
    face_uvs: list[tuple[UV, UV, UV, UV]] = field(default_factory=list)
    face_materials: list[int] = field(default_factory=list)
    vectors: dict[str, list[Vec3]] = field(default_factory=lambda: {n: [] for n in VECTOR_ATTRIBUTES})
    scalars: dict[str, list[float]] = field(default_factory=lambda: {n: [] for n in SCALAR_ATTRIBUTES})

    def vertex(self, position: Vec3, vectors: Mapping[str, Vec3], scalars: Mapping[str, float]) -> int:
        self.positions.append(position)
        for name in VECTOR_ATTRIBUTES:
            self.vectors[name].append(vectors[name])
        for name in SCALAR_ATTRIBUTES:
            self.scalars[name].append(scalars[name])
        return len(self.positions) - 1

    def grid(self, rows: Sequence[Sequence[int]], materials: Sequence[int]) -> None:
        """Quads (a[j], c[j], c[j+1], a[j+1]) between successive rows, wound as the reference blade; the band from
        row i to row i + 1 gets materials[i]. UVs run across the leaf (u) and along the whole leaf (v)."""
        last = len(rows) - 1
        for i, (a, c) in enumerate(zip(rows, rows[1:])):
            n = len(a) - 1
            for j in range(n):
                self.faces.append((a[j], c[j], c[j + 1], a[j + 1]))
                self.face_uvs.append(((j / n, i / last), (j / n, (i + 1) / last), ((j + 1) / n, (i + 1) / last),
                                      ((j + 1) / n, i / last)))
                self.face_materials.append(materials[i])


def build_plant(table: OrganTable, time: float, p: GeometryParams = GeometryParams(),
                lifetime: Sequence[str] | None = None) -> PlantMesh:
    """The healthy plant mesh at a sample time.

    With `lifetime` (leaf ids, e.g. lifetime_leaves(table)) every listed leaf is built in that order, and leaves not
    born at `time` or collapsed are degenerate at their first-seen attach (G28); without it, the leaves present at
    `time` are built. `_ORGAN` is the leaf's index in lifetime_leaves(table) plus 1 either way.

    STATIC_ATTRIBUTES come from the leaf's first-seen record, so they are identical at every build time. A degenerate
    leaf has fade 1 (opacity 0) and its collar and blade pivot at its anchor, so decline and wind keep it collapsed;
    otherwise both are the midrib vertex of blade row 0 (the emergence row, on the outer wall of the wrap, G37).
    """
    sample = sample_at(table, time)
    order = lifetime_leaves(table)
    organ_index = {lid: i + 1 for i, lid in enumerate(order)}
    first_seen: dict[str, tuple[Leaf, Tiller]] = {}
    for s in table.samples:
        for leaf in s.leaves.values():
            first_seen.setdefault(leaf.id, (leaf, s.tillers[leaf.tiller]))
    ids = list(sample.leaves) if lifetime is None else list(lifetime)
    unknown = [lid for lid in ids if lid not in organ_index]
    if unknown:
        raise OrganTableError(f"lifetime leaves {unknown} are not in the organ table; use lifetime_leaves(table)")
    elongation_age = _num("params ELONGATION_AGE", table.params.get("ELONGATION_AGE"))
    plateau = _num("params SHEATH_PLATEAU_M", table.params.get("SHEATH_PLATEAU_M"))
    materials = [MATERIAL_SHEATH] * p.sheath_rows + [MATERIAL_BLADE] * p.blade_segments
    emergence = emergence_heights(sample)
    fractions = blade_row_fractions(p)
    out = _Builder()
    ranges = []
    for lid in ids:
        if lid in sample.leaves:
            leaf, tiller = sample.leaves[lid], sample.tillers[sample.leaves[lid].tiller]
        else:
            leaf, tiller = first_seen[lid]
        first_leaf, first_tiller = first_seen[lid]
        degenerate = lid not in sample.leaves or leaf.fade >= 1.0
        anchor = first_leaf.attach if degenerate else None
        axis = unit(first_tiller.direction)
        emergence_m = leaf.sheath_length_m if degenerate else emergence[lid]
        twist, phase = _leaf_draws(table.seed, lid, p)
        grid = leaf_rows(leaf, tiller, sheath_radius(leaf.age, elongation_age, p), twist, p, anchor, emergence_m)
        # The collar attribute is the blade's midrib start at the emergence row, on the wrap wall (G36, G37): decline
        # turns the blade about it and scales everything below it uniformly.
        collar = grid.blade[0][p.cross_segments // 2]
        direction = unit(leaf.direction)
        shared_vectors = {"collar": collar, "side": leaf_side(first_leaf, first_tiller),
                          "tiller_base": first_tiller.base, "tiller_axis": axis}
        shared_scalars = {"organ": float(organ_index[lid]), "phase": phase, "tiller_az": first_tiller.azimuth_rad,
                          "tiller": float(first_tiller.tiller), "birth": first_leaf.birth_time,
                          "draw": first_leaf.decline_draw, "sen": leaf.senescence,
                          "fade": 1.0 if degenerate else leaf.fade, "arch": leaf.arch_rad}
        sheath_vectors = {**shared_vectors, "pivot": first_tiller.base, "axis": axis}
        blade_vectors = {**shared_vectors, "pivot": collar, "axis": direction}

        start = len(out.positions)
        rows: list[list[int]] = []
        for k, row in enumerate(grid.sheath):
            f = k / p.sheath_rows
            scalars = {**shared_scalars, "s": f, "stem_t": leaf.sheath_length_m * f / plateau,
                       "kind": float(KIND_SHEATH)}
            rows.append([out.vertex(q, sheath_vectors, scalars) for q in row])
        mid = len(out.positions)
        for i, row in enumerate(grid.blade):
            scalars = {**shared_scalars, "s": fractions[i], "stem_t": emergence_m / plateau,
                       "kind": float(KIND_BLADE)}
            rows.append([out.vertex(q, blade_vectors, scalars) for q in row])
        out.grid(rows, materials)
        ranges.append(LeafRange(id=lid, sheath=(start, mid), blade=(mid, len(out.positions))))

    return PlantMesh(positions=tuple(out.positions), faces=tuple(out.faces), face_uvs=tuple(out.face_uvs),
                     face_materials=tuple(out.face_materials),
                     vectors={k: tuple(v) for k, v in out.vectors.items()},
                     scalars={k: tuple(v) for k, v in out.scalars.items()}, leaves=tuple(ranges))


def _integral(name: str, value: float, i: int) -> int:
    if not (math.isfinite(value) and value.is_integer()):
        raise ValueError(f"vertex {i}: attribute {name!r} must be integral, got {value!r}")
    return int(value)


def vertex_inputs(mesh: PlantMesh, i: int) -> VertexInputs:
    """The decline inputs (decline.VertexInputs) of vertex i, read from its attributes (G29, G32)."""
    sc, vc = mesh.scalars, mesh.vectors
    return VertexInputs(kind=_integral("kind", sc["kind"][i], i), s=sc["s"][i], collar=vc["collar"][i],
                        side=vc["side"][i], axis=vc["axis"][i], tiller_base=vc["tiller_base"][i],
                        tiller_axis=vc["tiller_axis"][i], tiller_azimuth=sc["tiller_az"][i],
                        tiller=_integral("tiller", sc["tiller"][i], i), birth_time=sc["birth"][i],
                        decline_draw=sc["draw"][i], senescence=sc["sen"][i], fade=sc["fade"][i],
                        arch_rad=sc["arch"][i])

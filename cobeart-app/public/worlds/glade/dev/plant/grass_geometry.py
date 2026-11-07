"""Glade grass mesh from an organ table (GP roadmap, Phase 3): pure Python, stdlib only, no bpy.

Builds the healthy plant at one sample time of an export of ../lpy/grass.lpy (export_organs.py, schema v2). Each
leaf is a sheath tube from its tiller base to its collar along the tiller axis, then a curled blade from the collar
along its direction, arched in its leaf plane (G22). The cross-section curl and the blade outline are copied from the
approved reference, ../assets/build_grass_plant.py (G21), which stays frozen because the wind demo loads its GLB.
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

from decline import cross, rotate, to_tiller_frame, unit  # noqa: E402

SCHEMA_VERSION = 2
TIME_TOLERANCE = 1e-6

Vec3 = tuple[float, float, float]
UV = tuple[float, float]
Quad = tuple[int, int, int, int]

MATERIAL_SHEATH, MATERIAL_BLADE = 0, 1
KIND_SHEATH, KIND_BLADE = 0.0, 1.0

# Per-vertex attributes (G29), by their names in PlantMesh; the glTF name is gltf_name(name), e.g. "_TILLER_AZ".
VECTOR_ATTRIBUTES: tuple[str, ...] = ("pivot", "axis", "collar", "side", "tiller_base", "tiller_axis")
SCALAR_ATTRIBUTES: tuple[str, ...] = ("s", "stem_t", "organ", "phase", "kind", "tiller_az", "tiller", "birth", "draw",
                                      "sen", "fade", "arch")


class OrganTableError(ValueError):
    """Raised when an organ table is not a valid schema v2 export or a build request does not fit it."""


@dataclass(frozen=True)
class GeometryParams:
    """Every constant of the mesh code; the curl and outline defaults are the reference plant's."""
    blade_segments: int = 16             # quads along a blade (blade_segments + 1 vertex rows)
    cross_segments: int = 4              # quads across a blade (cross_segments + 1 vertices per row)
    tube_sides: int = 8                  # quads around a sheath (seam vertices doubled for UVs)
    tube_rings: int = 2                  # vertex rings along a sheath
    curl_base_rad: float = 0.35          # cross-section arc at the tip: 0.35 + 2.6 * (1 - s)^1.5
    curl_gain_rad: float = 2.6
    curl_power: float = 1.5
    outline_base: float = 0.5            # width profile: w * min(1, 0.5 + 1.6 s) * (1 - s)^0.6
    outline_rise: float = 1.6
    outline_taper: float = 0.6
    min_width_m: float = 0.0004          # floor of the width profile near the tip
    rolled_turn: float = 0.97            # a rolled blade is this fraction of a full turn
    roll_transition: float = 0.3         # blade fraction over which a rolled blade opens (G24)
    twist_range_rad: float = 0.5         # per-leaf cross-section twist at the tip, uniform in [-r, r]
    sheath_radius_min_m: float = 0.0008  # sheath radius at age 0 (before the size factor), G25
    sheath_radius_per_age_m: float = 0.00025
    sheath_radius_max_m: float = 0.0025

    def __post_init__(self) -> None:
        for name in ("blade_segments", "cross_segments", "tube_sides"):
            if getattr(self, name) < 1:
                raise ValueError(f"GeometryParams.{name} must be >= 1, got {getattr(self, name)}")
        if self.tube_rings < 2:
            raise ValueError(f"GeometryParams.tube_rings must be >= 2, got {self.tube_rings}")
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
        return self.tube_rings * (self.tube_sides + 1)


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
    """Vertex index ranges [start, stop) of one leaf's sheath and blade in a PlantMesh."""
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

    Vertex order per leaf: its sheath (tube_rings rings of tube_sides + 1 vertices, base to collar), then its blade
    (blade_segments + 1 rows of cross_segments + 1 vertices, collar to tip). Blade faces are wound so their normals
    point to the underside at rest, sheath faces so theirs point outward.
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


def _field(organ: Mapping[str, object], name: str, time: float) -> object:
    if name not in organ:
        raise OrganTableError(f"organ {organ.get('id')!r} at t={time}: field {name!r} is missing")
    return organ[name]


def _leaf(organ: Mapping[str, object], time: float) -> Leaf:
    def num(name: str) -> float:
        return _num(f"leaf {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    def vec(name: str) -> Vec3:
        return _vec(f"leaf {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    return Leaf(id=str(_field(organ, "id", time)), tiller=int(num("tiller")), rank=int(num("rank")),
                birth_time=num("birth_time"), age=num("age"), state=str(_field(organ, "state", time)),
                attach=vec("attach"), direction=vec("direction"), azimuth_rad=num("azimuth_rad"),
                angle_from_axis_rad=num("angle_from_axis_rad"), sheath_length_m=num("sheath_length_m"),
                blade_length_m=num("blade_length_m"), blade_width_m=num("blade_width_m"), unroll=num("unroll"),
                arch_rad=num("arch_rad"), senescence=num("senescence"), fade=num("fade"),
                decline_draw=num("decline_draw"))


def _tiller(organ: Mapping[str, object], time: float) -> Tiller:
    def num(name: str) -> float:
        return _num(f"tiller {organ.get('id')!r} at t={time}: {name}", _field(organ, name, time))

    return Tiller(id=str(_field(organ, "id", time)), tiller=int(num("tiller")), rank=int(num("rank")),
                  birth_time=num("birth_time"), base=_vec(f"tiller {organ.get('id')!r}: base", organ.get("base")),
                  direction=_vec(f"tiller {organ.get('id')!r}: direction", organ.get("direction")),
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
        time = _num("sample time", raw.get("time"))
        leaves: dict[str, Leaf] = {}
        tillers: dict[int, Tiller] = {}
        for organ in raw.get("organs", []):
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
    """The sample at this time (within TIME_TOLERANCE); the error names the nearest sample times otherwise."""
    for sample in table.samples:
        if abs(sample.time - time) <= TIME_TOLERANCE:
            return sample
    nearest = sorted(table.sample_times, key=lambda t: abs(t - time))[:2]
    raise OrganTableError(f"time {time} is not a sample time of the organ table; nearest samples: {sorted(nearest)}")


# Profiles (copied from the reference, build_grass_plant.py).

def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


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


# Building.

def _add(a: Sequence[float], b: Sequence[float], k: float = 1.0) -> Vec3:
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


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

    def grid(self, rows: list[list[tuple[int, UV]]], material: int, along_first: bool) -> None:
        """Quads over a grid of rows; along_first winds (a[j], c[j], c[j+1], a[j+1]) as the reference blade does,
        otherwise (a[j], a[j+1], c[j+1], c[j]) as its tubes do."""
        for a, c in zip(rows, rows[1:]):
            for j in range(len(a) - 1):
                quad = (a[j], c[j], c[j + 1], a[j + 1]) if along_first else (a[j], a[j + 1], c[j + 1], c[j])
                self.faces.append((quad[0][0], quad[1][0], quad[2][0], quad[3][0]))
                self.face_uvs.append((quad[0][1], quad[1][1], quad[2][1], quad[3][1]))
                self.face_materials.append(material)


def _sheath_rows(leaf: Leaf, tiller: Tiller, radius: float, degenerate_at: Vec3 | None,
                 p: GeometryParams) -> list[list[tuple[Vec3, float, UV]]]:
    """Ring vertices (position, s, uv) of the sheath tube, base to collar along the tiller axis."""
    axis = unit(tiller.direction)
    ref = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
    n = unit(cross(axis, ref))
    b = cross(axis, n)
    rows = []
    for i in range(p.tube_rings):
        s = i / (p.tube_rings - 1)
        centre = _add(tiller.base, axis, leaf.sheath_length_m * s)
        row = []
        for j in range(p.tube_sides + 1):
            a = 2.0 * math.pi * j / p.tube_sides
            pos = degenerate_at or _add(_add(centre, n, radius * math.cos(a)), b, radius * math.sin(a))
            row.append((pos, s, (j / p.tube_sides, s)))
        rows.append(row)
    return rows


def blade_rows(leaf: Leaf, side: Vec3, r0: float, twist: float, degenerate_at: Vec3 | None,
               p: GeometryParams) -> list[list[tuple[Vec3, float, UV]]]:
    """Row vertices (position, s, uv) of the curled blade from the collar (G22, G24).

    The midrib starts at attach and follows rotate(direction, side, arch * s^2), integrated with one step per row.
    The width runs along -side (the reference's d x Z), twisted about the midrib by twist * s; the cross-section is
    an arc of curl_angle(s) whose edges rise toward the upper side, and a rolled section is a closed arc of the sheath
    radius r0 centred on the midrib.
    """
    d0 = unit(leaf.direction)
    closed = 2.0 * math.pi * p.rolled_turn
    path = leaf.attach
    step = leaf.blade_length_m / p.blade_segments
    rows = []
    for i in range(p.blade_segments + 1):
        s = i / p.blade_segments
        d = unit(rotate(d0, side, leaf.arch_rad * s * s))
        wside = unit(rotate((-side[0], -side[1], -side[2]), d, twist * s))
        nrm = unit(cross(wside, d))  # the upper side of the blade
        blend = unroll_blend(s, leaf.unroll, p)
        theta = lerp(closed, curl_angle(s, p), blend)
        radius = lerp(closed * r0, leaf_width(leaf.blade_width_m, s, p), blend) / theta
        pos = _add(path, nrm, -radius * (1.0 - blend))
        row = []
        for j in range(p.cross_segments + 1):
            phi = (j / p.cross_segments - 0.5) * theta
            v = degenerate_at or _add(_add(pos, wside, radius * math.sin(phi)), nrm, radius * (1.0 - math.cos(phi)))
            row.append((v, s, (j / p.cross_segments, s)))
        rows.append(row)
        path = _add(path, d, step)
    return rows


def build_plant(table: OrganTable, time: float, p: GeometryParams = GeometryParams(),
                lifetime: Sequence[str] | None = None) -> PlantMesh:
    """The healthy plant mesh at a sample time.

    With `lifetime` (leaf ids, e.g. lifetime_leaves(table)) every listed leaf is built in that order, and leaves not
    born at `time` or collapsed are degenerate at their first-seen attach (G28); without it, the leaves present at
    `time` are built. `_ORGAN` is the leaf's index in lifetime_leaves(table) plus 1 either way.
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
    out = _Builder()
    ranges = []
    for lid in ids:
        if lid in sample.leaves:
            leaf, tiller = sample.leaves[lid], sample.tillers[sample.leaves[lid].tiller]
        else:
            leaf, tiller = first_seen[lid]
        degenerate = lid not in sample.leaves or leaf.fade >= 1.0
        anchor = first_seen[lid][0].attach if degenerate else None
        side = leaf_side(leaf, tiller)
        twist, phase = _leaf_draws(table.seed, lid, p)
        radius = sheath_radius(leaf.age, elongation_age, p)
        axis = unit(tiller.direction)
        shared_vectors = {"collar": leaf.attach, "side": side, "tiller_base": tiller.base, "tiller_axis": axis}
        shared_scalars = {"organ": float(organ_index[lid]), "phase": phase, "tiller_az": tiller.azimuth_rad,
                          "tiller": float(tiller.tiller), "birth": leaf.birth_time, "draw": leaf.decline_draw,
                          "sen": leaf.senescence, "fade": leaf.fade, "arch": leaf.arch_rad}

        start = len(out.positions)
        rows = []
        for ring in _sheath_rows(leaf, tiller, radius, anchor, p):
            row = []
            for pos, s, uv in ring:
                height = leaf.sheath_length_m * s
                index = out.vertex(pos, {**shared_vectors, "pivot": tiller.base, "axis": axis},
                                   {**shared_scalars, "s": s, "stem_t": height / plateau, "kind": KIND_SHEATH})
                row.append((index, uv))
            rows.append(row)
        out.grid(rows, MATERIAL_SHEATH, along_first=False)
        mid = len(out.positions)

        rows = []
        for blade_row in blade_rows(leaf, side, radius, twist, anchor, p):
            row = []
            for pos, s, uv in blade_row:
                index = out.vertex(pos, {**shared_vectors, "pivot": leaf.attach, "axis": unit(leaf.direction)},
                                   {**shared_scalars, "s": s, "stem_t": leaf.sheath_length_m / plateau,
                                    "kind": KIND_BLADE})
                row.append((index, uv))
            rows.append(row)
        out.grid(rows, MATERIAL_BLADE, along_first=True)
        ranges.append(LeafRange(id=lid, sheath=(start, mid), blade=(mid, len(out.positions))))

    return PlantMesh(positions=tuple(out.positions), faces=tuple(out.faces), face_uvs=tuple(out.face_uvs),
                     face_materials=tuple(out.face_materials),
                     vectors={k: tuple(v) for k, v in out.vectors.items()},
                     scalars={k: tuple(v) for k, v in out.scalars.items()}, leaves=tuple(ranges))

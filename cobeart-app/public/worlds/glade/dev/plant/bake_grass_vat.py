"""Grass VAT bake (GP roadmap, Phase 4): an organ table to a cobeart-vat file (../vat/vat_format.md). Stdlib only.

Usage: ~/miniconda3/envs/lpy/bin/python dev/plant/bake_grass_vat.py ORGANS.json OUT.vat.glb

Every sample of the table is one stage (G46) and every lifetime leaf one organ, in lifetime_leaves order. Each stage
is a fixed-topology build_plant over the lifetime list, so one triangle list serves all stages. Per vertex and stage
the file holds only the position (f16) and the area-weighted normal (oct16, G42); everything else is per vertex
static, per organ static or per organ per stage (G40), with the senescence and fade events analytic (G38). A leaf
collapsed at a stage keeps the positions, normals and organ stage values of the last stage where it was visible
(G39); unborn leaves stay degenerate at their first-seen attach.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
import sys
import time
from collections.abc import Sequence
from enum import Enum
from pathlib import Path

_PLANT_DIR = Path(__file__).resolve().parent
for _dir in (_PLANT_DIR, _PLANT_DIR.parent / "vat"):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import grass_geometry as gg  # noqa: E402
from vat_writer import NEVER, Block, VatPlant, write_vat  # noqa: E402

SPECIES = "grass"
UP: gg.Vec3 = (0.0, 0.0, 1.0)  # normal of a vertex with no face area (unborn or zero-width leaves); oct16 needs one

Triangle = tuple[int, int, int]


class BakeError(ValueError):
    """Raised when the built meshes do not fit one fixed-topology VAT."""


class LeafState(Enum):
    """A lifetime leaf at one stage: not born yet, visible, or collapsed (fade 1 or gone after being seen)."""
    UNBORN = "unborn"
    VISIBLE = "visible"
    COLLAPSED = "collapsed"


def triangulate(faces: Sequence[gg.Quad]) -> tuple[int, ...]:
    """Flat triangle indices: quad (a, b, c, d) becomes (a, b, c), (a, c, d), keeping the generator's winding."""
    return tuple(i for a, b, c, d in faces for i in (a, b, c, a, c, d))


def _sub(a: gg.Vec3, b: gg.Vec3) -> gg.Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: gg.Vec3, b: gg.Vec3) -> gg.Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def area_normal_sums(positions: Sequence[gg.Vec3], faces: Sequence[gg.Quad]) -> tuple[gg.Vec3, ...]:
    """Per vertex, the sum of the vector areas of its incident quads (0.5 (c - a) x (d - b), the winding's side)."""
    sums = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c, d in faces:
        n = _cross(_sub(positions[c], positions[a]), _sub(positions[d], positions[b]))
        for v in (a, b, c, d):
            acc = sums[v]
            acc[0] += 0.5 * n[0]
            acc[1] += 0.5 * n[1]
            acc[2] += 0.5 * n[2]
    return tuple((s[0], s[1], s[2]) for s in sums)


def vertex_normals(positions: Sequence[gg.Vec3], faces: Sequence[gg.Quad]) -> tuple[gg.Vec3, ...]:
    """Area-weighted unit vertex normals on the underside (PlantMesh winding); UP where a vertex has no face area."""
    normals = []
    for s in area_normal_sums(positions, faces):
        length = math.sqrt(s[0] * s[0] + s[1] * s[1] + s[2] * s[2])
        normals.append(UP if length == 0.0 else (s[0] / length, s[1] / length, s[2] / length))
    return tuple(normals)


def vertex_uvs(mesh: gg.PlantMesh) -> tuple[gg.UV, ...]:
    """One UV per vertex from the per-loop face_uvs (u across, v along the whole leaf); raises on a seam."""
    uvs: list[gg.UV | None] = [None] * len(mesh.positions)
    for face, face_uv in zip(mesh.faces, mesh.face_uvs):
        for v, uv in zip(face, face_uv):
            if uvs[v] is None:
                uvs[v] = uv
            elif uvs[v] != uv:
                raise BakeError(f"vertex {v} has two UVs, {uvs[v]} and {uv}; the VAT needs one UV per vertex, so "
                                "the generator's grid must not have a UV seam")
    missing = [v for v, uv in enumerate(uvs) if uv is None]
    if missing:
        raise BakeError(f"vertices {missing[:5]} (of {len(missing)}) are in no face and have no UV")
    return tuple(uv for uv in uvs if uv is not None)


def leaf_states(table: gg.OrganTable, lifetime: Sequence[str]) -> tuple[dict[str, LeafState], ...]:
    """Per stage (sample), the state of every lifetime leaf, matching build_plant's degenerate rule."""
    seen: set[str] = set()
    states = []
    for sample in table.samples:
        seen.update(sample.leaves)
        stage = {}
        for lid in lifetime:
            leaf = sample.leaves.get(lid)
            if leaf is not None and leaf.fade < 1.0:
                stage[lid] = LeafState.VISIBLE
            else:
                stage[lid] = LeafState.COLLAPSED if lid in seen else LeafState.UNBORN
        states.append(stage)
    return tuple(states)


def carry_sources(states: Sequence[dict[str, LeafState]]) -> tuple[dict[str, int], ...]:
    """Per stage, the stage whose data each leaf uses (G39): its own, or for a collapsed leaf the last stage where it
    was visible. A leaf that was never visible before collapsing keeps its own (degenerate) build."""
    last_visible: dict[str, int] = {}
    sources = []
    for k, stage in enumerate(states):
        source = {}
        for lid, state in stage.items():
            if state is LeafState.VISIBLE:
                last_visible[lid] = k
            source[lid] = last_visible.get(lid, k) if state is LeafState.COLLAPSED else k
        sources.append(source)
    return tuple(sources)


def _first_seen(table: gg.OrganTable) -> dict[str, gg.Leaf]:
    first: dict[str, gg.Leaf] = {}
    for sample in table.samples:
        for lid, leaf in sample.leaves.items():
            first.setdefault(lid, leaf)
    return first


def _organ_stage_values(table: gg.OrganTable, k: int, mesh: gg.PlantMesh,
                        first_seen: dict[str, gg.Leaf]) -> dict[str, tuple[float, ...]]:
    """Per leaf at stage k: collar (3), direction (3), arch, sheath_length, emergence, as build_plant used them."""
    sample = table.samples[k]
    emergence = gg.emergence_heights(sample)
    values = {}
    for rng in mesh.leaves:
        leaf = sample.leaves.get(rng.id, first_seen[rng.id])
        degenerate = rng.id not in sample.leaves or leaf.fade >= 1.0
        b = rng.blade[0]
        values[rng.id] = (*mesh.vectors["collar"][b], *mesh.vectors["axis"][b], mesh.scalars["arch"][b],
                          leaf.sheath_length_m, leaf.sheath_length_m if degenerate else emergence[rng.id])
    return values


def _check_fixed_topology(meshes: Sequence[gg.PlantMesh], lifetime: Sequence[str], times: Sequence[float]) -> None:
    first = meshes[0]
    if [r.id for r in first.leaves] != list(lifetime):
        raise BakeError("build_plant did not build the lifetime leaves in lifetime_leaves order")
    for mesh, t in zip(meshes[1:], times[1:]):
        if mesh.faces != first.faces or mesh.leaves != first.leaves:
            raise BakeError(f"the build at t={t} has a different topology from t={times[0]}; build_plant must keep "
                            "a fixed topology over the lifetime list (G28)")
        for name in gg.STATIC_ATTRIBUTES:
            store = mesh.vectors if name in mesh.vectors else mesh.scalars
            if store[name] != (first.vectors if name in first.vectors else first.scalars)[name]:
                raise BakeError(f"static attribute {name!r} differs between t={times[0]} and t={t}; it must be "
                                "fixed for a leaf's lifetime (grass_geometry.STATIC_ATTRIBUTES)")


def _events(e: gg.LeafEvents) -> tuple[float, float, float, float]:
    def at(value: float | None) -> float:
        return NEVER if value is None else value
    return (at(e.senescent_time), e.senescence_duration, at(e.death_time), at(e.collapse_time))


def bake(table: gg.OrganTable, p: gg.GeometryParams) -> VatPlant:
    """The grass VAT of an organ table: every sample a stage, every lifetime leaf an organ (G39 to G47)."""
    lifetime = gg.lifetime_leaves(table)
    times = table.sample_times
    meshes = [gg.build_plant(table, t, p, lifetime) for t in times]
    _check_fixed_topology(meshes, lifetime, times)
    first = meshes[0]
    sources = carry_sources(leaf_states(table, lifetime))
    normals = [vertex_normals(m.positions, m.faces) for m in meshes]
    first_seen = _first_seen(table)
    stage_values = [_organ_stage_values(table, k, m, first_seen) for k, m in enumerate(meshes)]

    positions: list[float] = []
    normal_values: list[float] = []
    organ_stage: list[float] = []
    for source in sources:
        for rng in first.leaves:
            src = source[rng.id]
            span = range(rng.sheath[0], rng.blade[1])
            positions.extend(c for v in span for c in meshes[src].positions[v])
            normal_values.extend(c for v in span for c in normals[src][v])
            organ_stage.extend(stage_values[src][rng.id])

    def organ_stage_block(name: str, start: int, components: int) -> Block:
        width = 9
        values = [organ_stage[i + start + c] for i in range(0, len(organ_stage), width) for c in range(components)]
        return Block(name, "organ", "stage", components, "f32", values)

    blade_starts = [rng.blade[0] for rng in first.leaves]

    def organ_static_block(name: str) -> Block:
        if name in first.vectors:
            values = [c for b in blade_starts for c in first.vectors[name][b]]
            return Block(name, "organ", "static", 3, "f32", values)
        return Block(name, "organ", "static", 1, "f32", [first.scalars[name][b] for b in blade_starts])

    uvs = vertex_uvs(first)
    blocks = [
        Block("position", "vertex", "stage", 3, "f16", positions),
        Block("normal", "vertex", "stage", 3, "oct16", normal_values),
        Block("organ", "vertex", "static", 1, "u32", [int(o) - 1 for o in first.scalars["organ"]]),
        Block("kind", "vertex", "static", 1, "u32", [int(k) for k in first.scalars["kind"]]),
        Block("s", "vertex", "static", 1, "f32", list(first.scalars["s"])),
        Block("uv", "vertex", "static", 2, "f32", [c for uv in uvs for c in uv]),
        *(organ_static_block(n) for n in ("birth", "draw", "phase", "tiller", "tiller_az",
                                          "tiller_base", "tiller_axis", "side")),
        Block("events", "organ", "static", 4, "f32", [c for lid in lifetime for c in _events(table.events[lid])]),
        organ_stage_block("collar", 0, 3),
        organ_stage_block("direction", 3, 3),
        organ_stage_block("arch", 6, 1),
        organ_stage_block("sheath_length", 7, 1),
        organ_stage_block("emergence", 8, 1),
    ]
    return VatPlant(species=SPECIES,
                    species_params={"model": table.model, "seed": table.seed, "params": dict(table.params),
                                    "geometry": dataclasses.asdict(p)},
                    organ_ids=tuple(lifetime), stage_times=times, triangles=triangulate(first.faces),
                    preview_stage=len(times) - 1, blocks=tuple(blocks))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("organs", type=Path, help="organ table (lpy/export_organs.py, schema v3)")
    parser.add_argument("out", type=Path, help="output VAT file (.vat.glb)")
    args = parser.parse_args(argv)
    start = time.perf_counter()
    plant = bake(gg.load_table(args.organs), gg.GeometryParams())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_vat(plant, args.out)
    size = args.out.stat().st_size
    organ = next(b for b in plant.blocks if b.name == "organ")
    print(f"wrote {args.out}: {len(organ.values)} vertices, {len(plant.organ_ids)} organs, "
          f"{len(plant.stage_times)} stages, {len(plant.triangles) // 3} triangles, {size} bytes "
          f"({size / 1e6:.2f} MB) in {time.perf_counter() - start:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

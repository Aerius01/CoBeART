"""Tests of grass_geometry.py against a fresh export of the real grass model (GP Phase 3, WP 3.2)."""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

import grass_geometry as gg
from decline import cross, dir_from, rotate, unit

TOL = 1e-9
P = gg.GeometryParams()


@pytest.fixture(scope="module")
def table(organ_table_path: Path) -> gg.OrganTable:
    return gg.load_table(organ_table_path)


@pytest.fixture(scope="module")
def lifetime(table: gg.OrganTable) -> list[str]:
    return gg.lifetime_leaves(table)


@pytest.fixture(scope="module")
def meshes(table: gg.OrganTable, lifetime: list[str]) -> dict[float, gg.PlantMesh]:
    return {t: gg.build_plant(table, t, P, lifetime) for t in table.sample_times}


def dist(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.dist(a, b)


def sub(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


def add(a: tuple[float, ...], b: tuple[float, ...], k: float) -> tuple[float, float, float]:
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


def neg(a: tuple[float, ...]) -> tuple[float, float, float]:
    return (-a[0], -a[1], -a[2])


def mesh_blade_rows(mesh: gg.PlantMesh, rng: gg.LeafRange) -> list[list[tuple[float, float, float]]]:
    n = P.cross_segments + 1
    pts = mesh.positions[rng.blade[0]:rng.blade[1]]
    return [list(pts[i:i + n]) for i in range(0, len(pts), n)]


def sheath_rings(mesh: gg.PlantMesh, rng: gg.LeafRange) -> list[list[tuple[float, float, float]]]:
    n = P.tube_sides + 1
    pts = mesh.positions[rng.sheath[0]:rng.sheath[1]]
    return [list(pts[i:i + n]) for i in range(0, len(pts), n)]


def ring_radius(ring: list[tuple[float, float, float]], centre: tuple[float, ...], axis: tuple[float, ...]) -> float:
    radial = []
    for q in ring[:-1]:
        d = sub(q, centre)
        a = dot(d, axis)
        radial.append(math.sqrt(max(dot(d, d) - a * a, 0.0)))
    return sum(radial) / len(radial)


def test_topology_identical_across_samples(meshes: dict[float, gg.PlantMesh], lifetime: list[str]) -> None:
    first = next(iter(meshes.values()))
    per_leaf = P.sheath_vertex_count + P.blade_vertex_count
    assert len(first.positions) == per_leaf * len(lifetime)
    for mesh in meshes.values():
        assert len(mesh.positions) == len(first.positions)
        assert mesh.faces == first.faces
        assert mesh.face_uvs == first.face_uvs
        assert mesh.face_materials == first.face_materials
        assert [r.id for r in mesh.leaves] == lifetime


def test_vertex_counts_match_params() -> None:
    assert P.blade_vertex_count == 85
    assert P.sheath_vertex_count == 18


def test_unborn_and_collapsed_leaves_are_degenerate_at_first_attach(
        table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    first_attach: dict[str, tuple[float, float, float]] = {}
    for s in table.samples:
        for leaf in s.leaves.values():
            first_attach.setdefault(leaf.id, leaf.attach)
    seen_unborn = seen_collapsed = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        for rng in mesh.leaves:
            leaf = sample.leaves.get(rng.id)
            unborn, collapsed = leaf is None, leaf is not None and leaf.fade >= 1.0
            pts = mesh.positions[rng.sheath[0]:rng.blade[1]]
            if unborn or collapsed:
                seen_unborn += unborn
                seen_collapsed += collapsed
                assert all(p == first_attach[rng.id] for p in pts), rng.id
            else:
                assert max(dist(p, pts[0]) for p in pts) > 1e-6, rng.id
    assert seen_unborn and seen_collapsed


def test_present_only_build(table: gg.OrganTable, lifetime: list[str]) -> None:
    sample = gg.sample_at(table, 6.0)
    mesh = gg.build_plant(table, 6.0, P)
    assert [r.id for r in mesh.leaves] == list(sample.leaves)
    for rng in mesh.leaves:
        organs = mesh.scalars["organ"][rng.sheath[0]:rng.blade[1]]
        assert set(organs) == {float(lifetime.index(rng.id) + 1)}


def test_midrib_length_and_sheath_top(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    checked = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        for rng in mesh.leaves:
            leaf = sample.leaves.get(rng.id)
            if leaf is None or leaf.fade >= 1.0:
                continue
            tiller = sample.tillers[leaf.tiller]
            top = sheath_rings(mesh, rng)[-1]
            centre = tuple(sum(q[k] for q in top[:-1]) / (len(top) - 1) for k in range(3))
            assert dist(centre, leaf.attach) < 1e-9
            assert abs(dist(leaf.attach, tiller.base) - leaf.sheath_length_m) < 1e-9
            if leaf.unroll >= 1.0 - TOL:
                mid = [row[P.cross_segments // 2] for row in mesh_blade_rows(mesh, rng)]
                assert dist(mid[0], leaf.attach) < 1e-12
                # Rows are one midrib step apart, so the tip row sits at the full blade length.
                length = sum(dist(a, b) for a, b in zip(mid, mid[1:]))
                assert abs(length - leaf.blade_length_m) < 1e-9 * max(1.0, leaf.blade_length_m)
                checked += 1
    assert checked > 50


def test_sheaths_nest_by_age(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    elongation_age = float(table.params["ELONGATION_AGE"])  # type: ignore[arg-type]
    pairs = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        by_id = {r.id: r for r in mesh.leaves}
        for tiller in sample.tillers.values():
            axis = unit(tiller.direction)
            leaves = [lf for lf in sample.leaves.values() if lf.tiller == tiller.tiller and lf.fade < 1.0]
            radii = [ring_radius(sheath_rings(mesh, by_id[lf.id])[0], tiller.base, axis) for lf in leaves]
            for leaf, r in zip(leaves, radii):
                expected = gg.sheath_radius(leaf.age, elongation_age, P)
                assert abs(r - expected) < 1e-9
            for older, younger in zip(radii, radii[1:]):  # leaves are in birth order
                assert older >= younger - 1e-12
                pairs += 1
    assert pairs > 50


def test_plateau_sheath_radius_default() -> None:
    assert 0.002 <= P.sheath_radius_max_m <= 0.0025


def test_unroll_blend_properties() -> None:
    for i in range(21):
        s = i / 20
        assert gg.unroll_blend(s, 0.0, P) == 0.0
        assert gg.unroll_blend(s, 1.0, P) == 1.0
        values = [gg.unroll_blend(s, u / 50, P) for u in range(51)]
        assert all(a <= b + TOL for a, b in zip(values, values[1:]))
    # The rolled region (blend 0) shrinks from the tip down as the leaf unrolls.
    for u in (0.2, 0.4, 0.6):
        rolled = [s / 100 for s in range(101) if gg.unroll_blend(s / 100, u, P) == 0.0]
        assert rolled == [s / 100 for s in range(len(rolled))]
        assert len(rolled) > len([s for s in range(101) if gg.unroll_blend(s / 100, u + 0.1, P) == 0.0])


def synthetic(unroll: float, arch: float = 0.0) -> tuple[gg.Leaf, gg.Tiller]:
    tiller = gg.Tiller(id="T0", tiller=0, rank=0, birth_time=0.0, base=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0),
                       azimuth_rad=0.4, splay_rad=0.0)
    leaf = gg.Leaf(id="L0.1", tiller=0, rank=1, birth_time=0.0, age=1.0, state="growing", attach=(0.0, 0.0, 0.05),
                   direction=dir_from(0.4, 0.3), azimuth_rad=0.4, angle_from_axis_rad=0.3, sheath_length_m=0.05,
                   blade_length_m=0.15, blade_width_m=0.01, unroll=unroll, arch_rad=arch, senescence=0.0, fade=0.0,
                   decline_draw=0.0)
    return leaf, tiller


def test_rolled_blade_is_closed_tube_of_sheath_radius() -> None:
    leaf, tiller = synthetic(unroll=0.0, arch=0.6)
    r0 = 0.002
    side = gg.leaf_side(leaf, tiller)
    path = leaf.attach
    for i, row in enumerate(gg.blade_rows(leaf, side, r0, 0.3, None, P)):
        s = i / P.blade_segments
        d = unit(rotate(unit(leaf.direction), side, leaf.arch_rad * s * s))
        for q, _, _ in row:
            assert abs(dist(q, path) - r0) < 1e-12
            assert abs(dot(sub(q, path), d)) < 1e-12  # in the plane across the midrib
        gap = dist(row[0][0], row[-1][0])
        assert abs(gap - 2 * r0 * math.sin(math.pi * (1 - P.rolled_turn))) < 1e-12
        path = add(path, d, leaf.blade_length_m / P.blade_segments)


def test_unrolled_blade_has_reference_curl() -> None:
    leaf, tiller = synthetic(unroll=1.0)
    side = gg.leaf_side(leaf, tiller)
    for i, row in enumerate(gg.blade_rows(leaf, side, 0.002, 0.0, None, P)):
        s = i / P.blade_segments
        theta = gg.curl_angle(s, P)
        radius = gg.leaf_width(leaf.blade_width_m, s, P) / theta
        mid = row[P.cross_segments // 2][0]
        assert abs(dist(row[0][0], row[-1][0]) - 2 * radius * math.sin(theta / 2)) < 1e-12
        assert all(abs(dist(q, add(mid, unit(cross(neg(side), unit(leaf.direction))), radius))
                       - radius) < 1e-12 for q, _, _ in row)


def test_reference_profiles() -> None:
    assert gg.curl_angle(0.0, P) == pytest.approx(0.35 + 2.6)
    assert gg.curl_angle(1.0, P) == pytest.approx(0.35)
    widest = max(gg.leaf_width(1.0, i / 100, P) for i in range(101))
    s_widest = max(range(101), key=lambda i: gg.leaf_width(1.0, i / 100, P)) / 100
    assert 0.25 <= s_widest <= 0.4 and widest < 1.0
    assert gg.leaf_width(1.0, 1.0, P) == P.min_width_m


def test_blade_faces_point_to_underside(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    sample = gg.sample_at(table, 12.0)
    mesh = meshes[12.0]
    owner = {}
    for rng in mesh.leaves:
        for v in range(*rng.blade):
            owner[v] = rng.id
    checked = 0
    for face, mat in zip(mesh.faces, mesh.face_materials):
        if mat != gg.MATERIAL_BLADE:
            continue
        leaf = sample.leaves.get(owner[face[0]])
        if leaf is None or leaf.fade >= 1.0 or leaf.unroll < 1.0 - TOL:
            continue
        a, b, c = (mesh.positions[k] for k in face[:3])
        normal = cross(sub(b, a), sub(c, b))
        upper = cross(neg(mesh.vectors["side"][face[0]]), mesh.vectors["axis"][face[0]])
        if math.sqrt(dot(normal, normal)) > 1e-12:
            assert dot(normal, upper) < 0.0
            checked += 1
    assert checked > 100


def test_every_attribute_present(meshes: dict[float, gg.PlantMesh]) -> None:
    mesh = meshes[12.0]
    n = len(mesh.positions)
    assert set(mesh.vectors) == set(gg.VECTOR_ATTRIBUTES)
    assert set(mesh.scalars) == set(gg.SCALAR_ATTRIBUTES)
    assert {gg.gltf_name(k) for k in (*gg.VECTOR_ATTRIBUTES, *gg.SCALAR_ATTRIBUTES)} == {
        "_PIVOT", "_AXIS", "_COLLAR", "_SIDE", "_TILLER_BASE", "_TILLER_AXIS", "_S", "_STEM_T", "_ORGAN", "_PHASE",
        "_KIND", "_TILLER_AZ", "_TILLER", "_BIRTH", "_DRAW", "_SEN", "_FADE", "_ARCH"}
    for values in (*mesh.vectors.values(), *mesh.scalars.values()):
        assert len(values) == n
        assert all(math.isfinite(c) for v in values for c in (v if isinstance(v, tuple) else (v,)))
    assert len(mesh.face_uvs) == len(mesh.faces) == len(mesh.face_materials)
    for rng in mesh.leaves:
        per_leaf = {name: set(mesh.scalars[name][rng.sheath[0]:rng.blade[1]])
                    for name in ("organ", "phase", "birth", "draw", "sen", "fade", "arch", "tiller", "tiller_az")}
        assert all(len(v) == 1 for v in per_leaf.values()), rng.id
        assert set(mesh.scalars["kind"][rng.sheath[0]:rng.sheath[1]]) == {gg.KIND_SHEATH}
        assert set(mesh.scalars["kind"][rng.blade[0]:rng.blade[1]]) == {gg.KIND_BLADE}
        assert set(mesh.vectors["collar"][rng.sheath[0]:rng.blade[1]]) == {mesh.vectors["collar"][rng.blade[0]]}
        assert set(mesh.vectors["pivot"][rng.blade[0]:rng.blade[1]]) == {mesh.vectors["collar"][rng.blade[0]]}
        assert set(mesh.vectors["pivot"][rng.sheath[0]:rng.sheath[1]]) == {mesh.vectors["tiller_base"][rng.sheath[0]]}


def test_attributes_follow_the_table(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    sample = gg.sample_at(table, 8.0)
    mesh = meshes[8.0]
    for rng in mesh.leaves:
        leaf = sample.leaves.get(rng.id)
        if leaf is None:
            continue
        v = rng.blade[0]
        tiller = sample.tillers[leaf.tiller]
        assert mesh.vectors["collar"][v] == leaf.attach
        assert mesh.vectors["tiller_base"][v] == tiller.base
        assert mesh.scalars["birth"][v] == leaf.birth_time
        assert mesh.scalars["draw"][v] == leaf.decline_draw
        assert mesh.scalars["sen"][v] == leaf.senescence
        assert mesh.scalars["fade"][v] == leaf.fade
        assert mesh.scalars["arch"][v] == leaf.arch_rad
        assert mesh.scalars["tiller"][v] == float(leaf.tiller)
        assert mesh.scalars["tiller_az"][v] == tiller.azimuth_rad
        assert abs(dot(mesh.vectors["side"][v], leaf.direction)) < 1e-9
        assert abs(dot(mesh.vectors["side"][v], unit(tiller.direction))) < 1e-9


def test_deterministic(table: gg.OrganTable, lifetime: list[str], meshes: dict[float, gg.PlantMesh]) -> None:
    assert gg.build_plant(table, 12.0, P, lifetime) == meshes[12.0]


def test_resolution_parameters(table: gg.OrganTable, lifetime: list[str]) -> None:
    low = replace(P, blade_segments=8, cross_segments=2, tube_sides=6)
    mesh = gg.build_plant(table, 12.0, low, lifetime)
    assert len(mesh.positions) == (9 * 3 + 2 * 7) * len(lifetime)


def test_errors(table: gg.OrganTable) -> None:
    with pytest.raises(gg.OrganTableError, match=r"nearest samples: \[6.0, 6.5\]"):
        gg.build_plant(table, 6.2, P)
    with pytest.raises(gg.OrganTableError, match="not in the organ table"):
        gg.build_plant(table, 6.0, P, ["L9.9"])
    with pytest.raises(gg.OrganTableError, match="schema_version must be 2"):
        gg.parse_table({"schema_version": 1})
    with pytest.raises(ValueError, match="tube_rings"):
        gg.GeometryParams(tube_rings=1)

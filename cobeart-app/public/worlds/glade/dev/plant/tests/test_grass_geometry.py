"""Tests of grass_geometry.py against a fresh export of the real grass model (GP Phase 3, WP 3.2)."""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

import grass_geometry as gg
from decline import DeclineParams, cross, declined_vertex, dir_from, health, look, rotate, unit

TOL = 1e-9
P = gg.GeometryParams()
FRACTIONS = gg.blade_row_fractions(P)


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


def rows_of(mesh: gg.PlantMesh, start: int, stop: int) -> list[list[tuple[float, float, float]]]:
    n = P.cross_segments + 1
    pts = mesh.positions[start:stop]
    return [list(pts[i:i + n]) for i in range(0, len(pts), n)]


def mesh_blade_rows(mesh: gg.PlantMesh, rng: gg.LeafRange) -> list[list[tuple[float, float, float]]]:
    return rows_of(mesh, *rng.blade)


def sheath_rows(mesh: gg.PlantMesh, rng: gg.LeafRange) -> list[list[tuple[float, float, float]]]:
    return rows_of(mesh, *rng.sheath)


def radial(q: tuple[float, ...], base: tuple[float, ...], axis: tuple[float, ...]) -> tuple[float, float]:
    """Height along the axis from base, and distance from the axis line."""
    d = sub(q, base)
    h = dot(d, axis)
    return h, math.sqrt(max(dot(d, d) - h * h, 0.0))


def out_dir(leaf: gg.Leaf, tiller: gg.Tiller) -> tuple[float, float, float]:
    return cross(gg.leaf_side(leaf, tiller), unit(tiller.direction))


def enclosed_length(leaf: gg.Leaf, sample: gg.Sample) -> float:
    """How far the leaf's blade runs inside older sheaths before it emerges (G36), capped at its length."""
    return min(gg.emergence_heights(sample)[leaf.id] - leaf.sheath_length_m, leaf.blade_length_m)


def present(sample: gg.Sample, mesh: gg.PlantMesh) -> list[tuple[gg.Leaf, gg.Tiller, gg.LeafRange]]:
    """The visible (not collapsed) leaves of a sample with their tiller and vertex ranges."""
    out = []
    for rng in mesh.leaves:
        leaf = sample.leaves.get(rng.id)
        if leaf is not None and leaf.fade < 1.0 and leaf.blade_length_m > 0.0:
            out.append((leaf, sample.tillers[leaf.tiller], rng))
    return out


def test_topology_identical_across_samples(meshes: dict[float, gg.PlantMesh], lifetime: list[str]) -> None:
    first = next(iter(meshes.values()))
    assert len(first.positions) == P.leaf_vertex_count * len(lifetime)
    for mesh in meshes.values():
        assert len(mesh.positions) == len(first.positions)
        assert mesh.faces == first.faces
        assert mesh.face_uvs == first.face_uvs
        assert mesh.face_materials == first.face_materials
        assert [r.id for r in mesh.leaves] == lifetime


def test_vertex_counts_match_params() -> None:
    assert P.blade_vertex_count == 17 * 9
    assert P.sheath_vertex_count == 3 * 9
    assert P.leaf_vertex_count == 180


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
                anchor = first_attach[rng.id]
                assert all(p == anchor for p in pts), rng.id
                assert set(mesh.scalars["fade"][rng.sheath[0]:rng.blade[1]]) == {1.0}, rng.id
                assert set(mesh.vectors["collar"][rng.sheath[0]:rng.blade[1]]) == {anchor}, rng.id
                assert set(mesh.vectors["pivot"][rng.blade[0]:rng.blade[1]]) == {anchor}, rng.id
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


def test_one_surface_per_leaf(meshes: dict[float, gg.PlantMesh]) -> None:
    """The sheath's top faces use the collar row (the blade's first row): no seam and no duplicated ring."""
    mesh = meshes[12.0]
    n = P.cross_segments + 1
    for rng in mesh.leaves:
        last_sheath = set(range(rng.sheath[1] - n, rng.sheath[1]))
        collar_row = set(range(rng.blade[0], rng.blade[0] + n))
        band = [(f, m) for f, m in zip(mesh.faces, mesh.face_materials)
                if set(f) & last_sheath and set(f) & collar_row]
        assert len(band) == P.cross_segments, rng.id
        assert {m for _, m in band} == {gg.MATERIAL_SHEATH}
        leaf_faces = [m for f, m in zip(mesh.faces, mesh.face_materials) if rng.sheath[0] <= f[0] < rng.blade[1]]
        assert leaf_faces.count(gg.MATERIAL_SHEATH) == P.sheath_rows * P.cross_segments
        assert leaf_faces.count(gg.MATERIAL_BLADE) == P.blade_segments * P.cross_segments


def test_first_blade_row_is_the_wrap_at_the_emergence_point(
        table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    elongation_age = float(table.params["ELONGATION_AGE"])  # type: ignore[arg-type]
    checked = enclosed = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        for leaf, tiller, rng in present(sample, mesh):
            axis = unit(tiller.direction)
            rc = gg.sheath_radius(leaf.age, elongation_age, P)
            e = enclosed_length(leaf, sample)
            enclosed += e > 1e-6
            first = mesh_blade_rows(mesh, rng)[0]
            for q in first:
                h, r = radial(q, tiller.base, axis)
                assert abs(h - (leaf.sheath_length_m + e)) < 1e-9 and abs(r - rc) < 1e-9, leaf.id
            emergence = add(leaf.attach, axis, e)
            wall = add(emergence, out_dir(leaf, tiller), rc)  # the midrib start on the wrap wall (G37)
            assert dist(first[P.cross_segments // 2], wall) < 1e-12
            assert dist(mesh.vectors["collar"][rng.blade[0]], wall) < 1e-12
            assert dist(mesh.vectors["pivot"][rng.blade[0]], wall) < 1e-12
            theta = gg.wrap_angle(rc, leaf.blade_width_m, P)
            assert abs(dist(first[0], first[-1]) - 2 * rc * math.sin(theta / 2)) < 1e-9
            checked += 1
    assert checked > 100 and enclosed > 20


def test_midrib_leaves_along_the_axis_and_reaches_its_direction(
        table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    checked = 0
    for sample in table.samples:
        for leaf, tiller, rng in present(sample, meshes[sample.time]):
            free = leaf.blade_length_m - enclosed_length(leaf, sample)
            if free < 1e-6:
                continue
            mid = [row[P.cross_segments // 2] for row in mesh_blade_rows(meshes[sample.time], rng)]
            steps = [unit(sub(b, a)) for a, b in zip(mid, mid[1:])]
            axis = unit(tiller.direction)
            side = gg.leaf_side(leaf, tiller)
            s0 = 0.5 * FRACTIONS[1]
            first_bound = leaf.angle_from_axis_rad * gg.smoothstep(s0 / P.collar_bend) + abs(leaf.arch_rad) * s0 * s0
            assert math.acos(min(1.0, dot(steps[0], axis))) <= first_bound + 1e-9, leaf.id
            for i, d in enumerate(steps):
                s_mid = 0.5 * (FRACTIONS[i] + FRACTIONS[i + 1])
                if s_mid >= P.collar_bend:
                    assert dist(d, rotate(leaf.direction, side, leaf.arch_rad * s_mid * s_mid)) < 1e-9, leaf.id
            # Each row step is its share of the free blade, so the tip sits at the free length.
            for i, (a, b) in enumerate(zip(mid, mid[1:])):
                assert abs(dist(a, b) - free * (FRACTIONS[i + 1] - FRACTIONS[i])) < 1e-12
            checked += 1
    assert checked > 100


def test_blade_rows_are_dense_near_emergence(meshes: dict[float, gg.PlantMesh]) -> None:
    assert FRACTIONS[0] == 0.0 and FRACTIONS[-1] == 1.0
    assert all(abs(f - (i / P.blade_segments) ** P.blade_row_power) < 1e-15 for i, f in enumerate(FRACTIONS))
    rows_in_bend = sum(1 for f in FRACTIONS if f <= P.collar_bend)
    even_rows_in_bend = sum(1 for i in range(P.blade_segments + 1) if i / P.blade_segments <= P.collar_bend)
    assert rows_in_bend >= 5 and even_rows_in_bend == 3
    mesh = meshes[12.0]
    n = P.cross_segments + 1
    for rng in mesh.leaves:
        blade_s = mesh.scalars["s"][rng.blade[0]:rng.blade[1]]
        assert [blade_s[i * n] for i in range(P.blade_segments + 1)] == list(FRACTIONS)


def test_enclosed_blades_stay_inside_the_enclosing_wraps(table: gg.OrganTable,
                                                         meshes: dict[float, gg.PlantMesh]) -> None:
    """Until its midrib passes the emergence point, no blade vertex reaches past the wraps of the older leaves around
    it (G36), both as built and as drawn by declined_vertex at v = 1 (turnover senescence shrinks some enclosing
    leaves). Rows after that have emerged; a drooping blade may hang below an older collar outside its sheath."""
    n = P.cross_segments + 1
    elongation_age = float(table.params["ELONGATION_AGE"])  # type: ignore[arg-type]
    decline = DeclineParams.from_mapping(table.params)
    worst = -1.0
    checked = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        visible = present(sample, mesh)
        for leaf, tiller, rng in visible:
            if enclosed_length(leaf, sample) <= 1e-6:
                continue
            axis = unit(tiller.direction)
            for declined in (False, True):
                def scale(lf: gg.Leaf) -> float:
                    if not declined:
                        return 1.0
                    return look(health(lf.senescence, 1.0, lf.age, lf.decline_draw, decline), decline).size_scale
                emerge_h = scale(leaf) * gg.emergence_heights(sample)[leaf.id]
                wraps = [(scale(o) * o.sheath_length_m, scale(o) * gg.sheath_radius(o.age, elongation_age, P))
                         for o, tl, _ in visible
                         if tl.tiller == leaf.tiller and o.birth_time < leaf.birth_time and o.fade < 1.0]
                for row_start in range(rng.blade[0], rng.blade[1], n):
                    row = []
                    for i in range(row_start, row_start + n):
                        q = mesh.positions[i]
                        if declined:
                            q = declined_vertex(q, gg.vertex_inputs(mesh, i), sample.time, 1.0, decline,
                                                collar_bend=P.collar_bend).position
                        row.append(radial(q, tiller.base, axis))
                    if row[P.cross_segments // 2][0] > emerge_h + 1e-9:
                        break  # the midrib has emerged
                    for h, r in row:
                        around = [rk for hk, rk in wraps if hk >= h]
                        if around:
                            worst = max(worst, r - min(around))
                            assert r <= min(around) + 1e-9, (sample.time, leaf.id, declined, r, min(around))
                            checked += 1
    assert checked > 1000
    assert worst <= 0.0


def test_emergence_point_eases_down_as_enclosing_leaves_fade(table: gg.OrganTable) -> None:
    """Between samples the emergence height drops only by what the fading weights of the enclosing leaves allow."""
    for s0, s1 in zip(table.samples, table.samples[1:]):
        h0, h1 = gg.emergence_heights(s0), gg.emergence_heights(s1)
        for lid, leaf0 in s0.leaves.items():
            leaf1 = s1.leaves.get(lid)
            if leaf1 is None or leaf0.fade >= 1.0 or leaf1.fade >= 1.0:
                continue
            allowed = 0.0
            for older in s0.leaves.values():
                if older.tiller != leaf0.tiller or older.birth_time >= leaf0.birth_time or older.fade >= 1.0:
                    continue
                after = s1.leaves.get(older.id)
                w1 = 1.0 - after.fade if after is not None and after.fade < 1.0 else 0.0
                allowed += max(older.sheath_length_m - leaf0.sheath_length_m, 0.0) * max((1.0 - older.fade) - w1, 0.0)
            assert h0[lid] - h1[lid] <= allowed + 1e-9, (s0.time, lid, h0[lid] - h1[lid], allowed)


def test_split_wraps_never_exceed_the_width_cap(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    split = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        for leaf, tiller, rng in present(sample, mesh):
            cap = P.sheath_width_factor * leaf.blade_width_m
            for row in (*sheath_rows(mesh, rng), *mesh_blade_rows(mesh, rng)):
                arc = sum(dist(a, b) for a, b in zip(row, row[1:]))
                assert arc <= cap + 1e-9, (sample.time, leaf.id, arc, cap)
            rc = gg.sheath_radius(leaf.age, float(table.params["ELONGATION_AGE"]), P)  # type: ignore[arg-type]
            split += gg.wrap_angle(rc, leaf.blade_width_m, P) < 2 * math.pi * P.rolled_turn
    assert split > 20
    assert gg.wrap_angle(0.002, 0.01, P) == pytest.approx(2 * math.pi * P.rolled_turn)
    assert gg.wrap_angle(0.002, 0.003, P) == pytest.approx(P.sheath_width_factor * 0.003 / 0.002)


def test_sheath_wraps_nest_by_age(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    elongation_age = float(table.params["ELONGATION_AGE"])  # type: ignore[arg-type]
    pairs = 0
    for sample in table.samples:
        mesh = meshes[sample.time]
        visible = present(sample, mesh)
        for tiller in sample.tillers.values():
            axis = unit(tiller.direction)
            leaves = [(lf, rng) for lf, tl, rng in visible if tl.tiller == tiller.tiller]
            radii = []
            for leaf, rng in leaves:
                rc = gg.sheath_radius(leaf.age, elongation_age, P)
                for k, row in enumerate(sheath_rows(mesh, rng)):
                    f = k / P.sheath_rows
                    for q in row:
                        h, r = radial(q, tiller.base, axis)
                        assert abs(h - leaf.sheath_length_m * f) < 1e-9
                        assert abs(r - rc * gg.lerp(P.sheath_taper, 1.0, f)) < 1e-9
                radii.append(rc)
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


def tangent(leaf: gg.Leaf, tiller: gg.Tiller, s: float) -> tuple[float, float, float]:
    return gg.midrib_direction(unit(tiller.direction), gg.leaf_side(leaf, tiller), leaf.angle_from_axis_rad,
                               leaf.arch_rad, s, P)


def test_rolled_blade_stays_a_wrap_of_the_collar_radius() -> None:
    leaf, tiller = synthetic(unroll=0.0, arch=0.6)
    rc = 0.002
    rows = gg.leaf_rows(leaf, tiller, rc, 0.3, P).blade
    for s, row in zip(FRACTIONS, rows):
        d = tangent(leaf, tiller, s)
        mid = row[P.cross_segments // 2]
        wside = rotate(neg(gg.leaf_side(leaf, tiller)), d, 0.3 * s)
        centre = add(mid, unit(cross(wside, d)), rc)
        for q in row:
            assert abs(dist(q, centre) - rc) < 1e-12
            assert abs(dot(sub(q, mid), d)) < 1e-12  # in the plane across the midrib
        assert abs(dist(row[0], row[-1]) - 2 * rc * math.sin(math.pi * (1 - P.rolled_turn))) < 1e-12


def exact_tip(leaf: gg.Leaf, tiller: gg.Tiller, start: tuple[float, ...], steps: int = 20000) -> tuple[float, ...]:
    """The midrib tip integrated finely (midpoint rule with many steps), the reference for the mesh's 16 rows."""
    path = start
    for i in range(steps):
        path = add(path, tangent(leaf, tiller, (i + 0.5) / steps), leaf.blade_length_m / steps)
    return path


def test_midpoint_midrib_close_to_exact() -> None:
    leaf, tiller = synthetic(unroll=1.0, arch=1.7)
    leaf = replace(leaf, blade_length_m=0.2)
    rows = gg.leaf_rows(leaf, tiller, 0.002, 0.0, P).blade
    tip = rows[-1][P.cross_segments // 2]
    assert dist(tip, exact_tip(leaf, tiller, rows[0][P.cross_segments // 2])) < 0.002 * leaf.blade_length_m


def test_emergence_keeps_the_enclosed_blade_on_the_axis() -> None:
    leaf, tiller = synthetic(unroll=1.0, arch=0.6)
    rc = 0.002
    rows = gg.leaf_rows(leaf, tiller, rc, 0.0, P, emergence_m=leaf.sheath_length_m + 0.04).blade
    first = rows[0][P.cross_segments // 2]
    assert dist(first, add(add(leaf.attach, (0.0, 0.0, 1.0), 0.04), out_dir(leaf, tiller), rc)) < 1e-12
    tip = rows[-1][P.cross_segments // 2]
    free = replace(leaf, blade_length_m=leaf.blade_length_m - 0.04)
    assert dist(tip, exact_tip(free, tiller, first)) < 0.002 * leaf.blade_length_m
    whole = gg.leaf_rows(leaf, tiller, rc, 0.0, P, emergence_m=leaf.sheath_length_m + 1.0).blade
    assert all(row == whole[0] for row in whole)  # still fully inside: zero area at the blade's tip height
    assert all(abs(q[2] - (leaf.attach[2] + leaf.blade_length_m)) < 1e-12 for q in whole[0])


def test_unrolled_blade_has_reference_curl_beyond_the_collar_opening() -> None:
    leaf, tiller = synthetic(unroll=1.0)
    rows = gg.leaf_rows(leaf, tiller, 0.002, 0.0, P).blade
    checked = 0
    for s, row in zip(FRACTIONS, rows):
        if s < P.collar_open:
            continue
        theta = gg.curl_angle(s, P)
        radius = gg.leaf_width(leaf.blade_width_m, s, P) / theta
        mid = row[P.cross_segments // 2]
        upper = unit(cross(neg(gg.leaf_side(leaf, tiller)), tangent(leaf, tiller, s)))
        assert abs(dist(row[0], row[-1]) - 2 * radius * math.sin(theta / 2)) < 1e-12
        assert all(abs(dist(q, add(mid, upper, radius)) - radius) < 1e-12 for q in row)
        checked += 1
    assert checked >= 10


def test_collar_opening_blend() -> None:
    for u in (0.0, 0.3, 1.0):
        assert gg.collar_open_blend(0.0, u, P) == 0.0
    assert gg.collar_open_blend(P.collar_open, 1.0, P) == 1.0
    values = [gg.collar_open_blend(i / 100, 1.0, P) for i in range(101)]
    assert all(a <= b + TOL for a, b in zip(values, values[1:]))
    assert all(gg.collar_open_blend(i / 100, 0.0, P) == 0.0 for i in range(101))


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
        if leaf is None or leaf.fade >= 1.0 or leaf.unroll < 1.0 - TOL or mesh.scalars["s"][face[0]] < 0.25:
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
        assert set(mesh.scalars["kind"][rng.sheath[0]:rng.sheath[1]]) == {float(gg.KIND_SHEATH)}
        assert set(mesh.scalars["kind"][rng.blade[0]:rng.blade[1]]) == {float(gg.KIND_BLADE)}
        assert set(mesh.vectors["collar"][rng.sheath[0]:rng.blade[1]]) == {mesh.vectors["collar"][rng.blade[0]]}
        assert set(mesh.vectors["pivot"][rng.blade[0]:rng.blade[1]]) == {mesh.vectors["collar"][rng.blade[0]]}
        assert set(mesh.vectors["pivot"][rng.sheath[0]:rng.sheath[1]]) == {mesh.vectors["tiller_base"][rng.sheath[0]]}


def test_attributes_follow_the_table(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    sample = gg.sample_at(table, 8.0)
    mesh = meshes[8.0]
    for rng in mesh.leaves:
        leaf = sample.leaves.get(rng.id)
        if leaf is None or leaf.fade >= 1.0:
            continue
        v = rng.blade[0]
        tiller = sample.tillers[leaf.tiller]
        emergence = add(leaf.attach, unit(tiller.direction), enclosed_length(leaf, sample))
        rc = gg.sheath_radius(leaf.age, float(table.params["ELONGATION_AGE"]), P)  # type: ignore[arg-type]
        wall = add(emergence, out_dir(leaf, tiller), rc)
        assert dist(mesh.vectors["collar"][v], wall) < 1e-12
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


def test_attribute_split() -> None:
    names = (*gg.VECTOR_ATTRIBUTES, *gg.SCALAR_ATTRIBUTES)
    assert set(gg.STAGE_ATTRIBUTES) | set(gg.STATIC_ATTRIBUTES) == set(names)
    assert not set(gg.STAGE_ATTRIBUTES) & set(gg.STATIC_ATTRIBUTES)
    assert set(gg.STAGE_ATTRIBUTES) == {"pivot", "axis", "collar", "stem_t", "arch", "sen", "fade"}
    assert "s" in gg.STATIC_ATTRIBUTES


def test_static_attributes_identical_across_samples(meshes: dict[float, gg.PlantMesh]) -> None:
    first = next(iter(meshes.values()))
    for mesh in meshes.values():
        for name in gg.STATIC_ATTRIBUTES:
            values = mesh.vectors[name] if name in gg.VECTOR_ATTRIBUTES else mesh.scalars[name]
            first_values = first.vectors[name] if name in gg.VECTOR_ATTRIBUTES else first.scalars[name]
            assert values == first_values, name


def test_declined_vertex_runs_over_lifetime_builds(table: gg.OrganTable,
                                                   meshes: dict[float, gg.PlantMesh]) -> None:
    decline = DeclineParams.from_mapping(table.params)
    for time, mesh in meshes.items():
        for i, position in enumerate(mesh.positions):
            vin = gg.vertex_inputs(mesh, i)
            for v in (1.0, 0.5, 0.1):
                dv = declined_vertex(position, vin, time, v, decline, collar_bend=P.collar_bend)
                if v == 1.0 and vin.senescence == 0.0:
                    assert dist(dv.position, position) < 1e-12, (time, i)
            if vin.fade >= 1.0:
                assert declined_vertex(position, vin, time, 1.0, decline, collar_bend=P.collar_bend).opacity == 0.0


def test_vertex_inputs_rejects_non_integral(meshes: dict[float, gg.PlantMesh]) -> None:
    mesh = meshes[12.0]
    for name in ("kind", "tiller"):
        values = list(mesh.scalars[name])
        values[0] = 0.5
        bad = replace(mesh, scalars={**mesh.scalars, name: tuple(values)})
        with pytest.raises(ValueError, match=f"vertex 0: attribute '{name}' must be integral"):
            gg.vertex_inputs(bad, 0)


def test_deterministic(table: gg.OrganTable, lifetime: list[str], meshes: dict[float, gg.PlantMesh]) -> None:
    assert gg.build_plant(table, 12.0, P, lifetime) == meshes[12.0]


def test_resolution_parameters(table: gg.OrganTable, lifetime: list[str]) -> None:
    low = replace(P, blade_segments=8, cross_segments=2, sheath_rows=2)
    mesh = gg.build_plant(table, 12.0, low, lifetime)
    assert len(mesh.positions) == (9 + 2) * 3 * len(lifetime)


def test_errors(table: gg.OrganTable) -> None:
    with pytest.raises(gg.OrganTableError, match=r"nearest samples: \[6.0, 6.5\]"):
        gg.build_plant(table, 6.2, P)
    with pytest.raises(gg.OrganTableError, match="not in the organ table"):
        gg.build_plant(table, 6.0, P, ["L9.9"])
    with pytest.raises(gg.OrganTableError, match="schema_version must be 3"):
        gg.parse_table({"schema_version": 1})
    with pytest.raises(ValueError, match="sheath_rows"):
        gg.GeometryParams(sheath_rows=0)
    with pytest.raises(ValueError, match="collar_bend"):
        gg.GeometryParams(collar_bend=0.0)
    with pytest.raises(ValueError, match="sheath_taper"):
        gg.GeometryParams(sheath_taper=0.9)
    with pytest.raises(ValueError, match="blade_row_power"):
        gg.GeometryParams(blade_row_power=0.0)


def test_loader_errors(organ_table_path: Path) -> None:
    import json
    doc = json.loads(organ_table_path.read_text(encoding="utf-8"))

    def broken(edit: object) -> dict[str, object]:
        d = json.loads(json.dumps(doc))
        edit(d)  # type: ignore[operator]
        return d

    with pytest.raises(gg.OrganTableError, match="each organ table sample must be an object"):
        gg.parse_table(broken(lambda d: d["samples"].__setitem__(0, 3)))
    with pytest.raises(gg.OrganTableError, match="each organ must be an object"):
        gg.parse_table(broken(lambda d: d["samples"][0]["organs"].__setitem__(0, "x")))

    def tiller_organ(d: dict[str, object]) -> dict[str, object]:
        return next(o for o in d["samples"][0]["organs"] if o["kind"] == "tiller")  # type: ignore[index]

    with pytest.raises(gg.OrganTableError, match=r"organ 'T0' at t=0.0: field 'base' is missing"):
        gg.parse_table(broken(lambda d: tiller_organ(d).pop("base")))
    with pytest.raises(gg.OrganTableError, match="tiller must be an integer"):
        gg.parse_table(broken(lambda d: tiller_organ(d).__setitem__("tiller", 0.5)))
    last = doc["samples"][-1]["organs"]
    leaf_index = next(i for i, o in enumerate(last) if o["kind"] == "leaf")
    with pytest.raises(gg.OrganTableError, match="rank must be an integer"):
        gg.parse_table(broken(lambda d: d["samples"][-1]["organs"][leaf_index].__setitem__("rank", 1.5)))


def test_events_follow_the_table(table: gg.OrganTable, lifetime: list[str]) -> None:
    """Every lifetime leaf has its events, and the G38 closed forms give its senescence and fade at every sample."""
    assert list(table.events) == lifetime
    fade_duration = float(table.params["FADE_DURATION"])  # type: ignore[arg-type]
    for sample in table.samples:
        for leaf in sample.leaves.values():
            e = table.events[leaf.id]
            sen = 0.0 if e.senescent_time is None else min(max(
                (sample.time - e.senescent_time) / e.senescence_duration, 0.0), 1.0)
            fade = 0.0 if e.death_time is None else min(max((sample.time - e.death_time) / fade_duration, 0.0), 1.0)
            assert abs(sen - leaf.senescence) <= 1e-6, (leaf.id, sample.time)
            assert abs(fade - leaf.fade) <= 1e-6, (leaf.id, sample.time)
            assert (leaf.fade == 1.0) == (e.collapse_time is not None and e.collapse_time <= sample.time)
    assert any(e.collapse_time is not None for e in table.events.values())
    assert any(e.senescent_time is None for e in table.events.values())


def test_event_loader_errors(organ_table_path: Path) -> None:
    import json
    doc = json.loads(organ_table_path.read_text(encoding="utf-8"))
    leaf_id = next(iter(doc["organ_events"]))

    def broken(edit: object) -> dict[str, object]:
        d = json.loads(json.dumps(doc))
        edit(d)  # type: ignore[operator]
        return d

    with pytest.raises(gg.OrganTableError, match="field 'organ_events' is missing"):
        gg.parse_table(broken(lambda d: d.pop("organ_events")))
    with pytest.raises(gg.OrganTableError, match=rf"missing \['{leaf_id}'\]"):
        gg.parse_table(broken(lambda d: d["organ_events"].pop(leaf_id)))
    with pytest.raises(gg.OrganTableError, match=r"not in any sample \['L9\.9'\]"):
        gg.parse_table(broken(lambda d: d["organ_events"].__setitem__("L9.9", d["organ_events"][leaf_id])))
    with pytest.raises(gg.OrganTableError, match=rf"organ_events '{leaf_id}': missing field\(s\) \['death_time'\]"):
        gg.parse_table(broken(lambda d: d["organ_events"][leaf_id].pop("death_time")))
    with pytest.raises(gg.OrganTableError, match=rf"organ_events '{leaf_id}': death_time must be a finite number"):
        gg.parse_table(broken(lambda d: d["organ_events"][leaf_id].__setitem__("death_time", "soon")))
    with pytest.raises(gg.OrganTableError, match=rf"organ_events '{leaf_id}': collapse_time must be >= 0"):
        gg.parse_table(broken(lambda d: d["organ_events"][leaf_id].__setitem__("collapse_time", -1.0)))
    with pytest.raises(gg.OrganTableError, match=rf"organ_events '{leaf_id}': senescence_duration must be a finite"):
        gg.parse_table(broken(lambda d: d["organ_events"][leaf_id].__setitem__("senescence_duration", None)))
    with pytest.raises(gg.OrganTableError, match=rf"organ_events '{leaf_id}': senescence_duration must be positive"):
        gg.parse_table(broken(lambda d: d["organ_events"][leaf_id].__setitem__("senescence_duration", 0.0)))

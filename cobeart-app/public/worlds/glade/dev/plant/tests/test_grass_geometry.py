"""Tests of grass_geometry.py against a fresh export of the real grass model (GP Phase 3, WP 3.2)."""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

import grass_geometry as gg
from decline import DeclineParams, cross, declined_vertex, dir_from, rotate, unit

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


def test_collar_row_is_the_wrap_at_the_collar(table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    elongation_age = float(table.params["ELONGATION_AGE"])  # type: ignore[arg-type]
    checked = 0
    for sample in table.samples:
        for leaf, tiller, rng in present(sample, meshes[sample.time]):
            axis = unit(tiller.direction)
            rc = gg.sheath_radius(leaf.age, elongation_age, P)
            collar_row = mesh_blade_rows(meshes[sample.time], rng)[0]
            for q in collar_row:
                h, r = radial(q, tiller.base, axis)
                assert abs(h - leaf.sheath_length_m) < 1e-9 and abs(r - rc) < 1e-9, leaf.id
            midrib = collar_row[P.cross_segments // 2]
            assert dist(midrib, add(leaf.attach, out_dir(leaf, tiller), rc)) < 1e-12
            gap = dist(collar_row[0], collar_row[-1])
            assert abs(gap - 2 * rc * math.sin(math.pi * (1 - P.rolled_turn))) < 1e-9
            checked += 1
    assert checked > 100


def test_midrib_leaves_along_the_axis_and_reaches_its_direction(
        table: gg.OrganTable, meshes: dict[float, gg.PlantMesh]) -> None:
    checked = 0
    for sample in table.samples:
        for leaf, tiller, rng in present(sample, meshes[sample.time]):
            mid = [row[P.cross_segments // 2] for row in mesh_blade_rows(meshes[sample.time], rng)]
            steps = [unit(sub(b, a)) for a, b in zip(mid, mid[1:])]
            axis = unit(tiller.direction)
            side = gg.leaf_side(leaf, tiller)
            s0 = 0.5 / P.blade_segments
            first_bound = leaf.angle_from_axis_rad * gg.smoothstep(s0 / P.collar_bend) + abs(leaf.arch_rad) * s0 * s0
            assert math.acos(min(1.0, dot(steps[0], axis))) <= first_bound + 1e-9, leaf.id
            for i, d in enumerate(steps):
                s_mid = (i + 0.5) / P.blade_segments
                if s_mid >= P.collar_bend:
                    assert dist(d, rotate(leaf.direction, side, leaf.arch_rad * s_mid * s_mid)) < 1e-9, leaf.id
            # Rows are one midrib step apart, so the tip row sits at the full blade length.
            length = sum(dist(a, b) for a, b in zip(mid, mid[1:]))
            assert abs(length - leaf.blade_length_m) < 1e-9 * max(1.0, leaf.blade_length_m)
            checked += 1
    assert checked > 100


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
    for i, row in enumerate(rows):
        s = i / P.blade_segments
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


def test_unrolled_blade_has_reference_curl_beyond_the_collar_opening() -> None:
    leaf, tiller = synthetic(unroll=1.0)
    rows = gg.leaf_rows(leaf, tiller, 0.002, 0.0, P).blade
    checked = 0
    for i, row in enumerate(rows):
        s = i / P.blade_segments
        if s < P.collar_open:
            continue
        theta = gg.curl_angle(s, P)
        radius = gg.leaf_width(leaf.blade_width_m, s, P) / theta
        mid = row[P.cross_segments // 2]
        upper = unit(cross(neg(gg.leaf_side(leaf, tiller)), tangent(leaf, tiller, s)))
        assert abs(dist(row[0], row[-1]) - 2 * radius * math.sin(theta / 2)) < 1e-12
        assert all(abs(dist(q, add(mid, upper, radius)) - radius) < 1e-12 for q in row)
        checked += 1
    assert checked >= 12


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


def test_attribute_split() -> None:
    names = (*gg.VECTOR_ATTRIBUTES, *gg.SCALAR_ATTRIBUTES)
    assert set(gg.STAGE_ATTRIBUTES) | set(gg.STATIC_ATTRIBUTES) == set(names)
    assert not set(gg.STAGE_ATTRIBUTES) & set(gg.STATIC_ATTRIBUTES)
    assert set(gg.STAGE_ATTRIBUTES) == {"pivot", "axis", "collar", "stem_t", "arch", "sen", "fade"}


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
    with pytest.raises(gg.OrganTableError, match="schema_version must be 2"):
        gg.parse_table({"schema_version": 1})
    with pytest.raises(ValueError, match="sheath_rows"):
        gg.GeometryParams(sheath_rows=0)
    with pytest.raises(ValueError, match="collar_bend"):
        gg.GeometryParams(collar_bend=0.0)
    with pytest.raises(ValueError, match="sheath_taper"):
        gg.GeometryParams(sheath_taper=0.9)


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

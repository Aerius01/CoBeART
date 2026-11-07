"""Mesh-level check of the decline rule (GP Phase 3, WP 3.3): declined_vertex on the real built mesh against a leaf
rebuilt from declined_leaf values, within the G23 bound."""
from __future__ import annotations

import math
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import grass_geometry as gg
from decline import DeclineParams, declined_leaf, declined_vertex, health, look

TIME = 12.0
P = gg.GeometryParams()
COLLAR_REL_TOL = 1e-9
TIP_REL_TOL = 0.02  # G23 bound, relative to the declined blade length, against the rebuilt leaf
VECTOR_TOL = 1e-9
IDENTITY_TOL = 1e-12


@pytest.fixture(scope="module")
def table(organ_table_path: Path) -> gg.OrganTable:
    return gg.load_table(organ_table_path)


@pytest.fixture(scope="module")
def mesh(table: gg.OrganTable) -> gg.PlantMesh:
    return gg.build_plant(table, TIME, P)


@pytest.fixture(scope="module")
def decline_params(table: gg.OrganTable) -> DeclineParams:
    return DeclineParams.from_mapping(table.params)


def test_declined_mesh_matches_rebuilt_leaves(table: gg.OrganTable, mesh: gg.PlantMesh,
                                              decline_params: DeclineParams,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    sample = gg.sample_at(table, TIME)
    emergence = gg.emergence_heights(sample)
    elongation_age = float(table.params["ELONGATION_AGE"])
    row = P.cross_segments + 1
    worst_tip = 0.0
    compared = 0
    enclosed_checked = 0
    for v in (1.0, 0.8, 0.5, 0.3, decline_params.V_DEAD):
        for rng in mesh.leaves:
            leaf, tiller = sample.leaves[rng.id], sample.tillers[sample.leaves[rng.id].tiller]
            dl = declined_leaf(asdict(leaf), asdict(tiller), v, decline_params)
            lk = look(health(leaf.senescence, v, leaf.age, leaf.decline_draw, decline_params), decline_params)
            vertices = [declined_vertex(mesh.positions[i], gg.vertex_inputs(mesh, i), TIME, v, decline_params,
                                        collar_bend=P.collar_bend) for i in range(*rng.blade)]
            for dv in vertices:
                assert dv.browning == pytest.approx(dl.browning, abs=1e-12), f"{rng.id} v={v}"
                assert dv.opacity == pytest.approx(dl.opacity, abs=1e-12), f"{rng.id} v={v}"
            if leaf.fade >= 1.0 or leaf.blade_length_m <= 0.0 or dl.blade_length_m <= 0.0:
                continue
            for dv in vertices:
                assert math.dist(dv.axis, dl.direction) <= VECTOR_TOL, f"{rng.id} v={v}: axis"
            enclosed_m = min(emergence[rng.id] - leaf.sheath_length_m, leaf.blade_length_m)
            enclosed_checked += enclosed_m > 0.0

            leaned = replace(tiller, direction=dl.tiller_axis)
            rebuilt_leaf = replace(leaf, attach=dl.attach, direction=dl.direction, sheath_length_m=dl.sheath_length_m,
                                   blade_length_m=dl.blade_length_m, blade_width_m=dl.blade_width_m,
                                   arch_rad=dl.arch_rad,
                                   angle_from_axis_rad=leaf.angle_from_axis_rad + lk.extra_angle_rad)
            radius = gg.sheath_radius(leaf.age, elongation_age, P) * lk.size_scale
            twist, _ = gg._leaf_draws(table.seed, rng.id, P)  # no public helper: the mesh's own twist stream
            rows = gg.leaf_rows(rebuilt_leaf, leaned, radius, twist, P,
                                emergence_m=emergence[rng.id] * lk.size_scale).blade

            expected = rows[0][row // 2]  # the midrib start on the wall: the pivot (G37) and the collar row midrib
            for dv in vertices:
                assert math.dist(dv.pivot, expected) <= VECTOR_TOL, f"{rng.id} v={v}: pivot"
            centre = vertices[row // 2].position
            assert math.dist(centre, expected) <= COLLAR_REL_TOL * max(math.dist(expected, (0, 0, 0)), 1e-3), \
                f"{rng.id} v={v}: collar row centre {centre} != {expected}"

            last = len(rows) - 1
            tip = vertices[last * row + row // 2].position
            want = rows[last][row // 2]
            err = math.dist(tip, want) / dl.blade_length_m
            worst_tip = max(worst_tip, err)
            assert err <= TIP_REL_TOL, f"{rng.id} v={v}: tip differs {err:.4f} of the declined blade length"
            compared += 1
    assert compared > 0 and enclosed_checked > 0
    with capsys.disabled():
        print(f"\nworst tip error: {worst_tip:.5f} of the declined blade length ({compared} comparisons, "
              f"{enclosed_checked} enclosed)")


def test_vertices_unchanged_at_full_vitality(table: gg.OrganTable, mesh: gg.PlantMesh,
                                             decline_params: DeclineParams) -> None:
    sample = gg.sample_at(table, TIME)
    checked = 0
    for rng in mesh.leaves:
        if sample.leaves[rng.id].senescence > 0.0:
            continue  # turnover senescence declines a leaf even at v = 1
        for i in (*range(*rng.sheath), *range(*rng.blade)):
            dv = declined_vertex(mesh.positions[i], gg.vertex_inputs(mesh, i), TIME, 1.0, decline_params,
                                 collar_bend=P.collar_bend)
            assert math.dist(dv.position, mesh.positions[i]) <= IDENTITY_TOL, f"{rng.id} vertex {i}"
            assert dv.browning == 0.0 and dv.opacity == 1.0
            checked += 1
    assert checked > 0

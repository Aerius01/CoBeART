"""Decline reference (GP Phase 5, WP 5.2): the rebuilt VertexInputs against build_plant, and the JSON it writes."""
from __future__ import annotations

import dataclasses
import json
import math
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import grass_geometry as gg
from bake_grass_vat import bake
from conftest import PLANT_DIR
from decline_reference import DEFAULT_CASES, PlantView, reference, vertex_inputs_at, view

sys.path.insert(0, str(PLANT_DIR.parent / "vat"))
from vat_writer import VatPlant, decode_vat, encode_vat  # noqa: E402

F16_RELATIVE = 2.0 ** -11
F16_ABSOLUTE = 2.0 ** -24
TOLERANCE = 1e-6
STAGE_TIMES_CHECKED = (3.0, 6.0, 12.0)


@dataclasses.dataclass(frozen=True)
class Reference:
    table: gg.OrganTable
    plant: VatPlant
    pv: PlantView
    lifetime: tuple[str, ...]


@pytest.fixture(scope="module")
def ref(organ_table_path: Path) -> Reference:
    table = gg.load_table(organ_table_path)
    plant = decode_vat(encode_vat(bake(table, gg.GeometryParams())))
    return Reference(table, plant, view(plant), tuple(gg.lifetime_leaves(table)))


def alive_organs(pv: PlantView, t: float) -> set[int]:
    """Organs visibly built at t: born before t (at t == appear build_plant has the leaf degenerate with fade 1) and
    not yet vanished."""
    lifespan = pv.blocks["lifespan"]
    return {o for o in range(pv.organ_count) if lifespan[2 * o] < t < lifespan[2 * o + 1]}


def vectors_close(a: Sequence[float], b: Sequence[float], tol: float = TOLERANCE) -> bool:
    return all(math.isclose(x, y, rel_tol=tol, abs_tol=tol) for x, y in zip(a, b, strict=True))


def test_vertex_inputs_equal_build_plant(ref: Reference) -> None:
    p = gg.GeometryParams()
    checked = 0
    for t in STAGE_TIMES_CHECKED:
        stage = list(ref.plant.stage_times).index(t)
        mesh = gg.build_plant(ref.table, t, p, lifetime=gg.lifetime_leaves(ref.table))
        alive = alive_organs(ref.pv, t)
        for v in range(ref.pv.vertex_count):
            if ref.pv.blocks["organ"][v] not in alive:
                continue
            got, want = vertex_inputs_at(ref.pv, stage, t, v), gg.vertex_inputs(mesh, v)
            assert (got.kind, got.tiller) == (want.kind, want.tiller), (t, v)
            for field in dataclasses.fields(got):
                a, b = getattr(got, field.name), getattr(want, field.name)
                if isinstance(a, tuple):
                    assert vectors_close(a, b), (t, v, field.name, a, b)
                else:
                    assert math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE), (t, v, field.name, a, b)
            checked += 1
    assert checked > 500


def test_reference_at_full_vitality_equals_positions_of_healthy_main_shoot_leaves(ref: Reference) -> None:
    t = 12.0
    stage = list(ref.plant.stage_times).index(t)
    result = reference(ref.plant, [(t, 1.0)], "x.vat.glb")
    positions = result["cases"][0]["positions"]
    decoded = ref.pv.blocks["position"]
    count = ref.pv.vertex_count
    alive = alive_organs(ref.pv, t)
    checked = 0
    for v in range(count):
        o = ref.pv.blocks["organ"][v]
        if o not in alive or ref.pv.blocks["tiller"][o] != 0:
            continue
        if vertex_inputs_at(ref.pv, stage, t, v).senescence != 0.0:
            continue
        want = decoded[(stage * count + v) * 3:(stage * count + v) * 3 + 3]
        for got, w in zip(positions[3 * v:3 * v + 3], want, strict=True):
            assert abs(got - w) <= 1e-6 + abs(w) * 1e-6, (v, got, w)
        checked += 1
    assert checked > 100


def test_a_non_stage_time_raises_naming_the_nearest_stages(ref: Reference) -> None:
    with pytest.raises(ValueError, match=r"T=6\.25 is not a stage time.*6\.0, 6\.5"):
        reference(ref.plant, [(6.25, 1.0)], "x.vat.glb")


def test_a_non_grass_bake_raises(ref: Reference) -> None:
    for changed in (dataclasses.replace(ref.plant, species="flower"),
                    dataclasses.replace(ref.plant, species_version=2)):
        with pytest.raises(ValueError, match="grass"):
            reference(changed, [(12.0, 1.0)], "x.vat.glb")


def check_shape(result: dict[str, Any], vertices: int, cases: Sequence[tuple[float, float]]) -> None:
    assert set(result) == {"vat", "vertex_count", "cases"}
    assert result["vertex_count"] == vertices
    assert [(c["t"], c["v"]) for c in result["cases"]] == [tuple(c) for c in cases]
    for case in result["cases"]:
        assert set(case) == {"t", "v", "positions", "pivots", "axes", "browning", "opacity"}
        for name in ("positions", "pivots", "axes"):
            assert len(case[name]) == 3 * vertices
        for name in ("browning", "opacity"):
            assert len(case[name]) == vertices
            assert all(0.0 <= x <= 1.0 for x in case[name])
        assert all(math.isfinite(x) for name in ("positions", "pivots", "axes") for x in case[name])


def test_json_shape_and_rounding(ref: Reference) -> None:
    cases = [(12.0, 0.6), (3.0, 0.8)]
    result = reference(ref.plant, cases, "grass.vat.glb")
    assert result["vat"] == "grass.vat.glb"
    check_shape(json.loads(json.dumps(result)), ref.pv.vertex_count, cases)
    assert all(float(f"{x:.7g}") == x for x in result["cases"][0]["positions"])


def test_cli_writes_default_cases(organ_table_path: Path, ref: Reference, tmp_path: Path) -> None:
    vat, out = tmp_path / "grass.vat.glb", tmp_path / "ref.json"
    vat.write_bytes(encode_vat(bake(gg.load_table(organ_table_path), gg.GeometryParams())))
    done = subprocess.run([sys.executable, str(PLANT_DIR / "decline_reference.py"), str(vat), str(out)],
                          capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0, done.stderr
    assert f"{len(DEFAULT_CASES)} cases" in done.stdout and f"{ref.pv.vertex_count} vertices" in done.stdout
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["vat"] == "grass.vat.glb"
    check_shape(result, ref.pv.vertex_count, DEFAULT_CASES)
    bad = subprocess.run([sys.executable, str(PLANT_DIR / "decline_reference.py"), str(vat), str(out),
                          "--case", "5.1,1"], capture_output=True, text=True, timeout=300, check=False)
    assert bad.returncode != 0 and "not a stage time" in bad.stderr

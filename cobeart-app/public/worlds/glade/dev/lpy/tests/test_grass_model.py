"""Exporter tests against the real grass model, ../grass.lpy (needs the lpy conda env)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from openalea.lpy import Lsystem

LPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LPY_DIR))

import export_organs as eo  # noqa: E402

GRASS = LPY_DIR / "grass.lpy"
MAX_UNROLLED_TILT_DEG = 25.0
ROLLED_UNROLL = 0.2
EXPECTED_TILLERS = 5


@pytest.fixture(scope="module")
def doc25(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return export(tmp_path_factory.mktemp("grass25"), "out.json", samples=25)


def export(directory: Path, name: str, **kwargs: Any) -> dict[str, Any]:
    eo.export_organs(GRASS, directory / name, **kwargs)
    return json.loads((directory / name).read_text(encoding="utf-8"))


def test_export_validates(doc25: dict[str, Any]) -> None:
    schema = json.loads(eo.SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc25)
    assert doc25["model"] == "grass.lpy"
    assert len(doc25["samples"]) == 25


def test_live_leaves_capped_per_tiller(doc25: dict[str, Any]) -> None:
    live_leaves = doc25["params"]["LIVE_LEAVES"]
    for sample in doc25["samples"]:
        live: dict[int, int] = {}
        for organ in sample["organs"]:
            if organ["kind"] == "leaf" and organ["state"] in ("growing", "mature"):
                live[organ["tiller"]] = live.get(organ["tiller"], 0) + 1
        assert all(count <= live_leaves for count in live.values()), (sample["time"], live)


def test_five_tillers_at_the_end(doc25: dict[str, Any]) -> None:
    tillers = [o for o in doc25["samples"][-1]["organs"] if o["kind"] == "tiller"]
    assert len(tillers) == EXPECTED_TILLERS


def test_final_sample_independent_of_sample_count(tmp_path: Path) -> None:
    few = export(tmp_path, "few.json", samples=2)
    many = export(tmp_path, "many.json", samples=25)
    assert few["samples"][-1] == many["samples"][-1]


def test_incremental_derivation_matches_single_derive() -> None:
    reference = Lsystem(str(GRASS))
    externs = reference.context().globals()
    steps = round(externs["T_END"] / externs["DT"])
    expected = str(reference.derive())
    stepped = Lsystem(str(GRASS))
    tree = stepped.axiom
    done = 0
    for target in (steps // 3, 2 * steps // 3, steps):
        tree = stepped.derive(tree, done, target - done)
        done = target
    assert str(tree) == expected


def test_seed_determinism_and_difference(tmp_path: Path) -> None:
    eo.export_organs(GRASS, tmp_path / "a.json", samples=5, seed=11)
    eo.export_organs(GRASS, tmp_path / "b.json", samples=5, seed=11)
    eo.export_organs(GRASS, tmp_path / "c.json", samples=5, seed=12)
    assert (tmp_path / "a.json").read_text() == (tmp_path / "b.json").read_text()
    first = json.loads((tmp_path / "a.json").read_text())
    other = json.loads((tmp_path / "c.json").read_text())
    assert first["samples"] != other["samples"]


def test_rolled_blades_stay_near_tiller_axis(doc25: dict[str, Any]) -> None:
    checked = 0
    for sample in doc25["samples"]:
        by_id = {o["id"]: o for o in sample["organs"]}
        for organ in sample["organs"]:
            if organ["kind"] != "leaf" or organ["unroll"] >= ROLLED_UNROLL:
                continue
            axis = by_id[f"T{organ['tiller']}"]["direction"]
            cosine = sum(a * b for a, b in zip(organ["direction"], axis))
            angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
            assert angle < MAX_UNROLLED_TILT_DEG, (organ["id"], sample["time"], angle)
            checked += 1
    assert checked > 0


MIN_TILLER_PLANE_ANGLE_DEG = 15.0


def test_tiller_leaf_planes_differ_from_main_shoot(doc25: dict[str, Any]) -> None:
    tillers = {o["id"]: o for o in doc25["samples"][-1]["organs"] if o["kind"] == "tiller"}
    main_azimuth = tillers["T0"]["azimuth_rad"]
    assert len(tillers) > 1
    for tiller_id, tiller in tillers.items():
        if tiller_id == "T0":
            continue
        folded = math.degrees(tiller["azimuth_rad"] - main_azimuth) % 180.0
        angle = min(folded, 180.0 - folded)
        assert angle >= MIN_TILLER_PLANE_ANGLE_DEG, (tiller_id, angle)

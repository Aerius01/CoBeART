"""Exporter tests against the small fixture model (needs the lpy conda env)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

LPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LPY_DIR))

import export_organs as eo  # noqa: E402

FIXTURE = Path(__file__).with_name("fixture_grass.lpy")


def run_export(tmp_path: Path, name: str = "out.json", model: Path = FIXTURE, **kwargs: Any) -> dict[str, Any]:
    eo.export_organs(model, tmp_path / name, **kwargs)
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def organs_by_id(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["id"]: o for o in sample["organs"]}


def test_export_matches_schema_and_header(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=7)
    schema = json.loads(eo.SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)
    assert doc["schema_version"] == 1
    assert doc["model"] == "fixture_grass.lpy"
    assert doc["seed"] == 1
    assert set(doc["params"]) == {"SEED", "DT", "T_END", "LIFE"}
    assert doc["sample_times"] == [s["time"] for s in doc["samples"]]
    assert doc["sample_times"][0] == 0 and doc["sample_times"][-1] == doc["t_end"]
    assert len(doc["samples"]) == 7


def test_schema_rejects_extra_and_out_of_range(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=3)
    bad_extra = json.loads(json.dumps(doc))
    bad_extra["samples"][-1]["organs"][0]["bogus"] = 1
    with pytest.raises(eo.OrganTableError):
        eo.validate_document(bad_extra)
    bad_range = json.loads(json.dumps(doc))
    leaf = next(o for o in bad_range["samples"][-1]["organs"] if o["kind"] == "leaf")
    leaf["unroll"] = 1.5
    with pytest.raises(eo.OrganTableError):
        eo.validate_document(bad_range)


def test_ids_stable_across_samples(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=13)
    seen: dict[str, dict[str, Any]] = {}
    for sample in doc["samples"]:
        current = organs_by_id(sample)
        assert set(seen) <= set(current)
        for organ_id, first in seen.items():
            for field in ("kind", "tiller", "rank", "birth_time"):
                assert current[organ_id][field] == first[field]
        for organ_id, organ in current.items():
            seen.setdefault(organ_id, organ)
    assert {"T0", "T1", "L0.1", "L1.1"} <= set(seen)


def test_dead_leaf_persists_with_zero_size(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=13)
    final = organs_by_id(doc["samples"][-1])
    leaf = final["L0.1"]
    assert leaf["state"] == "dead"
    assert leaf["senescence"] == 1
    assert leaf["sheath_length_m"] == leaf["blade_length_m"] == leaf["blade_width_m"] == 0
    assert "L0.1" in organs_by_id(doc["samples"][len(doc["samples"]) // 2])


def test_birth_time_follows_age_convention(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=13)
    final = organs_by_id(doc["samples"][-1])
    assert final["L0.1"]["birth_time"] == 0
    assert final["L0.2"]["birth_time"] == 0.5
    assert final["L1.1"]["birth_time"] == 1.5
    assert final["T1"]["birth_time"] == 0.5


def test_deterministic_for_fixed_seed_and_differs_for_other(tmp_path: Path) -> None:
    a = run_export(tmp_path, "a.json", seed=5)
    run_export(tmp_path, "b.json", seed=5)
    c = run_export(tmp_path, "c.json", seed=6)
    assert (tmp_path / "a.json").read_text() == (tmp_path / "b.json").read_text()
    assert a["seed"] == 5 and c["seed"] == 6
    assert a["samples"] != c["samples"]


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli" / "table.json"
    code = eo.main([str(FIXTURE), str(out), "--samples", "5", "--seed", "3"])
    assert code == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["seed"] == 3 and len(doc["samples"]) == 5
    assert "wrote" in capsys.readouterr().out


def test_malformed_module_names_module_and_field(tmp_path: Path) -> None:
    broken = tmp_path / "broken.lpy"
    broken.write_text(FIXTURE.read_text().replace('        "arch_rad": 0.4,\n', ""), encoding="utf-8")
    out = tmp_path / "out.json"
    with pytest.raises(eo.OrganTableError, match=r"Leaf L0\.1.*'o'.*arch_rad"):
        eo.export_organs(broken, out)
    assert not out.exists()
    assert eo.main([str(broken), str(out)]) == 1


def test_wrong_type_names_module_and_field(tmp_path: Path) -> None:
    broken = tmp_path / "broken.lpy"
    text = FIXTURE.read_text().replace('"unroll": min(1.0, age / 2.0)', '"unroll": "open"')
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match=r"Leaf L0\.1.*o\.unroll"):
        eo.export_organs(broken, tmp_path / "out.json")


def test_vanishing_organ_is_reported() -> None:
    leaf = eo.LeafOrgan(
        id="L0.1", kind="leaf", tiller=0, rank=1, birth_time=0.0, age=0.0, state="growing",
        attach=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0), azimuth_rad=0.0, angle_from_axis_rad=0.0,
        sheath_length_m=0.0, blade_length_m=0.0, blade_width_m=0.0, unroll=0.0, arch_rad=0.0, senescence=0.0,
    )
    with pytest.raises(eo.OrganTableError, match="L0.1"):
        eo.check_stable_ids([eo.Sample(0.0, (leaf,)), eo.Sample(1.0, ())])


def test_seed_without_extern_is_an_error(tmp_path: Path) -> None:
    model = tmp_path / "noseed.lpy"
    model.write_text(FIXTURE.read_text().replace("extern(SEED=1)", "SEED = 1"), encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match="SEED"):
        eo.export_organs(model, tmp_path / "out.json", seed=2)


def test_sample_count_limits(tmp_path: Path) -> None:
    with pytest.raises(eo.OrganTableError, match="at least 2"):
        eo.export_organs(FIXTURE, tmp_path / "o.json", samples=1)
    with pytest.raises(eo.OrganTableError, match="maximum is 25"):
        eo.export_organs(FIXTURE, tmp_path / "o.json", samples=26)
    assert len(run_export(tmp_path, samples=25)["samples"]) == 25


def test_t_end_must_be_multiple_of_dt(tmp_path: Path) -> None:
    model = tmp_path / "odd.lpy"
    model.write_text(FIXTURE.read_text().replace("extern(T_END=12.0)", "extern(T_END=12.2)"), encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match=r"T_END=12\.2.*DT=0\.5"):
        eo.export_organs(model, tmp_path / "out.json")


def test_organs_sorted_by_birth_time_then_id(tmp_path: Path) -> None:
    for sample in run_export(tmp_path, samples=13)["samples"]:
        keys = [(o["birth_time"], o["id"]) for o in sample["organs"]]
        assert keys == sorted(keys)


def test_dead_leaf_invariants_are_checked(tmp_path: Path) -> None:
    broken = tmp_path / "broken.lpy"
    text = FIXTURE.read_text().replace(
        '"blade_length_m": 0.15 * size', '"blade_length_m": 0.15 * size + (0.01 if age >= LIFE else 0.0)'
    )
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match=r"dead leaf L0\.1.*blade_length_m"):
        eo.export_organs(broken, tmp_path / "out.json", samples=13)

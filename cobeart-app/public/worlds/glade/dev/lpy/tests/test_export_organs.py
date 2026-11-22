"""Exporter tests against the small fixture model (needs the lpy conda env)."""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

LPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LPY_DIR))

import decline  # noqa: E402
import export_organs as eo  # noqa: E402

FIXTURE = Path(__file__).with_name("fixture_grass.lpy")
DECLINE_NAMES = {f.name for f in dataclasses.fields(decline.DeclineParams)}


def run_export(tmp_path: Path, name: str = "out.json", model: Path = FIXTURE, **kwargs: Any) -> dict[str, Any]:
    eo.export_organs(model, tmp_path / name, **kwargs)
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def organs_by_id(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["id"]: o for o in sample["organs"]}


def test_export_matches_schema_and_header(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=7)
    schema = json.loads(eo.SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)
    assert doc["schema_version"] == 3
    assert doc["model"] == "fixture_grass.lpy"
    assert doc["seed"] == 1
    assert set(doc["params"]) == {"SEED", "DT", "T_END", "LIFE", "FADE_DURATION", "PREVIEW_DECLINE"} | DECLINE_NAMES
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


def test_dead_leaf_fades_then_collapses(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=25)
    history = [organs_by_id(s)["L0.1"] for s in doc["samples"]]
    assert history[-1]["state"] == "dead" and history[-1]["fade"] == 1
    assert all(history[-1][f] == 0 for f in eo.SIZE_FIELDS)
    dry = [h for h in history if h["state"] == "dead" and h["fade"] < 1]
    assert dry and all(h["senescence"] == 1 and h["blade_length_m"] > 0 for h in dry)
    fades = [h["fade"] for h in history]
    assert fades == sorted(fades)
    assert all(h["fade"] == 0 for h in history if h["state"] != "dead")


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


def make_leaf(**changes: Any) -> eo.LeafOrgan:
    leaf = eo.LeafOrgan(
        id="L0.1", kind="leaf", tiller=0, rank=1, birth_time=0.0, age=0.0, state="growing",
        attach=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0), azimuth_rad=0.0, angle_from_axis_rad=0.0,
        sheath_length_m=0.1, blade_length_m=0.1, blade_width_m=0.01, unroll=0.0, arch_rad=0.0, senescence=0.0,
        fade=0.0, decline_draw=0.0, senescence_duration=2.0,
    )
    return dataclasses.replace(leaf, **changes)


def make_tiller() -> eo.TillerOrgan:
    return eo.TillerOrgan(
        id="T0", kind="tiller", tiller=0, rank=0, birth_time=0.0, age=0.0, state="growing",
        base=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0), azimuth_rad=0.0, splay_rad=0.0,
    )


COLLAPSED = {"state": "dead", "senescence": 1.0, "fade": 1.0, "sheath_length_m": 0.0, "blade_length_m": 0.0,
             "blade_width_m": 0.0}
DRY = {"state": "dead", "senescence": 1.0, "fade": 0.4}


def check(*leaves: eo.LeafOrgan) -> None:
    """Run the stable-id checks over one sample per leaf (times 0, 1, ...), each with the tiller T0."""
    eo.check_stable_ids([eo.Sample(float(i), (make_tiller(), leaf)) for i, leaf in enumerate(leaves)])


def test_valid_life_cycle_passes() -> None:
    check(make_leaf(), make_leaf(state="senescent", senescence=0.5), make_leaf(**DRY), make_leaf(**COLLAPSED),
          make_leaf(**COLLAPSED))


@pytest.mark.parametrize("changes, pattern", [
    ({"state": "senescent", "senescence": 1.0}, r"leaf L0\.1.*'senescence'.*time 0\.0"),
    ({"state": "dead", "senescence": 0.9}, r"leaf L0\.1.*'senescence'.*time 0\.0"),
    ({"fade": 0.2}, r"leaf L0\.1.*'fade'.*time 0\.0"),
    ({"state": "mature", "fade": 0.1}, r"leaf L0\.1.*'fade'.*time 0\.0"),
])
def test_senescence_and_fade_state_rules(changes: dict[str, Any], pattern: str) -> None:
    with pytest.raises(eo.OrganTableError, match=pattern):
        check(make_leaf(**changes))


def test_fade_must_not_decrease() -> None:
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1.*'fade' decreased.*time 1\.0"):
        check(make_leaf(**DRY), make_leaf(**{**DRY, "fade": 0.2}))


@pytest.mark.parametrize("field", ["sheath_length_m", "blade_length_m", "blade_width_m"])
def test_collapsed_leaf_must_have_zero_size(field: str) -> None:
    with pytest.raises(eo.OrganTableError, match=rf"leaf L0\.1.*{field!r}.*time 0\.0"):
        check(make_leaf(**{**COLLAPSED, field: 0.01}))


def test_collapsed_leaf_must_sit_at_tiller_base() -> None:
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1.*'attach'.*time 0\.0"):
        check(make_leaf(**COLLAPSED, attach=(0.0, 0.0, 0.1)))


def test_collapsed_leaf_stays_collapsed() -> None:
    regrown = make_leaf(**{**COLLAPSED, "blade_length_m": 0.05})
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1.*'blade_length_m'.*time 1\.0.*collapsed earlier"):
        check(make_leaf(**COLLAPSED), regrown)


def test_dead_leaf_cannot_revive() -> None:
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1.*'state' left 'dead'.*time 1\.0"):
        check(make_leaf(**DRY), make_leaf(**{**DRY, "state": "senescent"}))


def test_vanishing_organ_is_reported() -> None:
    leaf = make_leaf()
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


def test_model_breaking_an_invariant_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "broken.lpy"
    text = FIXTURE.read_text().replace(
        '"blade_length_m": 0.15 * size', '"blade_length_m": 0.15 * size + (0.01 if fade >= 1.0 else 0.0)'
    )
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1.*'blade_length_m'"):
        eo.export_organs(broken, tmp_path / "out.json", samples=25)


def test_sampling_never_derives_beyond_t_end(tmp_path: Path) -> None:
    longer = tmp_path / "longer.lpy"
    longer.write_text(
        FIXTURE.read_text().replace("derivation length: int(round(T_END / DT))",
                                    "derivation length: int(round(T_END / DT)) + 8"),
        encoding="utf-8",
    )
    assert longer.read_text() != FIXTURE.read_text()
    reference = run_export(tmp_path, "ref.json", samples=13)
    doc = run_export(tmp_path, "long.json", model=longer, samples=13)
    assert doc["samples"] == reference["samples"]
    assert doc["samples"][-1]["time"] == doc["t_end"]


def test_missing_decline_extern_fails_at_export(tmp_path: Path) -> None:
    model = tmp_path / "nodecline.lpy"
    model.write_text(FIXTURE.read_text().replace("extern(V_DEAD=0.15)\n", ""), encoding="utf-8")
    out = tmp_path / "out.json"
    with pytest.raises(eo.OrganTableError, match=r"nodecline\.lpy.*V_DEAD"):
        eo.export_organs(model, out)
    assert not out.exists()


def test_decline_draw_outside_unit_range_is_rejected(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=3)
    leaf = next(o for o in doc["samples"][-1]["organs"] if o["kind"] == "leaf")
    assert -1 <= leaf["decline_draw"] <= 1
    leaf["decline_draw"] = 1.5
    with pytest.raises(eo.OrganTableError):
        eo.validate_document(doc)


def test_schema_required_fields_match_dataclasses() -> None:
    schema = json.loads(eo.SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    assert "organ_events" in schema["required"]
    assert set(defs["leafEvents"]["required"]) == {f.name for f in dataclasses.fields(eo.LeafEvents)}
    assert set(defs["leafEvents"]["properties"]) == set(defs["leafEvents"]["required"])
    assert set(defs["leaf"]["required"]) == {f.name for f in dataclasses.fields(eo.LeafOrgan)}
    assert set(defs["tiller"]["required"]) == {f.name for f in dataclasses.fields(eo.TillerOrgan)}
    assert set(defs["leaf"]["properties"]) == set(defs["leaf"]["required"])
    assert set(defs["tiller"]["properties"]) == set(defs["tiller"]["required"])
    assert eo.LEAF_VECTOR_KEYS == ("attach", "direction")
    assert eo.TILLER_VECTOR_KEYS == ("base", "direction")


def test_organ_events_of_the_fixture(tmp_path: Path) -> None:
    """L0.1 turns senescent at age 3 + DT and lasts 1.5 P; late leaves miss events by T_END."""
    doc = run_export(tmp_path, samples=25)
    events = doc["organ_events"]
    last = [o for o in doc["samples"][-1]["organs"] if o["kind"] == "leaf"]
    assert list(events) == [o["id"] for o in last]
    assert events["L0.1"] == {"senescent_time": 3.0, "senescence_duration": 1.5, "death_time": 4.5,
                              "collapse_time": 5.5}
    assert events["L0.9"]["death_time"] == 11.5 and events["L0.9"]["collapse_time"] is None
    assert events["L0.10"]["senescent_time"] == 11.5 and events["L0.10"]["death_time"] is None
    assert events["L1.11"] == {"senescent_time": None, "senescence_duration": 1.5, "death_time": None,
                               "collapse_time": None}
    for sample in doc["samples"]:
        for leaf in (o for o in sample["organs"] if o["kind"] == "leaf"):
            assert leaf["senescence_duration"] == events[leaf["id"]]["senescence_duration"]


def test_events_do_not_depend_on_the_sample_count(tmp_path: Path) -> None:
    assert run_export(tmp_path, "a.json", samples=2)["organ_events"] == \
        run_export(tmp_path, "b.json", samples=25)["organ_events"]


def test_senescence_breaking_the_closed_form_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "broken.lpy"
    text = FIXTURE.read_text().replace(
        "return min(1.0, max(0.0, (age - (LIFE - 1.0)) / senescence_duration(leaf_id)))",
        "return min(1.0, max(0.0, (age - (LIFE - 1.0)) / senescence_duration(leaf_id))) ** 2",
    )
    assert text != FIXTURE.read_text()
    broken.write_text(text, encoding="utf-8")
    out = tmp_path / "out.json"
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1: field 'senescence' .* at time 3\.5"):
        eo.export_organs(broken, out)
    assert not out.exists()


def test_changing_senescence_duration_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "broken.lpy"
    text = FIXTURE.read_text().replace('"senescence_duration": senescence_duration(leaf_id),',
                                       '"senescence_duration": senescence_duration(leaf_id) + age,')
    assert text != FIXTURE.read_text()
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match=r"leaf L0\.1: field 'senescence_duration' changed"):
        eo.export_organs(broken, tmp_path / "out.json")


def test_missing_fade_duration_is_an_error(tmp_path: Path) -> None:
    model = tmp_path / "nofade.lpy"
    model.write_text(FIXTURE.read_text().replace("extern(FADE_DURATION=1.0)\n", "FADE_DURATION = 1.0\n"),
                     encoding="utf-8")
    with pytest.raises(eo.OrganTableError, match=r"nofade\.lpy.*FADE_DURATION"):
        eo.export_organs(model, tmp_path / "out.json")


def test_schema_rejects_bad_organ_events(tmp_path: Path) -> None:
    doc = run_export(tmp_path, samples=3)
    for edit in (lambda d: d.pop("organ_events"),
                 lambda d: d["organ_events"]["L0.1"].pop("death_time"),
                 lambda d: d["organ_events"]["L0.1"].__setitem__("senescence_duration", None),
                 lambda d: d["organ_events"].__setitem__("T0", d["organ_events"]["L0.1"])):
        bad = json.loads(json.dumps(doc))
        edit(bad)
        with pytest.raises(eo.OrganTableError):
            eo.validate_document(bad)

"""Exporter tests against the real grass model, ../grass.lpy (needs the lpy conda env)."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from openalea.lpy import Lsystem

LPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LPY_DIR))

import decline  # noqa: E402
import export_organs as eo  # noqa: E402

GRASS = LPY_DIR / "grass.lpy"
MAX_UNROLLED_TILT_DEG = 25.0
ROLLED_UNROLL = 0.2
EXPECTED_TILLERS = 5
EVERY_STEP_SAMPLES = 241  # T_END / DT + 1 with the defaults: a sample at every step
EXPORT_TIMEOUT_S = 120
EVENT_TOLERANCE = 1e-6


@pytest.fixture(scope="module")
def doc25(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return export(tmp_path_factory.mktemp("grass25"), "out.json", samples=25)


@pytest.fixture(scope="module")
def doc241(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A sample at every step, exported in a subprocess so no Lsystem outlives its tree in this process."""
    out = tmp_path_factory.mktemp("grass241") / "out.json"
    result = subprocess.run([sys.executable, str(LPY_DIR / "export_organs.py"), str(GRASS), str(out),
                             "--samples", str(EVERY_STEP_SAMPLES)], capture_output=True, text=True,
                            timeout=EXPORT_TIMEOUT_S)
    if result.returncode != 0:
        raise RuntimeError(f"export_organs.py failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(out.read_text(encoding="utf-8"))


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


def leaf_histories(doc: dict[str, Any]) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    histories: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for sample in doc["samples"]:
        for organ in sample["organs"]:
            if organ["kind"] == "leaf":
                histories.setdefault(organ["id"], []).append((sample["time"], organ))
    return histories


def test_schema_version_and_new_leaf_fields(doc25: dict[str, Any]) -> None:
    assert doc25["schema_version"] == 3
    leaves = [o for o in doc25["samples"][-1]["organs"] if o["kind"] == "leaf"]
    assert leaves and all("fade" in o and "decline_draw" in o for o in leaves)
    assert len({o["decline_draw"] for o in leaves}) > 1
    assert len({o["senescence_duration"] for o in leaves}) > 1


def test_senescence_one_iff_dead_and_fade_only_when_dead(doc25: dict[str, Any]) -> None:
    for history in leaf_histories(doc25).values():
        for time, leaf in history:
            assert (leaf["senescence"] == 1) == (leaf["state"] == "dead"), (leaf["id"], time)
            assert leaf["fade"] == 0 or leaf["state"] == "dead", (leaf["id"], time)


def test_fade_never_decreases_and_fade_one_is_collapsed(doc25: dict[str, Any]) -> None:
    tillers = {t["id"]: t for t in doc25["samples"][-1]["organs"] if t["kind"] == "tiller"}
    collapsed_seen = 0
    for leaf_id, history in leaf_histories(doc25).items():
        fades = [leaf["fade"] for _, leaf in history]
        assert fades == sorted(fades), leaf_id
        collapsed = False
        for time, leaf in history:
            collapsed = collapsed or leaf["fade"] == 1
            if collapsed:
                assert leaf["fade"] == 1 and leaf["state"] == "dead", (leaf_id, time)
                assert leaf["sheath_length_m"] == leaf["blade_length_m"] == leaf["blade_width_m"] == 0, (leaf_id, time)
                assert leaf["attach"] == tillers[f"T{leaf['tiller']}"]["base"], (leaf_id, time)
                collapsed_seen += 1
    assert collapsed_seen > 0


def test_dead_leaves_reach_fade_one_within_fade_duration(doc241: dict[str, Any]) -> None:
    """With a sample at every step, a leaf dead for FADE_DURATION (plus half a step) is collapsed."""
    dt, fade_duration = doc241["dt"], doc241["params"]["FADE_DURATION"]
    assert len(doc241["samples"]) == round(doc241["t_end"] / dt) + 1
    checked = 0
    for leaf_id, history in leaf_histories(doc241).items():
        first_dead = next((time for time, leaf in history if leaf["state"] == "dead"), None)
        for time, leaf in history:
            if first_dead is not None and time >= first_dead + fade_duration + dt / 2:
                assert leaf["fade"] == 1, (leaf_id, time, leaf["fade"])
                checked += 1
    assert checked > 0


def test_productions_freeze_after_t_end() -> None:
    """With a decline preview the L-string at T_END is unchanged by deriving the preview steps."""
    lsystem = Lsystem(str(GRASS), {"PREVIEW_DECLINE": 2.0})
    names = lsystem.context().globals()
    grown = round(names["T_END"] / names["DT"])
    total = round((names["T_END"] + names["PREVIEW_DECLINE"]) / names["DT"])
    assert total > grown
    tree = lsystem.derive(lsystem.axiom, 0, grown)
    at_t_end = str(tree)
    assert str(lsystem.derive(tree, grown, total - grown)) == at_t_end


DECLINE_VITALITIES = (0.0, 0.1, 0.5, 1.0)


def test_export_params_build_the_model_decline_and_declined_leaf_runs(doc25: dict[str, Any]) -> None:
    lsystem = Lsystem(str(GRASS))
    assert decline.DeclineParams.from_mapping(doc25["params"]) == lsystem.context().globals()["DECLINE"]
    params = decline.DeclineParams.from_mapping(doc25["params"])
    checked = 0
    for sample in doc25["samples"]:
        tillers = {o["tiller"]: o for o in sample["organs"] if o["kind"] == "tiller"}
        for leaf in (o for o in sample["organs"] if o["kind"] == "leaf"):
            for v in DECLINE_VITALITIES:
                drawn = decline.declined_leaf(leaf, tillers[leaf["tiller"]], v, params)
                numbers = [*drawn.attach, *drawn.direction, *drawn.tiller_axis, drawn.sheath_length_m,
                           drawn.blade_length_m, drawn.blade_width_m, drawn.arch_rad, drawn.browning]
                assert all(math.isfinite(x) for x in numbers), (leaf["id"], sample["time"], v)
                assert 0.0 <= drawn.opacity <= 1.0, (leaf["id"], sample["time"], v)
                checked += 1
    assert checked > 0


def clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def test_organ_events_reproduce_senescence_and_fade(doc241: dict[str, Any]) -> None:
    """G38: the closed forms of the events give every leaf's senescence and fade at every step."""
    events = doc241["organ_events"]
    fade_duration = doc241["params"]["FADE_DURATION"]
    histories = leaf_histories(doc241)
    assert set(events) == set(histories)
    checked = 0
    for leaf_id, history in histories.items():
        e = events[leaf_id]
        for time, leaf in history:
            sen = 0.0 if e["senescent_time"] is None else clamp01(
                (time - e["senescent_time"]) / e["senescence_duration"])
            fade = 0.0 if e["death_time"] is None else clamp01((time - e["death_time"]) / fade_duration)
            assert abs(sen - leaf["senescence"]) <= EVENT_TOLERANCE, (leaf_id, time, sen, leaf["senescence"])
            assert abs(fade - leaf["fade"]) <= EVENT_TOLERANCE, (leaf_id, time, fade, leaf["fade"])
            if leaf["fade"] == 1:
                assert e["collapse_time"] is not None and e["collapse_time"] <= time, (leaf_id, time)
            else:
                assert e["collapse_time"] is None or e["collapse_time"] > time, (leaf_id, time)
            assert leaf["senescence_duration"] == e["senescence_duration"], (leaf_id, time)
            checked += 1
    assert checked > 0


def test_organ_events_are_ordered_and_reached(doc241: dict[str, Any]) -> None:
    events = doc241["organ_events"]
    fade_duration = doc241["params"]["FADE_DURATION"]
    reached = {name: sum(e[name] is not None for e in events.values())
               for name in ("senescent_time", "death_time", "collapse_time")}
    assert reached["senescent_time"] >= reached["death_time"] >= reached["collapse_time"] > 0, reached
    for leaf_id, e in events.items():
        if e["death_time"] is not None:
            assert e["senescent_time"] is not None and e["senescent_time"] < e["death_time"], leaf_id
        if e["collapse_time"] is not None:
            assert e["death_time"] is not None, leaf_id
            assert abs(e["collapse_time"] - e["death_time"] - fade_duration) <= EVENT_TOLERANCE, leaf_id


def test_organ_events_independent_of_sample_count(doc25: dict[str, Any], doc241: dict[str, Any]) -> None:
    assert doc25["organ_events"] == doc241["organ_events"]

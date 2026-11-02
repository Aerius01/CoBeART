"""Tests of the decline rule, ../decline.py (stdlib only; runs under any interpreter)."""
from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

LPY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LPY_DIR))

import decline as dc  # noqa: E402

P = dc.DeclineParams(
    V_DEAD=0.15, DECLINE_AGE_SPREAD=0.4, DECLINE_AGE_REF=3.0, DECLINE_JITTER=0.1, DECLINE_RESIDUAL=0.5,
    SHRIVEL_FRACTION=0.3,
    ANGLE_SENESCENT_DEG=12.0, SHRIVEL_ANGLE_DEG=25.0, SHRIVEL_ARCH_RAD=0.8, DECLINE_SPLAY_DEG=25.0,
)
AGES = (0.0, 0.05, 0.5, 1.0, 2.0, 3.0, 5.0, 12.0)
DRAWS = (-1.0, -0.3, 0.0, 0.5, 1.0)
TOL = 1e-12
VS = [i / 100 for i in range(101)]


def test_full_vitality_is_healthy_and_identity() -> None:
    for age in AGES:
        for draw in DRAWS:
            assert dc.decline_health(1.0, age, draw, P) == 1.0
            assert dc.health(0.0, 1.0, age, draw, P) == 1.0
    assert dc.look(1.0, P) == dc.Look(size_scale=1.0, extra_angle_rad=0.0, extra_arch_rad=0.0, browning=0.0)
    assert dc.plant_fade(1.0, P) == 0.0
    assert dc.tiller_lean_rad(1.0, P) == 0.0
    assert dc.opacity(0.0, 1.0, P) == 1.0


def test_health_monotonic_in_vitality() -> None:
    for age in AGES:
        for draw in DRAWS:
            hs = [dc.decline_health(v, age, draw, P) for v in VS]
            assert all(a <= b for a, b in zip(hs, hs[1:])), (age, draw)


def test_every_leaf_dead_at_v_dead() -> None:
    for age in AGES:
        for draw in DRAWS:
            assert dc.decline_health(P.V_DEAD, age, draw, P) == 0.0
            assert dc.decline_health(0.0, age, draw, P) == 0.0
            assert dc.death_point(age, draw, P) >= P.V_DEAD


def test_older_leaves_die_at_higher_vitality() -> None:
    points = [dc.death_point(age, 0.0, P) for age in (0.0, 1.0, 2.0, 3.0)]
    assert all(a < b for a, b in zip(points, points[1:])), points
    assert dc.death_point(3.0, 0.0, P) == dc.death_point(12.0, 0.0, P)  # spread saturates at DECLINE_AGE_REF
    assert dc.death_point(0.0, 0.0, P) == P.V_DEAD


def test_whole_plant_declines_at_once() -> None:
    """Every leaf, even the youngest, has lost some health as soon as v drops below 1 (G12)."""
    for age in AGES:
        assert dc.decline_health(0.9, age, 0.0, P) < 1.0


def test_health_is_the_worse_cause() -> None:
    assert dc.health(0.6, 1.0, 2.0, 0.0, P) == pytest.approx(0.4)
    h_decline = dc.decline_health(0.5, 2.0, 0.0, P)
    assert dc.health(0.0, 0.5, 2.0, 0.0, P) == h_decline
    assert dc.health(1.0, 0.5, 2.0, 0.0, P) == 0.0


def test_look_at_death_is_dry_residual_blade() -> None:
    dead = dc.look(0.0, P)
    assert dead.size_scale == pytest.approx(P.DECLINE_RESIDUAL)
    assert dead.browning == 1.0
    assert dead.extra_angle_rad == pytest.approx(math.radians(P.ANGLE_SENESCENT_DEG + P.SHRIVEL_ANGLE_DEG))
    assert dead.extra_arch_rad == pytest.approx(P.SHRIVEL_ARCH_RAD)
    # no shrivel above SHRIVEL_FRACTION of health
    above = dc.look(P.SHRIVEL_FRACTION, P)
    assert above.size_scale == 1.0 and above.extra_arch_rad == 0.0
    sizes = [dc.look(h / 100, P).size_scale for h in range(101)]
    assert all(a <= b for a, b in zip(sizes, sizes[1:]))


def test_plant_fade_and_point_of_no_return() -> None:
    assert all(dc.plant_fade(v, P) == 0.0 for v in VS if v >= P.V_DEAD)
    assert dc.plant_fade(0.0, P) == 1.0
    fades = [dc.plant_fade(v, P) for v in VS]
    assert all(a >= b for a, b in zip(fades, fades[1:]))
    assert dc.opacity(0.0, 0.0, P) == 0.0
    assert dc.opacity(1.0, 1.0, P) == 0.0
    assert dc.opacity(0.5, P.V_DEAD / 2, P) == pytest.approx(0.25)


def test_tiller_lean_reaches_full_at_v_dead() -> None:
    full = math.radians(P.DECLINE_SPLAY_DEG)
    assert dc.tiller_lean_rad(P.V_DEAD, P) == pytest.approx(full)
    assert dc.tiller_lean_rad(0.0, P) == pytest.approx(full)
    leans = [dc.tiller_lean_rad(v, P) for v in VS]
    assert all(a >= b for a, b in zip(leans, leans[1:]))


@pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan, math.inf])
def test_out_of_range_inputs_raise(bad: float) -> None:
    with pytest.raises(ValueError, match="v must be in"):
        dc.decline_health(bad, 1.0, 0.0, P)
    with pytest.raises(ValueError, match="h must be in"):
        dc.look(bad, P)
    with pytest.raises(ValueError, match="senescence must be in"):
        dc.health(bad, 1.0, 1.0, 0.0, P)
    with pytest.raises(ValueError, match="leaf_fade must be in"):
        dc.opacity(bad, 1.0, P)
    with pytest.raises(ValueError, match="v must be in"):
        dc.plant_fade(bad, P)
    with pytest.raises(ValueError, match="v must be in"):
        dc.tiller_lean_rad(bad, P)


def test_bad_age_and_draw_raise() -> None:
    with pytest.raises(ValueError, match="age"):
        dc.death_point(-1.0, 0.0, P)
    with pytest.raises(ValueError, match="decline_draw"):
        dc.death_point(1.0, math.nan, P)
    with pytest.raises(ValueError, match="decline_draw must be in"):
        dc.death_point(1.0, 1.5, P)


def test_jitter_scales_the_unit_draw() -> None:
    assert dc.death_point(1.0, 1.0, P) - dc.death_point(1.0, 0.0, P) == pytest.approx(
        (1.0 - P.V_DEAD) * P.DECLINE_JITTER)
    no_jitter = replace(P, DECLINE_JITTER=0.0)
    assert dc.death_point(1.0, 1.0, no_jitter) == dc.death_point(1.0, -1.0, no_jitter)


@pytest.mark.parametrize("field, value", [
    ("V_DEAD", 0.0), ("V_DEAD", 1.0), ("DECLINE_AGE_REF", 0.0), ("DECLINE_RESIDUAL", 1.5),
    ("SHRIVEL_FRACTION", 0.0), ("SHRIVEL_ARCH_RAD", math.nan), ("DECLINE_AGE_SPREAD", -0.1),
    ("DECLINE_JITTER", -0.1),
])
def test_bad_params_raise(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        replace(P, **{field: value})


def test_from_mapping() -> None:
    names: dict[str, object] = {f: getattr(P, f) for f in P.__dataclass_fields__}
    names["SEED"] = 1  # unrelated externs are ignored
    assert dc.DeclineParams.from_mapping(names) == P
    del names["V_DEAD"]
    with pytest.raises(ValueError, match="V_DEAD"):
        dc.DeclineParams.from_mapping(names)
    names["V_DEAD"] = "0.15"
    with pytest.raises(ValueError, match="V_DEAD must be a number"):
        dc.DeclineParams.from_mapping(names)


def tiller_fields(tiller: int) -> dict[str, object]:
    if tiller == 0:
        return {"base": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0], "azimuth_rad": 0.4, "splay_rad": 0.0}
    az, splay = 1.0, math.radians(12.0)
    return {"base": [0.004 * math.cos(az), 0.004 * math.sin(az), 0.0], "direction": list(dc.dir_from(az, splay)),
            "azimuth_rad": az, "splay_rad": splay}


def leaf_fields(tiller: int, senescence: float = 0.0, fade: float = 0.0) -> dict[str, object]:
    """Healthy organ-table leaf fields built the way grass.lpy builds them, plus `age`."""
    t = tiller_fields(tiller)
    axis, base = t["direction"], t["base"]
    assert isinstance(axis, list) and isinstance(base, list)
    sheath, az, angle = 0.05, 2.0, 0.5
    return {
        "tiller": tiller, "age": 2.5, "attach": [base[i] + axis[i] * sheath for i in range(3)],
        "direction": list(dc.unit(dc.to_tiller_frame(dc.dir_from(az, angle), axis))), "azimuth_rad": az,
        "angle_from_axis_rad": angle, "sheath_length_m": sheath, "blade_length_m": 0.15, "blade_width_m": 0.01,
        "unroll": 1.0, "arch_rad": 0.7, "senescence": senescence, "fade": fade, "decline_draw": 0.2,
    }


def close(a: object, b: object) -> bool:
    assert isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
    return all(abs(x - y) <= TOL for x, y in zip(a, b))


@pytest.mark.parametrize("tiller", [0, 2])
def test_declined_leaf_healthy_is_identity(tiller: int) -> None:
    leaf = leaf_fields(tiller)
    dl = dc.declined_leaf(leaf, tiller_fields(tiller), 1.0, P)
    assert close(dl.attach, leaf["attach"]) and close(dl.direction, leaf["direction"])
    assert close([dl.sheath_length_m, dl.blade_length_m, dl.blade_width_m, dl.arch_rad],
                 [leaf["sheath_length_m"], leaf["blade_length_m"], leaf["blade_width_m"], leaf["arch_rad"]])
    assert dl.opacity == 1.0 and dl.browning == 0.0


def test_main_shoot_never_leans() -> None:
    for v in VS:
        assert dc.declined_leaf(leaf_fields(0), tiller_fields(0), v, P).tiller_axis == (0.0, 0.0, 1.0)


def test_leaning_tiller_carries_its_leaf() -> None:
    """The collar stays on the leaned axis, sheath * size_scale from the base; the axis splays outward."""
    t = tiller_fields(2)
    base, azimuth, splay = t["base"], t["azimuth_rad"], t["splay_rad"]
    assert isinstance(base, list) and isinstance(azimuth, float) and isinstance(splay, float)
    leaf = leaf_fields(2)
    previous_tilt = -1.0
    for v in VS[::-1]:
        dl = dc.declined_leaf(leaf, t, v, P)
        size_scale = dc.look(dc.health(0.0, v, 2.5, 0.2, P), P).size_scale
        assert close([dl.attach[i] - base[i] for i in range(3)],
                     [dl.tiller_axis[i] * 0.05 * size_scale for i in range(3)]), v
        tilt = math.acos(dl.tiller_axis[2])
        assert tilt >= previous_tilt - TOL
        previous_tilt = tilt
        assert math.atan2(dl.tiller_axis[1], dl.tiller_axis[0]) == pytest.approx(azimuth)
    assert previous_tilt == pytest.approx(splay + math.radians(P.DECLINE_SPLAY_DEG))


def test_declined_leaf_dead_and_faded() -> None:
    dead = dc.declined_leaf(leaf_fields(1, senescence=1.0, fade=0.5), tiller_fields(1), 1.0, P)
    assert dead.browning == 1.0 and dead.opacity == pytest.approx(0.5)
    assert dead.blade_length_m == pytest.approx(0.15 * P.DECLINE_RESIDUAL)
    gone = dc.declined_leaf(leaf_fields(1), tiller_fields(1), 0.0, P)
    assert gone.opacity == 0.0 and gone.browning == 1.0


def test_declined_leaf_bad_fields_raise() -> None:
    leaf = leaf_fields(1)
    del leaf["age"]
    with pytest.raises(ValueError, match="'age' is missing"):
        dc.declined_leaf(leaf, tiller_fields(1), 1.0, P)
    with pytest.raises(ValueError, match="'tiller' must be an int"):
        dc.declined_leaf(dict(leaf_fields(1), tiller=None), tiller_fields(1), 1.0, P)
    with pytest.raises(ValueError, match="'base' must be 3 numbers"):
        dc.declined_leaf(leaf_fields(1), dict(tiller_fields(1), base=[0.0, 0.0]), 1.0, P)
    with pytest.raises(ValueError, match="'blade_width_m' must be a finite number"):
        dc.declined_leaf(dict(leaf_fields(1), blade_width_m=math.nan), tiller_fields(1), 1.0, P)

"""Tests of the decline rule, ../decline.py (stdlib only; runs under any interpreter)."""
from __future__ import annotations

import math
import sys
from collections.abc import Sequence
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


# Per-vertex rule (G23, G32), checked against declined_leaf on a blade built the way G22 builds it.

P_DEFAULT = dc.DeclineParams(  # grass.lpy's defaults
    V_DEAD=0.15, DECLINE_AGE_SPREAD=0.4, DECLINE_AGE_REF=3.0, DECLINE_JITTER=0.1, DECLINE_RESIDUAL=0.7,
    SHRIVEL_FRACTION=1.0,
    ANGLE_SENESCENT_DEG=12.0, SHRIVEL_ANGLE_DEG=25.0, SHRIVEL_ARCH_RAD=0.8, DECLINE_SPLAY_DEG=25.0,
)
BIRTH, TIME = 4.0, 6.5  # leaf_fields has age 2.5
ARCH_BOUND = 0.02  # tip error relative to the declined blade length (G23 amendment)
MIDRIB_STEPS = 2000
FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
Vec = tuple[float, float, float]


def leaf_side(azimuth: float, tiller_axis: object) -> Vec:
    assert isinstance(tiller_axis, (list, tuple))
    return dc.unit(dc.to_tiller_frame((-math.sin(azimuth), math.cos(azimuth), 0.0), tiller_axis))


def midrib(collar: object, direction: object, side: Vec, length: float, arch: float, s: float) -> Vec:
    """Exact (midpoint rule) midrib point at fraction s: direction bent about side by arch * u^2 (G22)."""
    assert isinstance(collar, (list, tuple)) and isinstance(direction, (list, tuple))
    n = max(1, round(MIDRIB_STEPS * s))
    point = [float(c) for c in collar]
    for i in range(n):
        u = (i + 0.5) / n * s
        d = dc.rotate(direction, side, arch * u * u)
        point = [point[k] + d[k] * length * s / n for k in range(3)]
    return (point[0], point[1], point[2])


def vertex_inputs(leaf: dict[str, object], tiller: dict[str, object], kind: int, s: float) -> dc.VertexInputs:
    axis = tiller["direction"]
    az, t_az, draw, sen, fade, arch = (leaf["azimuth_rad"], tiller["azimuth_rad"], leaf["decline_draw"],
                                       leaf["senescence"], leaf["fade"], leaf["arch_rad"])
    assert isinstance(az, float) and isinstance(t_az, float) and isinstance(draw, float)
    assert isinstance(sen, float) and isinstance(fade, float) and isinstance(arch, float)
    tid = leaf["tiller"]
    assert isinstance(tid, int)
    return dc.VertexInputs(
        kind=kind, s=s, collar=tuple(leaf["attach"]), side=leaf_side(az, axis),  # type: ignore[arg-type]
        axis=tuple(leaf["direction"] if kind == dc.KIND_BLADE else axis),  # type: ignore[arg-type]
        tiller_base=tuple(tiller["base"]), tiller_axis=tuple(axis),  # type: ignore[arg-type]
        tiller_azimuth=t_az, tiller=tid, birth_time=BIRTH, decline_draw=draw, senescence=sen, fade=fade,
        arch_rad=arch)


def healthy_midrib(leaf: dict[str, object], tiller: dict[str, object], s: float) -> Vec:
    length, arch, az = leaf["blade_length_m"], leaf["arch_rad"], leaf["azimuth_rad"]
    assert isinstance(length, float) and isinstance(arch, float) and isinstance(az, float)
    return midrib(leaf["attach"], leaf["direction"], leaf_side(az, tiller["direction"]), length, arch, s)


def declined_midrib(leaf: dict[str, object], tiller: dict[str, object], v: float, p: dc.DeclineParams,
                    s: float) -> Vec:
    """Exact midrib point of the leaf rebuilt from declined_leaf (the leaned frame's side axis)."""
    dl = dc.declined_leaf(leaf, tiller, v, p)
    az = leaf["azimuth_rad"]
    assert isinstance(az, float)
    return midrib(dl.attach, dl.direction, leaf_side(az, dl.tiller_axis), dl.blade_length_m, dl.arch_rad, s)


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


@pytest.mark.parametrize("tiller", [0, 2])
def test_declined_vertex_healthy_is_identity(tiller: int) -> None:
    leaf, t = leaf_fields(tiller), tiller_fields(tiller)
    for s in FRACTIONS:
        point = healthy_midrib(leaf, t, s)
        dv = dc.declined_vertex(point, vertex_inputs(leaf, t, dc.KIND_BLADE, s), TIME, 1.0, P_DEFAULT)
        assert close(dv.position, point) and dv.browning == 0.0 and dv.opacity == 1.0
    sheath_point = [0.001, -0.002, 0.03]
    dv = dc.declined_vertex(sheath_point, vertex_inputs(leaf, t, dc.KIND_SHEATH, 0.5), TIME, 1.0, P_DEFAULT)
    assert close(dv.position, sheath_point)


@pytest.mark.parametrize("tiller", [0, 2])
def test_declined_vertex_collar_and_look_match_declined_leaf(tiller: int) -> None:
    for sen in (0.0, 0.4, 1.0):
        leaf, t = dict(leaf_fields(tiller), senescence=sen, fade=0.3 if sen == 1.0 else 0.0), tiller_fields(tiller)
        for v in VS[::10]:
            dl = dc.declined_leaf(leaf, t, v, P_DEFAULT)
            dv = dc.declined_vertex(leaf["attach"], vertex_inputs(leaf, t, dc.KIND_BLADE, 0.0), TIME, v, P_DEFAULT)
            assert close(dv.position, dl.attach), (sen, v)
            assert dv.browning == dl.browning and dv.opacity == dl.opacity


@pytest.mark.parametrize("tiller", [0, 2])
def test_declined_blade_within_arch_bound(tiller: int) -> None:
    """Every midrib point of the declined blade is within ARCH_BOUND of the declined length from the exact one."""
    t = tiller_fields(tiller)
    for arch in (0.0, 0.4, 0.65, 0.9):
        for extra_arch in (0.2, 0.4, 0.8):
            p = replace(P_DEFAULT, SHRIVEL_ARCH_RAD=extra_arch)
            leaf = dict(leaf_fields(tiller), arch_rad=arch, blade_length_m=0.20)
            for v in (0.8, 0.5, 0.3, P_DEFAULT.V_DEAD):
                length = dc.declined_leaf(leaf, t, v, p).blade_length_m
                for s in FRACTIONS:
                    vin = vertex_inputs(leaf, t, dc.KIND_BLADE, s)
                    dv = dc.declined_vertex(healthy_midrib(leaf, t, s), vin, TIME, v, p)
                    error = distance(dv.position, declined_midrib(leaf, t, v, p, s))
                    assert error < ARCH_BOUND * length, (arch, extra_arch, v, s, error / length)


def test_arch_bound_at_the_default_extremes() -> None:
    """Healthy arch 0.9 plus the full SHRIVEL_ARCH_RAD on a 20 cm blade at health 0 (G23)."""
    t = tiller_fields(2)
    leaf = dict(leaf_fields(2), arch_rad=0.9, blade_length_m=0.20, senescence=1.0)
    dl = dc.declined_leaf(leaf, t, 1.0, P_DEFAULT)
    assert dl.arch_rad == pytest.approx(0.9 + P_DEFAULT.SHRIVEL_ARCH_RAD)
    dv = dc.declined_vertex(healthy_midrib(leaf, t, 1.0), vertex_inputs(leaf, t, dc.KIND_BLADE, 1.0), TIME, 1.0,
                            P_DEFAULT)
    assert distance(dv.position, declined_midrib(leaf, t, 1.0, P_DEFAULT, 1.0)) < ARCH_BOUND * dl.blade_length_m


def test_main_shoot_vertices_never_lean_and_tillers_lean_rigidly() -> None:
    no_lean = replace(P_DEFAULT, DECLINE_SPLAY_DEG=0.0)
    for tiller in (0, 2):
        leaf, t = leaf_fields(tiller), tiller_fields(tiller)
        base = t["base"]
        assert isinstance(base, list)
        points = [(healthy_midrib(leaf, t, s), vertex_inputs(leaf, t, dc.KIND_BLADE, s)) for s in FRACTIONS]
        points.append(((0.001, 0.0, 0.02), vertex_inputs(leaf, t, dc.KIND_SHEATH, 0.4)))
        for v in (0.7, 0.3, 0.1):
            leaned = [dc.declined_vertex(pt, vin, TIME, v, P_DEFAULT).position for pt, vin in points]
            upright = [dc.declined_vertex(pt, vin, TIME, v, no_lean).position for pt, vin in points]
            if tiller == 0:
                assert all(close(a, b) for a, b in zip(leaned, upright))
                continue
            assert not close(leaned[-2], upright[-2])
            for i, a in enumerate(leaned):
                assert distance(a, base) == pytest.approx(distance(upright[i], base), abs=1e-12)
                for j in range(i):
                    assert distance(a, leaned[j]) == pytest.approx(distance(upright[i], upright[j]), abs=1e-12)


@pytest.mark.parametrize("tiller", [0, 2])
def test_sheath_vertices_scale_along_the_leaned_axis(tiller: int) -> None:
    leaf, t = dict(leaf_fields(tiller), senescence=0.7), tiller_fields(tiller)
    base, axis = t["base"], t["direction"]
    assert isinstance(base, list) and isinstance(axis, list)
    sheath = 0.05
    vin = vertex_inputs(leaf, t, dc.KIND_SHEATH, 1.0)
    for v in (1.0, 0.6, 0.2):
        dl = dc.declined_leaf(leaf, t, v, P_DEFAULT)
        top = dc.declined_vertex([base[i] + axis[i] * sheath for i in range(3)], vin, TIME, v, P_DEFAULT)
        assert close(top.position, dl.attach)
        mid = dc.declined_vertex([base[i] + axis[i] * 0.02 for i in range(3)], vin, TIME, v, P_DEFAULT).position
        rel = [mid[i] - base[i] for i in range(3)]
        assert close(dc.cross(rel, dl.tiller_axis), (0.0, 0.0, 0.0))  # still on the axis line through the base
        assert math.sqrt(sum(c * c for c in rel)) == pytest.approx(0.02 * dl.sheath_length_m / sheath)


def test_declined_vertex_bad_inputs_raise() -> None:
    leaf, t = leaf_fields(1), tiller_fields(1)
    good = vertex_inputs(leaf, t, dc.KIND_BLADE, 0.5)
    for field, value, message in [
        ("kind", 2, "kind must be"), ("kind", True, "kind must be an int"), ("tiller", -1, "tiller must be >= 0"),
        ("s", 1.5, "s must be in"), ("side", (1.0, 1.0, 0.0), "side must be a unit vector"),
        ("tiller_axis", (0.0, 0.0), "'tiller_axis' must be 3 numbers"), ("senescence", math.nan, "senescence"),
        ("fade", -0.1, "fade must be in"), ("decline_draw", 2.0, "decline_draw must be in"),
        ("arch_rad", math.inf, "'arch_rad' must be a finite number"),
    ]:
        with pytest.raises(ValueError, match=message):
            replace(good, **{field: value})
    with pytest.raises(ValueError, match="axis must be a unit vector"):
        replace(good, axis=(0.0, 0.0, 2.0))
    with pytest.raises(ValueError, match="time must be finite"):
        dc.declined_vertex(leaf["attach"], good, math.nan, 1.0, P_DEFAULT)
    with pytest.raises(ValueError, match="v must be in"):
        dc.declined_vertex(leaf["attach"], good, TIME, 1.5, P_DEFAULT)
    with pytest.raises(ValueError, match="'position' must be 3 numbers"):
        dc.declined_vertex([0.0, 0.0], good, TIME, 1.0, P_DEFAULT)
    with pytest.raises(ValueError, match="beyond the chord approximation"):
        dc.chord_factor(5.0, 1.0)


@pytest.mark.parametrize("tiller", [0, 2])
def test_declined_vertex_wind_pivot_and_axis_match_declined_leaf(tiller: int) -> None:
    for sen in (0.0, 0.5, 1.0):
        leaf, t = dict(leaf_fields(tiller), senescence=sen), tiller_fields(tiller)
        base = t["base"]
        assert isinstance(base, list)
        for v in (1.0, 0.7, 0.4, 0.2):
            dl = dc.declined_leaf(leaf, t, v, P_DEFAULT)
            for s in FRACTIONS:
                blade = dc.declined_vertex(healthy_midrib(leaf, t, s), vertex_inputs(leaf, t, dc.KIND_BLADE, s),
                                           TIME, v, P_DEFAULT)
                assert close(blade.pivot, dl.attach) and close(blade.axis, dl.direction), (sen, v, s)
            sheath = dc.declined_vertex((0.001, 0.0, 0.02), vertex_inputs(leaf, t, dc.KIND_SHEATH, 0.4), TIME, v,
                                        P_DEFAULT)
            assert close(sheath.pivot, base) and close(sheath.axis, dl.tiller_axis), (sen, v)


def test_declined_vertex_before_birth_is_age_zero() -> None:
    """An unborn leaf of a fixed-topology build has a later birth_time; its age clamps at 0."""
    leaf, t = leaf_fields(1), tiller_fields(1)
    vin = vertex_inputs(leaf, t, dc.KIND_BLADE, 1.0)
    point = healthy_midrib(leaf, t, 1.0)
    for v in (1.0, 0.6, 0.3):
        early = dc.declined_vertex(point, vin, BIRTH - 2.0, v, P_DEFAULT)
        at_birth = dc.declined_vertex(point, vin, BIRTH, v, P_DEFAULT)
        assert early == at_birth

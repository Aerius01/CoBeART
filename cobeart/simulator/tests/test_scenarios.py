"""Motion scenario tests: contract validity and scenario-specific behavior."""
import math

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from scipy.spatial.transform import Rotation

from cobeart.simulator.models import Frame, RigidBody
from cobeart.simulator.scenarios import (
    CLAP_PERIOD_S,
    MOTION_SCENARIOS,
    MotionConfig,
    build_motion_scenario,
    clap_times,
    load_body_map,
)
from cobeart.simulator.tests.helpers import motion_config

CFG: MotionConfig = motion_config()
TIMES: list[float] = [0.0, 0.004, 0.5, 1.0, 2.0, 3.99, 4.0, 7.3, 11.0, 29.9]


def _frame(name: str, t: float, seed: int = 0) -> Frame:
    return build_motion_scenario(name, CFG)(t, np.random.default_rng(seed))


def _by_id(frame: Frame) -> dict[int, RigidBody]:
    return {body.ID: body for body in frame.rigidbodies}


@pytest.mark.parametrize("name", sorted(MOTION_SCENARIOS))
def test_every_frame_matches_contract(name: str, frame_validator: Draft202012Validator) -> None:
    for t in TIMES:
        frame_validator.validate(_frame(name, t).to_payload())


@pytest.mark.parametrize("name", sorted(MOTION_SCENARIOS))
def test_quaternions_are_unit_and_ids_unique(name: str) -> None:
    for t in TIMES:
        frame: Frame = _frame(name, t)
        ids: list[int] = [b.ID for b in frame.rigidbodies]
        assert len(ids) == len(set(ids))
        for body in frame.rigidbodies:
            assert math.isclose(math.hypot(body.qx, body.qy, body.qz, body.qw), 1.0, abs_tol=1e-9)


def test_same_seed_same_frame() -> None:
    assert _frame("shuffled", 1.5, seed=3) == _frame("shuffled", 1.5, seed=3)


def test_hands_use_body_map_ids() -> None:
    body_map: dict[str, int] = load_body_map()
    ids: set[int] = set(_by_id(_frame("hands", 1.0)))
    assert ids == {body_map["left_hand"], body_map["right_hand"], body_map["head"]}


def test_hands_clap_within_200mm_at_expected_times_and_not_between() -> None:
    body_map: dict[str, int] = load_body_map()

    def distance(t: float) -> float:
        bodies: dict[int, RigidBody] = _by_id(_frame("hands", t))
        left, right = bodies[body_map["left_hand"]], bodies[body_map["right_hand"]]
        return math.dist((left.x, left.y, left.z), (right.x, right.y, right.z))

    for clap in clap_times(20.0):
        assert distance(clap) < 200.0
    assert distance(CLAP_PERIOD_S / 2) > 800.0


def test_head_nod_lean_follows_quaternion_convention() -> None:
    leans: list[float] = []
    for t in np.linspace(0.0, 2.0, 41):
        head: RigidBody = _by_id(_frame("hands", float(t)))[CFG.head_id]
        forward: np.ndarray = Rotation.from_quat([head.qx, head.qy, head.qz, head.qw]).apply([0.0, 1.0, 0.0])
        leans.append(math.degrees(math.asin(-forward[2])))
    assert max(leans) > 20.0 and min(leans) < 0.0
    assert all(-90.0 <= lean <= 60.0 for lean in leans)


def test_orbit_angular_velocity_matches_yaw_rate() -> None:
    body: RigidBody = _by_id(_frame("orbit", 1.0))[0]
    assert body.wz == pytest.approx(math.degrees(0.8), rel=1e-3)
    assert body.vz == pytest.approx(0.0, abs=1e-6)


def test_still_goes_to_zero_velocity() -> None:
    moving: Frame = _frame("still", 1.0)
    stopped: Frame = _frame("still", 10.0)
    assert any(b.norm_abs_vel > 0 for b in moving.rigidbodies)
    for body in stopped.rigidbodies:
        assert body.abs_vel == pytest.approx(0.0, abs=1e-6)
        assert body.norm_abs_vel == 0.0
        assert (body.wx, body.wy, body.wz) == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)


def test_missing_body_drops_one_hand_in_gap() -> None:
    body_map: dict[str, int] = load_body_map()
    assert len(_frame("missing-body", 1.0).rigidbodies) == 3
    gap_ids: set[int] = set(_by_id(_frame("missing-body", 5.0)))
    assert body_map["left_hand"] not in gap_ids and body_map["right_hand"] in gap_ids
    next_gap: set[int] = set(_by_id(_frame("missing-body", 11.0)))
    assert body_map["right_hand"] not in next_gap and body_map["left_hand"] in next_gap


def test_shuffled_keeps_id_set_but_varies_order() -> None:
    scenario = build_motion_scenario("shuffled", CFG)
    rng = np.random.default_rng(0)
    orders: set[tuple[int, ...]] = set()
    for i in range(50):
        frame: Frame = scenario(i / 240, rng)
        ids: tuple[int, ...] = tuple(b.ID for b in frame.rigidbodies)
        assert sorted(ids) == list(range(CFG.num_bodies))
        orders.add(ids)
    assert len(orders) > 1


def test_edge_reaches_and_passes_arena_bounds() -> None:
    xs: list[int] = []
    zs: list[int] = []
    for t in np.arange(0.0, 60.0, 0.05):
        for body in _frame("edge", float(t)).rigidbodies:
            xs.append(body.x)
            zs.append(body.z)
    assert max(xs) > CFG.arena_x[1] and min(xs) < CFG.arena_x[0]
    assert max(zs) > CFG.arena_z[1] and min(zs) < CFG.arena_z[0]


def test_too_many_bodies_rejected() -> None:
    with pytest.raises(ValueError, match="num_bodies"):
        build_motion_scenario("orbit", motion_config(num_bodies=11))(0.0, np.random.default_rng(0))

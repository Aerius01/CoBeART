"""Motion scenario tests: contract validity and scenario-specific behavior."""
import math

import numpy as np
import pytest
from jsonschema import Draft202012Validator
from scipy.spatial.transform import Rotation

from cobeart.simulator.models import PoseBody, PoseFrame
from cobeart.simulator.scenarios import (
    CLAP_PERIOD_S,
    MOTION_SCENARIOS,
    MotionConfig,
    build_motion_scenario,
    clap_times,
)
from cobeart.simulator.tests.helpers import SETTINGS, motion_config

CFG: MotionConfig = motion_config()
TIMES: list[float] = [0.0, 0.004, 0.5, 1.0, 2.0, 3.99, 4.0, 7.3, 11.0, 29.9]
ROLES_BY_ID: list[str] = [role for _, role in sorted(SETTINGS.tracking.roles.items())]


def _frame(name: str, t: float, seed: int = 0) -> PoseFrame:
    return build_motion_scenario(name, CFG)(t, np.random.default_rng(seed))


@pytest.mark.parametrize("name", sorted(MOTION_SCENARIOS))
def test_every_frame_matches_contract(name: str, pose_frame_validator: Draft202012Validator) -> None:
    for t in TIMES:
        pose_frame_validator.validate(_frame(name, t).to_payload())


def test_shuffled_scenario_is_gone() -> None:
    assert "shuffled" not in MOTION_SCENARIOS


@pytest.mark.parametrize("name", sorted(MOTION_SCENARIOS))
def test_quaternions_are_unit_and_source_time_is_scenario_time(name: str) -> None:
    for t in TIMES:
        frame: PoseFrame = _frame(name, t)
        assert frame.source_time_s == t
        for body in frame.bodies.values():
            assert math.isclose(math.hypot(body.qx, body.qy, body.qz, body.qw), 1.0, abs_tol=1e-9)


def test_same_seed_same_frame() -> None:
    assert _frame("orbit", 1.5, seed=3) == _frame("orbit", 1.5, seed=3)


def test_payloads_carry_pose_only_with_integer_positions() -> None:
    payload = _frame("orbit", 1.0).to_payload()
    assert set(payload) == {"schemaVersion", "source_time_s", "bodies"}
    assert payload["schemaVersion"] == 2
    for body in payload["bodies"].values():
        assert set(body) == {"x", "y", "z", "qx", "qy", "qz", "qw"}
        assert all(isinstance(body[axis], int) for axis in "xyz")


def test_bodies_option_takes_the_first_n_roles_in_motive_id_order() -> None:
    for count in (1, 3, len(ROLES_BY_ID)):
        frame: PoseFrame = build_motion_scenario("orbit", motion_config(count))(0.0, np.random.default_rng(0))
        assert list(frame.bodies) == ROLES_BY_ID[:count]


@pytest.mark.parametrize("count", [0, len(ROLES_BY_ID) + 1])
def test_body_count_outside_configured_roles_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="num_bodies"):
        motion_config(count)


def test_hands_use_the_hand_and_head_roles() -> None:
    assert set(_frame("hands", 1.0).bodies) == {"left_hand", "right_hand", "head"}


def test_hands_clap_within_200mm_at_expected_times_and_not_between() -> None:
    def distance(t: float) -> float:
        bodies: dict[str, PoseBody] = dict(_frame("hands", t).bodies)
        left, right = bodies["left_hand"], bodies["right_hand"]
        return math.dist((left.x, left.y, left.z), (right.x, right.y, right.z))

    for clap in clap_times(20.0):
        assert distance(clap) < 200.0
    assert distance(CLAP_PERIOD_S / 2) > 800.0


def test_head_nod_lean_follows_quaternion_convention() -> None:
    leans: list[float] = []
    for t in np.linspace(0.0, 2.0, 41):
        head: PoseBody = _frame("hands", float(t)).bodies["head"]
        forward: np.ndarray = Rotation.from_quat([head.qx, head.qy, head.qz, head.qw]).apply([0.0, 1.0, 0.0])
        leans.append(math.degrees(math.asin(-forward[2])))
    assert max(leans) > 20.0 and min(leans) < 0.0
    assert all(-90.0 <= lean <= 60.0 for lean in leans)


def test_orbit_bodies_move_at_their_analytic_speed() -> None:
    # Body i circles at radius 900 + 250 i mm with angular speed 0.8 / (1 + 0.25 i) rad/s (WP 6.4 relies on this).
    step_s: float = 0.1  # positions are integer mm, so differentiate over a longer step
    scenario = build_motion_scenario("orbit", CFG)
    rng = np.random.default_rng(0)
    first, second = scenario(1.0, rng), scenario(1.0 + step_s, rng)
    for i, role in enumerate(CFG.roles):
        a, b = first.bodies[role], second.bodies[role]
        expected: float = (900.0 + 250.0 * i) * 0.8 / (1.0 + 0.25 * i)
        assert math.dist((a.x, a.y), (b.x, b.y)) / step_s == pytest.approx(expected, rel=0.02)


def test_still_comes_to_rest() -> None:
    moving: PoseFrame = _frame("still", 1.0)
    assert any(moving.bodies[role] != _frame("still", 1.1).bodies[role] for role in moving.bodies)
    assert _frame("still", 10.0).bodies == _frame("still", 11.0).bodies


def test_missing_body_drops_one_hand_in_gap() -> None:
    assert len(_frame("missing-body", 1.0).bodies) == 3
    gap_roles: set[str] = set(_frame("missing-body", 5.0).bodies)
    assert "left_hand" not in gap_roles and "right_hand" in gap_roles
    next_gap: set[str] = set(_frame("missing-body", 11.0).bodies)
    assert "right_hand" not in next_gap and "left_hand" in next_gap


def test_edge_reaches_and_passes_arena_bounds() -> None:
    xs: list[int] = []
    zs: list[int] = []
    for t in np.arange(0.0, 60.0, 0.05):
        for body in _frame("edge", float(t)).bodies.values():
            xs.append(body.x)
            zs.append(body.z)
    assert max(xs) > CFG.arena_x[1] and min(xs) < CFG.arena_x[0]
    assert max(zs) > CFG.arena_z[1] and min(zs) < CFG.arena_z[0]

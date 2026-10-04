import logging

import pytest

from cobeart.optitrackclient.tracked_bodies import apply_rigid_body
from cobeart.optitrackclient.transform import Quaternion, Vec3, is_valid_pose

POS: Vec3 = (1.0, 2.0, 3.0)
QUAT: Quaternion = (0.0, 0.0, 0.0, 1.0)
NAN: float = float("nan")
INF: float = float("inf")


def test_valid_pose() -> None:
    assert is_valid_pose(POS, QUAT)


@pytest.mark.parametrize("position", [(NAN, 0.0, 0.0), (0.0, INF, 0.0), (0.0, 0.0, -INF)])
def test_non_finite_position_is_invalid(position: Vec3) -> None:
    assert not is_valid_pose(position, QUAT)


@pytest.mark.parametrize("rotation", [(0.0, NAN, 0.0, 1.0), (0.0, 0.0, 0.0, INF)])
def test_non_finite_quaternion_is_invalid(rotation: Quaternion) -> None:
    assert not is_valid_pose(POS, rotation)


def test_zero_quaternion_is_invalid() -> None:
    assert not is_valid_pose(POS, (0.0, 0.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("position", "rotation", "tracking_valid"),
    [(POS, QUAT, False), ((NAN, 0.0, 0.0), QUAT, True), (POS, (0.0, 0.0, 0.0, 0.0), True)],
)
def test_untracked_or_invalid_body_is_dropped_others_kept(
    position: Vec3, rotation: Quaternion, tracking_valid: bool
) -> None:
    bodies: dict[int, list[float]] = {}
    dropped: set[int] = set()
    apply_rigid_body(bodies, dropped, 0, POS, QUAT, True)
    apply_rigid_body(bodies, dropped, 1, POS, QUAT, True)
    apply_rigid_body(bodies, dropped, 1, position, rotation, tracking_valid)
    assert set(bodies) == {0}
    assert dropped == {1}


def test_returning_body_is_stored_again(caplog: pytest.LogCaptureFixture) -> None:
    bodies: dict[int, list[float]] = {}
    dropped: set[int] = set()
    with caplog.at_level(logging.INFO):
        apply_rigid_body(bodies, dropped, 2, POS, QUAT, True)
        for _ in range(5):
            apply_rigid_body(bodies, dropped, 2, POS, QUAT, False)
        assert 2 not in bodies
        apply_rigid_body(bodies, dropped, 2, POS, QUAT, True)
        apply_rigid_body(bodies, dropped, 2, POS, QUAT, True)
    assert bodies[2] == [-1000.0, 3000.0, 2000.0, -0.0, 0.0, 0.0, 1.0]
    assert dropped == set()
    assert len(caplog.records) == 2  # one dropout, one return, never per frame

import math

import numpy as np
import numpy.typing as npt
import pytest
from scipy.spatial.transform import Rotation

from cobeart.optitrackclient.transform import (
    ArenaPose,
    Quaternion,
    pose_to_arena,
    position_to_arena,
    quaternion_to_arena,
)

# Arena axes as rows of OptiTrack axes: x = -X, y = Z, z = Y.
P: npt.NDArray[np.float64] = np.array([[-1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]])
FORWARD: npt.NDArray[np.float64] = np.array([0.0, 1.0, 0.0])
UP: npt.NDArray[np.float64] = np.array([0.0, 0.0, 1.0])


def optitrack_quat(axis: tuple[float, float, float], degrees: float) -> Quaternion:
    """OptiTrack quaternion for a rotation about an OptiTrack-frame axis."""
    rotvec: npt.NDArray[np.float64] = np.radians(degrees) * np.asarray(axis, dtype=float)
    qx, qy, qz, qw = Rotation.from_rotvec(rotvec).as_quat()
    return (float(qx), float(qy), float(qz), float(qw))


def arena_axis(q: Quaternion, axis: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Body axis expressed in arena axes."""
    return Rotation.from_quat(q).apply(axis)


def forward_lean_deg(q: Quaternion) -> float:
    """Forward lean as the contract defines it: asin(-forward.z)."""
    return math.degrees(math.asin(-arena_axis(q, FORWARD)[2]))


def test_identity_maps_to_identity() -> None:
    q: Quaternion = quaternion_to_arena((0.0, 0.0, 0.0, 1.0))
    assert np.allclose(q, (0.0, 0.0, 0.0, 1.0))
    assert np.allclose(arena_axis(q, FORWARD), FORWARD)
    assert np.allclose(arena_axis(q, UP), UP)


def test_position_axis_remap() -> None:
    assert np.allclose(position_to_arena((0.1, 1.5, -0.2)), (-100.0, -200.0, 1500.0))


def test_pose_to_arena_combines_both() -> None:
    pose: ArenaPose = pose_to_arena((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0))
    assert np.allclose(pose.position, (-1000.0, 3000.0, 2000.0))
    assert np.allclose(pose.orientation, (0.0, 0.0, 0.0, 1.0))


def test_forward_lean_is_positive_and_keeps_heading() -> None:
    # Nodding forward in Motive: rotation about OptiTrack +X tips the facing axis +Z down toward -Y.
    q: Quaternion = quaternion_to_arena(optitrack_quat((1.0, 0.0, 0.0), 20.0))
    forward: npt.NDArray[np.float64] = arena_axis(q, FORWARD)
    assert forward_lean_deg(q) == pytest.approx(20.0)
    assert forward[0] == pytest.approx(0.0, abs=1e-12)  # heading still along +y
    assert forward[1] > 0.0


def test_turn_in_place_stays_horizontal() -> None:
    # Rotation about the OptiTrack vertical (+Y) is rotation about arena +z: +y turns toward -x.
    q: Quaternion = quaternion_to_arena(optitrack_quat((0.0, 1.0, 0.0), 30.0))
    forward: npt.NDArray[np.float64] = arena_axis(q, FORWARD)
    assert np.allclose(forward, (-math.sin(math.radians(30.0)), math.cos(math.radians(30.0)), 0.0))
    assert np.allclose(arena_axis(q, UP), UP)
    assert forward_lean_deg(q) == pytest.approx(0.0, abs=1e-12)


def test_side_lean_tilts_up_toward_x_without_forward_lean() -> None:
    # Rotation about the OptiTrack facing axis (+Z, arena +y) tilts up toward -X, i.e. arena +x.
    q: Quaternion = quaternion_to_arena(optitrack_quat((0.0, 0.0, 1.0), 25.0))
    up: npt.NDArray[np.float64] = arena_axis(q, UP)
    assert up[0] == pytest.approx(math.sin(math.radians(25.0)))
    assert up[1] == pytest.approx(0.0, abs=1e-12)
    assert forward_lean_deg(q) == pytest.approx(0.0, abs=1e-12)
    q_other: Quaternion = quaternion_to_arena(optitrack_quat((0.0, 0.0, 1.0), -25.0))
    assert arena_axis(q_other, UP)[0] == pytest.approx(-math.sin(math.radians(25.0)))


def test_remap_equals_matrix_conjugation_and_is_proper_unit() -> None:
    rng: np.random.Generator = np.random.default_rng(1234)
    for _ in range(200):
        raw = rng.normal(size=4)
        q_ot: Quaternion = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
        q: Quaternion = quaternion_to_arena(q_ot)
        assert np.linalg.norm(q) == pytest.approx(1.0)
        r_arena = Rotation.from_quat(q).as_matrix()
        expected = P @ Rotation.from_quat(q_ot).as_matrix() @ P
        assert np.allclose(r_arena, expected)
        assert np.linalg.det(expected) == pytest.approx(1.0)
        # A world point rotated then remapped equals the remapped point rotated by the remapped rotation.
        v = rng.normal(size=3)
        assert np.allclose(P @ Rotation.from_quat(q_ot).apply(v), Rotation.from_quat(q).apply(P @ v))


def test_zero_quaternion_raises() -> None:
    with pytest.raises(ValueError, match="zero-length"):
        quaternion_to_arena((0.0, 0.0, 0.0, 0.0))

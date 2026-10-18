"""OptiTrack (Motive) pose to arena pose, as pure functions.

Conventions (decision D5, provisional pending an on-site check):

- OptiTrack frame: Motive streams positions in metres in a right-handed, Y-up frame,
  and orientations as unit quaternions (qx, qy, qz, qw), scalar last.
- Arena frame: right-handed, x and y horizontal, z up, positions in millimetres.
- Axis remap: x = -X, y = Z, z = Y (capital letters are OptiTrack axes). As a matrix
  this is P = [[-1, 0, 0], [0, 0, 1], [0, 1, 0]]. P is orthogonal, symmetric and has
  determinant +1: the y/z swap and the x negation are each a reflection, and together
  they form a proper rotation (180 degrees about the OptiTrack axis (0, 1, 1)/sqrt(2)).
- Orientation remap: R_arena = P R P (P equals its own inverse). Because P is a proper
  rotation this is a change of basis, so the quaternion keeps its scalar part and its
  vector part maps like a position: (qx, qy, qz, qw) -> (-qx, qz, qy, qw).
- Identity orientation: at the identity quaternion a body faces arena +y with up = +z
  and right = +x, i.e. in OptiTrack terms it faces +Z with up = +Y. This holds only if
  each rigid body was created in Motive with its local axes aligned to the global axes
  while the performer faced OptiTrack +Z. Verify on site.
- Derived scalars: forward = q applied to (0, 1, 0); forward lean = asin(-forward.z),
  positive when leaning forward.
"""
import math
from dataclasses import dataclass

Vec3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]  # (qx, qy, qz, qw), scalar last

METRES_TO_MM: float = 1000.0
MIN_QUATERNION_NORM: float = 1e-9


@dataclass(frozen=True, slots=True)
class ArenaPose:
    """Rigid-body pose in arena axes."""
    position: Vec3  # [mm]
    orientation: Quaternion  # unit quaternion (qx, qy, qz, qw)


def is_valid_pose(position: Vec3, rotation: Quaternion) -> bool:
    """True if all 7 values are finite and the quaternion has non-zero length."""
    if not all(math.isfinite(v) for v in (*position, *rotation)):
        return False
    return math.sqrt(sum(c * c for c in rotation)) >= MIN_QUATERNION_NORM


def position_to_arena(position: Vec3) -> Vec3:
    """Map an OptiTrack position in metres to arena millimetres."""
    x, y, z = position
    return (-x * METRES_TO_MM, z * METRES_TO_MM, y * METRES_TO_MM)


def quaternion_to_arena(rotation: Quaternion) -> Quaternion:
    """Map an OptiTrack orientation quaternion to a unit quaternion in arena axes."""
    qx, qy, qz, qw = rotation
    norm: float = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm < MIN_QUATERNION_NORM:
        raise ValueError(f"Cannot remap a zero-length orientation quaternion: {rotation}")
    return (-qx / norm, qz / norm, qy / norm, qw / norm)


def pose_to_arena(position: Vec3, rotation: Quaternion) -> ArenaPose:
    """Map an OptiTrack position (metres) and quaternion to an arena pose."""
    return ArenaPose(position=position_to_arena(position), orientation=quaternion_to_arena(rotation))

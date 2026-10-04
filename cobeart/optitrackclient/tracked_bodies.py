"""Keeps the per-frame set of tracked rigid bodies, dropping bodies whose pose is not usable."""
import logging

from cobeart.optitrackclient.transform import Quaternion, Vec3, is_valid_pose, pose_to_arena

logger: logging.Logger = logging.getLogger(__name__)


def apply_rigid_body(
    bodies: dict[int, list[float]],
    dropped: set[int],
    new_id: int,
    position: Vec3,
    rotation: Quaternion,
    tracking_valid: bool,
) -> None:
    """Store the body as [x, y, z, qx, qy, qz, qw] in arena axes, or remove it if untracked or invalid."""
    if tracking_valid and is_valid_pose(position, rotation):
        pose = pose_to_arena(position, rotation)
        bodies[new_id] = [*pose.position, *pose.orientation]
        if new_id in dropped:
            dropped.discard(new_id)
            logger.info("Rigid body %d is tracked again", new_id)
        return
    bodies.pop(new_id, None)
    if new_id not in dropped:
        dropped.add(new_id)
        logger.info("Rigid body %d dropped out (tracking invalid or non-finite pose)", new_id)

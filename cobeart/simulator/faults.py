"""Fault injection: pure functions turning a valid payload into one the contract rejects."""
from collections.abc import Callable
from typing import Any

Payload = dict[str, Any]
Fault = Callable[[Payload], Payload]

WRONG_SCHEMA_VERSION: int = 1  # the pre-pose_frame contract version


def _malformed(payload: Payload) -> Payload:
    """Drop a required field and corrupt another field's type."""
    broken: Payload = dict(payload)
    if "bodies" in broken:
        del broken["bodies"]
        broken["source_time_s"] = "now"
    else:
        del broken["rms"]
        broken["peak"] = "loud"
    return broken


def _wrong_version(payload: Payload) -> Payload:
    return {**payload, "schemaVersion": WRONG_SCHEMA_VERSION}


def _nan(payload: Payload) -> Payload:
    """NaN in a coordinate (pose_frame; in the timestamp if no body is present) or in rms (audio); not valid JSON."""
    broken: Payload = dict(payload)
    if "bodies" in broken:
        bodies: dict[str, Payload] = {role: dict(body) for role, body in broken["bodies"].items()}
        if bodies:
            bodies[next(iter(bodies))]["x"] = float("nan")
        else:
            broken["source_time_s"] = float("nan")
        broken["bodies"] = bodies
    else:
        broken["rms"] = float("nan")
    return broken


FAULTS: dict[str, Fault] = {
    "malformed": _malformed,
    "wrong-version": _wrong_version,
    "nan": _nan,
}


def apply_fault(name: str | None, payload: Payload) -> Payload:
    """Return the payload with the named fault applied, or unchanged when `name` is None."""
    if name is None:
        return payload
    if name not in FAULTS:
        raise ValueError(f"Unknown fault {name!r}, choose from {sorted(FAULTS)}")
    return FAULTS[name](payload)

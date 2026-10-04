"""Fault injection: pure functions turning a valid payload into one the contract rejects."""
from collections.abc import Callable
from typing import Any

Payload = dict[str, Any]
Fault = Callable[[Payload], Payload]

WRONG_SCHEMA_VERSION: int = 2


def _malformed(payload: Payload) -> Payload:
    """Drop a required field and corrupt another field's type."""
    broken: Payload = dict(payload)
    if "rigidbodies" in broken:
        del broken["rigidbodies"]
        broken["type"] = 42
    else:
        del broken["rms"]
        broken["peak"] = "loud"
    return broken


def _wrong_version(payload: Payload) -> Payload:
    return {**payload, "schemaVersion": WRONG_SCHEMA_VERSION}


def _nan(payload: Payload) -> Payload:
    """NaN in a coordinate (frame) or in rms (audio); neither is valid JSON for the hub."""
    broken: Payload = dict(payload)
    if "rigidbodies" in broken:
        bodies: list[Payload] = [dict(body) for body in broken["rigidbodies"]]
        if bodies:
            bodies[0]["x"] = float("nan")
            bodies[0]["vx"] = float("nan")
        broken["rigidbodies"] = bodies
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

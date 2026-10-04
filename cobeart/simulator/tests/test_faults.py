"""Each fault must produce a payload the contract rejects."""
import json
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from cobeart.simulator.audio import AudioConfig, build_audio_scenario
from cobeart.simulator.faults import FAULTS, apply_fault
from cobeart.simulator.scenarios import MotionConfig, build_motion_scenario


def _frame_payload() -> dict[str, Any]:
    scenario = build_motion_scenario("orbit", MotionConfig(start_epoch_ms=1.8e12))
    return scenario(1.0, np.random.default_rng(0)).to_payload()


def _audio_payload() -> dict[str, Any]:
    return build_audio_scenario("beat", AudioConfig())(1.0, np.random.default_rng(0)).to_payload()


def test_baseline_payloads_are_valid(
    frame_validator: Draft202012Validator, audio_validator: Draft202012Validator
) -> None:
    frame_validator.validate(_frame_payload())
    audio_validator.validate(_audio_payload())


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_fault_frame_rejected_by_schema(fault: str, frame_validator: Draft202012Validator) -> None:
    assert not frame_validator.is_valid(apply_fault(fault, _frame_payload()))


@pytest.mark.parametrize("fault", ["malformed", "wrong-version"])
def test_fault_audio_rejected_by_schema(fault: str, audio_validator: Draft202012Validator) -> None:
    assert not audio_validator.is_valid(apply_fault(fault, _audio_payload()))


def test_nan_audio_is_not_strict_json() -> None:
    # The schema cannot express "finite" for plain numbers, but strict JSON (what the hub's parser needs) rejects NaN.
    with pytest.raises(ValueError):
        json.dumps(apply_fault("nan", _audio_payload()), allow_nan=False)


def test_none_fault_is_identity_and_unknown_raises() -> None:
    payload = _frame_payload()
    assert apply_fault(None, payload) is payload
    with pytest.raises(ValueError, match="Unknown fault"):
        apply_fault("bogus", payload)

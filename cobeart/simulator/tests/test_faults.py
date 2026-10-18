"""Each fault must produce a payload the contract rejects."""
import json
from typing import Any

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from cobeart.simulator.audio import build_audio_scenario
from cobeart.simulator.faults import FAULTS, apply_fault
from cobeart.simulator.scenarios import build_motion_scenario
from cobeart.simulator.tests.helpers import audio_config, motion_config


def _pose_payload() -> dict[str, Any]:
    scenario = build_motion_scenario("orbit", motion_config())
    return scenario(1.0, np.random.default_rng(0)).to_payload()


def _audio_payload() -> dict[str, Any]:
    return build_audio_scenario("beat", audio_config())(1.0, np.random.default_rng(0)).to_payload()


def test_baseline_payloads_are_valid(
    pose_frame_validator: Draft202012Validator, audio_validator: Draft202012Validator
) -> None:
    pose_frame_validator.validate(_pose_payload())
    audio_validator.validate(_audio_payload())


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_fault_pose_frame_rejected_by_schema(fault: str, pose_frame_validator: Draft202012Validator) -> None:
    assert not pose_frame_validator.is_valid(apply_fault(fault, _pose_payload()))


@pytest.mark.parametrize("fault", ["malformed", "wrong-version"])
def test_fault_audio_rejected_by_schema(fault: str, audio_validator: Draft202012Validator) -> None:
    assert not audio_validator.is_valid(apply_fault(fault, _audio_payload()))


def test_nan_audio_is_not_strict_json() -> None:
    # The schema cannot express "finite" for plain numbers, but strict JSON (what the hub's parser needs) rejects NaN.
    with pytest.raises(ValueError):
        json.dumps(apply_fault("nan", _audio_payload()), allow_nan=False)


def test_wrong_version_is_the_old_contract_version() -> None:
    assert apply_fault("wrong-version", _pose_payload())["schemaVersion"] == 1


def test_nan_pose_is_not_strict_json() -> None:
    with pytest.raises(ValueError):
        json.dumps(apply_fault("nan", _pose_payload()), allow_nan=False)


def test_nan_in_an_empty_pose_frame_hits_the_timestamp() -> None:
    payload = apply_fault("nan", {"schemaVersion": 2, "source_time_s": 1.0, "bodies": {}})
    assert payload["source_time_s"] != payload["source_time_s"]


def test_none_fault_is_identity_and_unknown_raises() -> None:
    payload = _pose_payload()
    assert apply_fault(None, payload) is payload
    with pytest.raises(ValueError, match="Unknown fault"):
        apply_fault("bogus", payload)

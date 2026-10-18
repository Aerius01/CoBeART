"""Shared fixtures: the contract validators for the payloads the simulator produces."""
import pytest
from jsonschema import Draft202012Validator

from tests.contract.schemas import contract_validator


@pytest.fixture(scope="session")
def pose_frame_validator() -> Draft202012Validator:
    return contract_validator("pose_frame")


@pytest.fixture(scope="session")
def audio_validator() -> Draft202012Validator:
    return contract_validator("audio_metrics")

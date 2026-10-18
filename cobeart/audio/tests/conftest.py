"""Shared fixtures: the audio_metrics contract validator, and a hang guard for the threaded capture tests."""
import faulthandler
import sys
from collections.abc import Iterator

import pytest
from jsonschema import Draft202012Validator

from tests.contract.schemas import contract_validator

# Far above the slowest test here (about 15 s with every CPU core busy), so it only fires on a real hang.
HANG_TIMEOUT_S: float = 120.0


@pytest.fixture(autouse=True)
def hang_guard() -> Iterator[None]:
    """Dump every thread's stack and end the run if a test hangs (threads, child processes, device waits)."""
    faulthandler.dump_traceback_later(HANG_TIMEOUT_S, exit=True, file=sys.stderr)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(scope="session")
def audio_validator() -> Draft202012Validator:
    return contract_validator("audio_metrics")

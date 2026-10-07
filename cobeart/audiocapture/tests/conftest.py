"""Shared fixtures: the audio_metrics contract validator, and a hang guard for the threaded capture tests."""
import faulthandler
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACT_DIR: Path = Path(__file__).resolve().parents[3] / "contract"
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
    registry: Registry = Registry()
    for name in ("frame", "audio_metrics", "splat"):
        schema = json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text())
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema, DRAFT202012))
    schema = json.loads((CONTRACT_DIR / "audio_metrics.schema.json").read_text())
    return Draft202012Validator(schema, registry=registry)

"""Shared fixtures: the audio_metrics contract validator built from contract/*.schema.json."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACT_DIR: Path = Path(__file__).resolve().parents[3] / "contract"


@pytest.fixture(scope="session")
def audio_validator() -> Draft202012Validator:
    registry: Registry = Registry()
    for name in ("frame", "audio_metrics", "splat"):
        schema = json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text())
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema, DRAFT202012))
    schema = json.loads((CONTRACT_DIR / "audio_metrics.schema.json").read_text())
    return Draft202012Validator(schema, registry=registry)

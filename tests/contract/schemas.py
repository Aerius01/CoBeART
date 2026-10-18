"""Contract validators built from contract/*.schema.json, and the valid example messages in fixtures/."""
import json
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACT_DIR: Path = Path(__file__).resolve().parents[2] / "contract"
FIXTURE_DIR: Path = Path(__file__).resolve().parent / "fixtures"

type SchemaName = Literal["pose_frame", "frame", "audio_metrics", "audio"]
SCHEMA_NAMES: tuple[SchemaName, ...] = ("pose_frame", "frame", "audio_metrics", "audio")


def load_schema(name: SchemaName) -> dict[str, Any]:
    """The parsed contract/<name>.schema.json."""
    schema: dict[str, Any] = json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    return schema


def contract_registry() -> Registry:
    """A registry holding every contract schema under its $id, so cross-file $refs resolve."""
    return Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema, DRAFT202012))
        for schema in (load_schema(name) for name in SCHEMA_NAMES)
    )


def contract_validator(name: SchemaName) -> Draft202012Validator:
    """Draft 2020-12 validator for contract/<name>.schema.json."""
    return Draft202012Validator(load_schema(name), registry=contract_registry())


def load_fixture(name: SchemaName) -> dict[str, Any]:
    """A fresh copy of the valid example message fixtures/<name>.json."""
    fixture: dict[str, Any] = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return fixture

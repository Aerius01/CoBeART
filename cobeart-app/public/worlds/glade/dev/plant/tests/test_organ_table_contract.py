"""Ties grass_geometry's organ-table model to lpy/organ_table.schema.json, so a schema change fails here first."""
from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest

import grass_geometry as gg
from bake_grass_vat import bake

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "lpy" / "organ_table.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    return document


@pytest.fixture(scope="module")
def table(organ_table_path: Path) -> gg.OrganTable:
    return gg.load_table(organ_table_path)


def field_names(cls: type) -> set[str]:
    return {f.name for f in fields(cls)}


def required(schema: dict[str, Any], name: str) -> set[str]:
    return set(schema["$defs"][name]["required"])


def test_schema_version_matches(schema: dict[str, Any]) -> None:
    assert schema["properties"]["schema_version"]["const"] == gg.SCHEMA_VERSION


def test_leaf_fields_match_schema(schema: dict[str, Any]) -> None:
    # kind is implied by the class; senescence_duration lives in LeafEvents.
    assert field_names(gg.Leaf) == required(schema, "leaf") - {"kind", "senescence_duration"}


def test_tiller_fields_match_schema(schema: dict[str, Any]) -> None:
    # kind is implied by the class; a tiller's age and state are not used by the mesh.
    assert field_names(gg.Tiller) == required(schema, "tiller") - {"kind", "age", "state"}


def test_leaf_events_fields_match_schema(schema: dict[str, Any]) -> None:
    assert field_names(gg.LeafEvents) == required(schema, "leafEvents")


def test_missing_model_param_names_the_key(table: gg.OrganTable) -> None:
    params = {k: v for k, v in table.params.items() if k != "SHEATH_PLATEAU_M"}
    broken = replace(table, params=params)
    p = gg.GeometryParams()
    with pytest.raises(gg.OrganTableError, match="SHEATH_PLATEAU_M"):
        gg.build_plant(broken, table.sample_times[0], p)
    with pytest.raises(gg.OrganTableError, match="SHEATH_PLATEAU_M"):
        bake(broken, p, "0" * 64)

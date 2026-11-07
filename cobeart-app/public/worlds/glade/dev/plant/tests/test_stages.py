"""Stage meshes without Blender (GP Phase 3, WP 3.3): every stage goes through declined_vertex."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

import grass_geometry as gg
from decline import DeclineParams
from stages import DECLINE_TIME, HIDE_OPACITY, StageSpec, build_stage, stage_specs


@pytest.fixture(scope="module")
def table(organ_table_path: Path) -> gg.OrganTable:
    return gg.load_table(organ_table_path)


def test_stage_names_are_unique_and_ordered() -> None:
    names = [spec.name for spec in stage_specs()]
    assert len(set(names)) == len(names)
    assert names[0] == "Stage_t1" and names[-1] == "Stage_t12_v0.2"


def test_growth_stage_moves_only_senescent_leaves(table: gg.OrganTable) -> None:
    p = gg.GeometryParams()
    stage = build_stage(table, StageSpec(DECLINE_TIME, 1.0), p, DeclineParams.from_mapping(table.params))
    mesh = gg.build_plant(table, DECLINE_TIME, p)
    sample = gg.sample_at(table, DECLINE_TIME)
    kept = moved = 0
    index = 0
    for rng in mesh.leaves:
        senescent = sample.leaves[rng.id].senescence > 0.0
        for i in (*range(*rng.sheath), *range(*rng.blade)):
            if mesh.scalars["fade"][i] >= 1.0 - HIDE_OPACITY:
                continue  # dropped from the stage
            if math.dist(stage.positions[index], mesh.positions[i]) > 1e-9:
                assert senescent, f"{rng.id} moved at v = 1 without senescence"
                moved += 1
            kept += 1
            index += 1
    assert index == len(stage.positions) and kept > 0 and moved > 0

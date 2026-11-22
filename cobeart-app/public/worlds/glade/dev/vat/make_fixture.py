"""Builds the tiny synthetic VAT plant and writes fixtures/tiny.vat.glb (deterministic, no randomness).

Two organs of two quads each (3 rows x 2 columns of vertices), three stages, every storage type, both domains and
both timings, a `lifespan` block (organ 0 is dead at the preview stage), plus a `uv` block so the preview mesh
carries TEXCOORD_0.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vat_writer import NEVER, Block, Frame, VatPlant, write_vat  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "tiny.vat.glb"
ROWS, COLS, ORGANS = 3, 2, 2
STAGE_TIMES = (0.0, 1.5, 3.0)
VERTS_PER_ORGAN = ROWS * COLS


def _grid_triangles(base: int) -> list[int]:
    """Two triangles per quad of one ROWS x COLS vertex grid whose first vertex has index `base`."""
    out: list[int] = []
    for r in range(ROWS - 1):
        for c in range(COLS - 1):
            a, b = base + r * COLS + c, base + r * COLS + c + 1
            d, e = base + (r + 1) * COLS + c, base + (r + 1) * COLS + c + 1
            out += [a, b, e, a, e, d]
    return out


def build_tiny_plant() -> VatPlant:
    """The fixture plant."""
    vertices = [(o, r, c) for o in range(ORGANS) for r in range(ROWS) for c in range(COLS)]
    stages = range(len(STAGE_TIMES))
    position = [v for k in stages for o, r, c in vertices
                for v in (0.125 * o + 0.0625 * c * (k + 1), 0.0078125 * r * (k + 1), 0.25 * r * (k + 1) + 0.01 * c)]
    normal = [v for k in stages for o, r, c in vertices
              for v in (0.3 * k * (c - 0.5), 0.25 * o - 0.1 * r, math.copysign(1.0 + r, 1.0 if k < 2 else -1.0))]
    metas = {"events": {"components": ["senescent_time", "senescence_duration", "death_time", "collapse_time"],
                        "unit": "P"},
             "lifespan": {"components": ["appear", "vanish"], "unit": "P"}}
    organ_static = [("birth", "f32", 1, [0.0, 1.5]),
                    ("events", "f32", 4, [1.0, 0.5, 2.0, 2.5, 2.0, 1.0, NEVER, NEVER]),
                    ("lifespan", "f32", 2, [0.0, 2.5, 1.5, NEVER]),
                    ("kind", "u32", 1, [0, 1]),
                    ("tilt", "f16", 1, [0.5, -1.25])]
    plant_blocks = [
        Block("position", "vertex", "stage", 3, "f16", position, {"components": ["x", "y", "z"], "unit": "m"}),
        Block("normal", "vertex", "stage", 3, "oct16", normal),
        Block("organ", "vertex", "static", 1, "u32", [o for o, _, _ in vertices]),
        Block("uv", "vertex", "static", 2, "f32", [v for _, r, c in vertices for v in (float(c), r / (ROWS - 1))]),
        Block("s", "vertex", "static", 1, "f16", [r / (ROWS - 1) for _, r, _ in vertices]),
        *(Block(n, "organ", "static", comps, storage, vals, metas.get(n)) for n, storage, comps, vals in organ_static),
        Block("arch", "organ", "stage", 1, "f32", [0.25 * (o + 1) * k for k in stages for o in range(ORGANS)]),
        Block("collar", "organ", "stage", 3, "f16",
              [v for k in stages for o in range(ORGANS) for v in (0.5 * o, 0.25 * k, 0.125 * (k + o))]),
    ]
    triangles = [i for o in range(ORGANS) for i in _grid_triangles(o * VERTS_PER_ORGAN)]
    return VatPlant(species="tiny", species_params={"seed": 1, "note": "synthetic loader fixture"},
                    organ_ids=("1", "2"), stage_times=STAGE_TIMES, triangles=triangles, preview_stage=2,
                    blocks=plant_blocks, frame=Frame("+Z", "m"), species_version=1)


def main() -> None:
    """Writes the fixture file."""
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_vat(build_tiny_plant(), FIXTURE_PATH)
    print(f"wrote {FIXTURE_PATH} ({FIXTURE_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

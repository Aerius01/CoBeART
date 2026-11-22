"""Python reference of the per-vertex decline rule over a grass VAT bake (GP roadmap, G55). Stdlib only.

Usage: ~/miniconda3/envs/lpy/bin/python dev/plant/decline_reference.py BAKE.vat.glb OUT.json [--case T,V ...]

Rebuilds decline.VertexInputs for every vertex from the decoded blocks (grass_vat_profile.md, rebuild rules) at exact
stage times T and vitalities V, runs decline.declined_vertex, and writes {vat, vat_sha256, vertex_count, cases: [{t, v,
positions, pivots, axes, browning, opacity}]} with flat lists (3 per vertex for the vectors). vat is the bake's file
name and vat_sha256 the hex sha256 of its bytes (G76). grass-check.html compares the GLSL port against it and fails
when vat_sha256 is not the sha256 of the bake it loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PLANT_DIR = Path(__file__).resolve().parent
for _dir in (_PLANT_DIR.parent / "vat", _PLANT_DIR.parent / "lpy"):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import decline  # noqa: E402
from vat_writer import NEVER, VatPlant, decode_vat  # noqa: E402

SPECIES = "grass"
SPECIES_VERSION = 1
STAGE_TOLERANCE = 1e-9
SIGNIFICANT_DIGITS = 7
DEFAULT_CASES: tuple[tuple[float, float], ...] = (
    (12.0, 1.0), (12.0, 0.6), (12.0, 0.3), (12.0, 0.1), (6.0, 1.0), (6.0, 0.5), (3.0, 0.8), (9.0, 0.2))

Case = tuple[float, float]
Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class PlantView:
    """Block values of a decoded plant by name, plus the counts they are indexed with."""
    plant: VatPlant
    blocks: dict[str, Sequence[float]]
    vertex_count: int
    organ_count: int

    def vec(self, name: str, index: int) -> Vec3:
        v = self.blocks[name]
        return (v[3 * index], v[3 * index + 1], v[3 * index + 2])


def view(plant: VatPlant) -> PlantView:
    """Checks the species and indexes the blocks; raises ValueError for a bake that is not grass version 1."""
    if plant.species != SPECIES or plant.species_version != SPECIES_VERSION:
        raise ValueError(f"bake is species {plant.species!r} version {plant.species_version}; this reference "
                         f"supports {SPECIES!r} version {SPECIES_VERSION}")
    blocks = {b.name: b.values for b in plant.blocks}
    return PlantView(plant, blocks, len(blocks["organ"]), len(plant.organ_ids))


def stage_index(stage_times: Sequence[float], t: float) -> int:
    """Index of the stage whose time is t (within 1e-9); raises ValueError naming the nearest stage times."""
    for k, st in enumerate(stage_times):
        if abs(st - t) <= STAGE_TOLERANCE:
            return k
    nearest = sorted(sorted(stage_times, key=lambda st: abs(st - t))[:3])
    raise ValueError(f"T={t} is not a stage time; use one of the bake's stage times, nearest: "
                     f"{', '.join(str(n) for n in nearest)}")


def _ramp(t: float, start: float, duration: float) -> float:
    """clamp((t - start) / duration, 0, 1); a NEVER start gives 0 (the organ table guarantees duration > 0)."""
    if start >= NEVER:
        return 0.0
    return min(max((t - start) / duration, 0.0), 1.0)


def vertex_inputs_at(plant: VatPlant | PlantView, stage: int, t: float, vertex: int) -> decline.VertexInputs:
    """The decline inputs of one vertex at stage `stage` (model time t), from the profile's rebuild rules."""
    pv = plant if isinstance(plant, PlantView) else view(plant)
    fade_duration = float(pv.plant.species_params["params"]["FADE_DURATION"])
    b, n = pv.blocks, pv.organ_count
    o = b["organ"][vertex]
    kind = int(b["kind"][vertex])
    collar = pv.vec("collar", stage * n + o)
    direction = pv.vec("direction", stage * n + o)
    tiller_axis = pv.vec("tiller_axis", o)
    sen_time, sen_duration, death_time = b["senescence"][3 * o:3 * o + 3]
    return decline.VertexInputs(
        kind=kind, s=b["s"][vertex], collar=collar, side=pv.vec("side", o),
        axis=direction if kind == decline.KIND_BLADE else tiller_axis,
        tiller_base=pv.vec("tiller_base", o), tiller_axis=tiller_axis, tiller_azimuth=b["tiller_az"][o],
        tiller=int(b["tiller"][o]), birth_time=b["lifespan"][2 * o], decline_draw=b["draw"][o],
        senescence=_ramp(t, sen_time, sen_duration), fade=_ramp(t, death_time, fade_duration),
        arch_rad=b["arch"][stage * n + o])


def _round(values: Sequence[float]) -> list[float]:
    return [float(f"{x:.{SIGNIFICANT_DIGITS}g}") for x in values]


def reference(plant: VatPlant, cases: Sequence[Case], vat_name: str, vat_sha256: str) -> dict[str, Any]:
    """Declined position, pivot, axis, browning and opacity of every vertex for each (T, v) case, bound to the bake
    by its file name and the hex sha256 of its bytes."""
    pv = view(plant)
    params = decline.DeclineParams.from_mapping(plant.species_params["params"])
    collar_bend = float(plant.species_params["geometry"]["collar_bend"])
    position = pv.blocks["position"]
    count = pv.vertex_count
    out_cases = []
    for t, v in cases:
        stage = stage_index(plant.stage_times, t)
        positions: list[float] = []
        pivots: list[float] = []
        axes: list[float] = []
        browning: list[float] = []
        opacity: list[float] = []
        for i in range(count):
            base = (stage * count + i) * 3
            vin = vertex_inputs_at(pv, stage, t, i)
            d = decline.declined_vertex(position[base:base + 3], vin, t, v, params, collar_bend)
            positions.extend(d.position)
            pivots.extend(d.pivot)
            axes.extend(d.axis)
            browning.append(d.browning)
            opacity.append(d.opacity)
        out_cases.append({"t": t, "v": v, "positions": _round(positions), "pivots": _round(pivots),
                          "axes": _round(axes), "browning": _round(browning), "opacity": _round(opacity)})
    return {"vat": vat_name, "vat_sha256": vat_sha256, "vertex_count": count, "cases": out_cases}


def parse_case(text: str) -> Case:
    try:
        t, v = (float(x) for x in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--case expects T,V as two numbers (for example 12,0.6), got {text!r}")
    return t, v


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vat", type=Path, help="grass VAT bake (.vat.glb)")
    parser.add_argument("out", type=Path, help="output reference JSON")
    parser.add_argument("--case", action="append", type=parse_case, metavar="T,V",
                        help="stage time and vitality; repeatable (default: the eight canonical cases)")
    args = parser.parse_args(argv)
    cases = tuple(args.case) if args.case else DEFAULT_CASES
    start = time.perf_counter()
    data = args.vat.read_bytes()
    result = reference(decode_vat(data), cases, args.vat.name, hashlib.sha256(data).hexdigest())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    size = args.out.stat().st_size
    print(f"wrote {args.out}: {len(cases)} cases, {result['vertex_count']} vertices, {size} bytes "
          f"({size / 1e6:.2f} MB) in {time.perf_counter() - start:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

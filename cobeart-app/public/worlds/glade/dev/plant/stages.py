"""Decline-stage meshes of the grass, without Blender: each vertex of the healthy mesh goes through
decline.declined_vertex (the shader's path), growth stages included (turnover senescence shrinks and droops a leaf
even at vitality 1)."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_PLANT_DIR = Path(__file__).resolve().parent
for _dir in (_PLANT_DIR, _PLANT_DIR.parent / "lpy"):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import grass_geometry as gg  # noqa: E402
from decline import DeclineParams, declined_vertex  # noqa: E402

STAGE_TIMES: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
DECLINE_TIME = 12.0
DECLINE_VITALITIES: tuple[float, ...] = (0.8, 0.6, 0.4, 0.2)
HIDE_OPACITY = 0.1        # leaves drawn below this opacity are left out of a stage
# Copied from grass.lpy's preview materials (0..255); display only, not part of the contract.
GREEN = (40, 120, 20)
SHEATH_GREEN = (90, 120, 40)
BROWN = (140, 100, 45)

Vec3 = gg.Vec3
Rgba = tuple[float, float, float, float]


@dataclass(frozen=True)
class StageSpec:
    """One stage object: a sample time and a plant vitality (1 is the healthy growth stage)."""
    time: float
    vitality: float

    @property
    def name(self) -> str:
        return f"Stage_t{self.time:g}" + ("" if self.vitality == 1.0 else f"_v{self.vitality:g}")


@dataclass(frozen=True)
class StageMesh:
    """Display mesh of one stage: positions, quads, per-loop UVs, materials and a colour per vertex."""
    positions: tuple[Vec3, ...]
    faces: tuple[gg.Quad, ...]
    face_uvs: tuple[tuple[gg.UV, gg.UV, gg.UV, gg.UV], ...]
    face_materials: tuple[int, ...]
    colors: tuple[Rgba, ...]  # linear RGBA


def stage_specs() -> list[StageSpec]:
    """Growth stages (v = 1), then decline stages at DECLINE_TIME."""
    return ([StageSpec(t, 1.0) for t in STAGE_TIMES] + [StageSpec(DECLINE_TIME, v) for v in DECLINE_VITALITIES])


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def browning_color(browning: float, sheath: bool) -> Rgba:
    """Linear RGBA at a browning level: lerp from the green (blade or sheath) to the brown, 0..255 as in grass.lpy."""
    green = SHEATH_GREEN if sheath else GREEN
    rgb = [srgb_to_linear((g + (b - g) * browning) / 255.0) for g, b in zip(green, BROWN)]
    return (rgb[0], rgb[1], rgb[2], 1.0)


def build_stage(table: gg.OrganTable, spec: StageSpec, p: gg.GeometryParams, decline: DeclineParams) -> StageMesh:
    """The plant at one stage: every vertex runs through declined_vertex; leaves below HIDE_OPACITY are dropped."""
    mesh = gg.build_plant(table, spec.time, p)  # leaves present at the time: no degenerate ones to carry
    keep: dict[int, int] = {}
    positions: list[Vec3] = []
    colors: list[Rgba] = []
    for rng in mesh.leaves:
        for i in (*range(*rng.sheath), *range(*rng.blade)):
            dv = declined_vertex(mesh.positions[i], gg.vertex_inputs(mesh, i), spec.time, spec.vitality, decline)
            if dv.opacity < HIDE_OPACITY:
                continue
            keep[i] = len(positions)
            positions.append(dv.position)
            colors.append(browning_color(dv.browning, rng.sheath[0] <= i < rng.sheath[1]))
    faces, uvs, mats = [], [], []
    for face, uv, mat in zip(mesh.faces, mesh.face_uvs, mesh.face_materials):
        if all(i in keep for i in face):
            faces.append((keep[face[0]], keep[face[1]], keep[face[2]], keep[face[3]]))
            uvs.append(uv)
            mats.append(mat)
    return StageMesh(tuple(positions), tuple(faces), tuple(uvs), tuple(mats), tuple(colors))

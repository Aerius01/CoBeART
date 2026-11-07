"""Build Glade's grass mesh from an organ table in Blender: a GLB for the shader and a scene of decline stages.

Headless:
  blender --background --python build_grass_from_table.py -- ORGANS.json --glb OUT.glb [--time T]
  blender --background --python build_grass_from_table.py -- ORGANS.json --stages OUT.blend
  (ORGANS.json comes from ../lpy/export_organs.py; --glb and --stages can be given together)

--glb      the healthy plant at T (default the table's t_end) with the lifetime leaf list (fixed topology, G28) and
           every G29 attribute as a point attribute named by grass_geometry.gltf_name (e.g. _TILLER_AZ). Z up, metres.
--stages   a row of objects along +x: growth at STAGE_TIMES, then decline at DECLINE_TIME for DECLINE_VITALITIES, each
           vertex placed by decline.declined_vertex (the shader's path). A display-only colour attribute (green to
           brown) is baked; leaves whose opacity is below HIDE_OPACITY are dropped from that stage.
The Blender-only parts are at the bottom; the stage maths above them uses no bpy.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_PLANT_DIR = Path(__file__).resolve().parent
for _dir in (_PLANT_DIR, _PLANT_DIR.parent / "lpy"):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import grass_geometry as gg  # noqa: E402
from decline import DeclineParams, VertexInputs, declined_vertex  # noqa: E402

STAGE_TIMES: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
DECLINE_TIME = 12.0
DECLINE_VITALITIES: tuple[float, ...] = (0.8, 0.6, 0.4, 0.2)
STAGE_SPACING_M = 0.45
HIDE_OPACITY = 0.1        # leaves drawn below this opacity are left out of a stage
GREEN = (40, 120, 20)     # grass.lpy's blade colours, 0..255
SHEATH_GREEN = (90, 120, 40)
BROWN = (140, 100, 45)
COLLECTION_GLB = "GrassFromTable"
COLLECTION_STAGES = "GrassStages"
MESH_NAME = "GrassFromTable_Mesh"
COLOR_ATTRIBUTE = "Browning"
LABEL_SIZE_M = 0.035
CAMERA_HEIGHT_M = 0.35
CAMERA_DISTANCE_M = 1.6
CAMERA_TILT_DEG = 80.0    # 90 looks level along +y
MATERIALS: tuple[tuple[str, tuple[float, float, float], float], ...] = (
    ("Grass_Sheath", (0.30, 0.40, 0.12), 0.5),
    ("Grass_Blade", (0.10, 0.32, 0.05), 0.45),
)

Vec3 = tuple[float, float, float]


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
    colors: tuple[tuple[float, float, float, float], ...]  # linear RGBA


def stage_specs() -> list[StageSpec]:
    """Growth stages (healthy), then decline stages at DECLINE_TIME."""
    return ([StageSpec(t, 1.0) for t in STAGE_TIMES] + [StageSpec(DECLINE_TIME, v) for v in DECLINE_VITALITIES])


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def browning_color(browning: float, sheath: bool) -> tuple[float, float, float, float]:
    """Linear RGBA at a browning level: lerp from the green (blade or sheath) to the brown, 0..255 as in grass.lpy."""
    green = SHEATH_GREEN if sheath else GREEN
    rgb = [srgb_to_linear((g + (b - g) * browning) / 255.0) for g, b in zip(green, BROWN)]
    return (rgb[0], rgb[1], rgb[2], 1.0)


def vertex_inputs(mesh: gg.PlantMesh, i: int) -> VertexInputs:
    """The decline inputs of mesh vertex i, read from its G29 attributes (kind and tiller are floats in the mesh)."""
    sc, vc = mesh.scalars, mesh.vectors
    return VertexInputs(kind=int(sc["kind"][i]), s=sc["s"][i], collar=vc["collar"][i], side=vc["side"][i],
                        tiller_base=vc["tiller_base"][i], tiller_axis=vc["tiller_axis"][i],
                        tiller_azimuth=sc["tiller_az"][i], tiller=int(sc["tiller"][i]), birth_time=sc["birth"][i],
                        decline_draw=sc["draw"][i], senescence=sc["sen"][i], fade=sc["fade"][i], arch_rad=sc["arch"][i])


def build_stage(table: gg.OrganTable, spec: StageSpec, p: gg.GeometryParams,
                decline: DeclineParams) -> StageMesh:
    """The plant at one stage. A healthy stage keeps the healthy positions and browns by senescence; a decline stage
    runs every vertex through declined_vertex. Leaves with opacity below HIDE_OPACITY are dropped."""
    mesh = gg.build_plant(table, spec.time, p)  # leaves present at the time: no degenerate ones to carry
    keep: dict[int, int] = {}
    positions: list[Vec3] = []
    colors: list[tuple[float, float, float, float]] = []
    for rng in mesh.leaves:
        for i in (*range(*rng.sheath), *range(*rng.blade)):
            sheath = rng.sheath[0] <= i < rng.sheath[1]
            if spec.vitality == 1.0:
                position, browning = mesh.positions[i], mesh.scalars["sen"][i]
                opacity = 1.0 - mesh.scalars["fade"][i]
            else:
                dv = declined_vertex(mesh.positions[i], vertex_inputs(mesh, i), spec.time, spec.vitality, decline)
                position, browning, opacity = dv.position, dv.browning, dv.opacity
            if opacity < HIDE_OPACITY:
                continue
            keep[i] = len(positions)
            positions.append(position)
            colors.append(browning_color(browning, sheath))
    faces, uvs, mats = [], [], []
    for face, uv, mat in zip(mesh.faces, mesh.face_uvs, mesh.face_materials):
        if all(i in keep for i in face):
            faces.append((keep[face[0]], keep[face[1]], keep[face[2]], keep[face[3]]))
            uvs.append(uv)
            mats.append(mat)
    return StageMesh(tuple(positions), tuple(faces), tuple(uvs), tuple(mats), tuple(colors))


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="build_grass_from_table.py", description=__doc__.split("\n")[0])
    parser.add_argument("organs", type=Path, help="organ table JSON from lpy/export_organs.py")
    parser.add_argument("--glb", type=Path, help="write the healthy mesh with its attributes as a GLB")
    parser.add_argument("--time", type=float, help="sample time of the GLB mesh (default the table's t_end)")
    parser.add_argument("--stages", type=Path, help="save a .blend of growth and decline stages")
    args = parser.parse_args(list(argv))
    if args.glb is None and args.stages is None:
        parser.error("give at least one of --glb OUT.glb and --stages OUT.blend")
    return args


def script_args() -> list[str]:
    """The arguments after `--` on Blender's command line."""
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


# Blender-only code.

import bpy  # noqa: E402


def ensure_material(name: str, color: tuple[float, float, float], roughness: float,
                    vertex_color: str | None = None) -> bpy.types.Material:
    """A Principled material; with vertex_color its base colour is that colour attribute."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        if vertex_color is not None:
            node = mat.node_tree.nodes.new("ShaderNodeVertexColor")
            node.layer_name = vertex_color
            mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def to_blender_mesh(name: str, positions: Sequence[Vec3], faces: Sequence[gg.Quad], face_uvs: Sequence[object],
                    face_materials: Sequence[int], vertex_color: str | None = None) -> bpy.types.Mesh:
    """Mesh with the two materials, smooth shading and UVs; validation failures raise."""
    me = bpy.data.meshes.new(name)
    me.from_pydata(list(positions), [], list(faces))
    for mat_name, color, rough in MATERIALS:
        me.materials.append(ensure_material(mat_name + ("_Stage" if vertex_color else ""), color, rough, vertex_color))
    me.polygons.foreach_set("material_index", list(face_materials))
    me.polygons.foreach_set("use_smooth", [True] * len(faces))
    me.uv_layers.new(name="UVMap").data.foreach_set("uv", [c for f in face_uvs for uv in f for c in uv])  # type: ignore
    return me


def finish_mesh(me: bpy.types.Mesh) -> bpy.types.Mesh:
    if me.validate():
        raise RuntimeError(f"{me.name}: Blender had to correct invalid geometry; check the generator")
    me.update()
    return me


def plant_blender_mesh(plant: gg.PlantMesh) -> bpy.types.Mesh:
    """The healthy plant with every G29 attribute as a point attribute named by gltf_name."""
    me = to_blender_mesh(MESH_NAME, plant.positions, plant.faces, plant.face_uvs, plant.face_materials)
    for name in gg.VECTOR_ATTRIBUTES:
        me.attributes.new(gg.gltf_name(name), 'FLOAT_VECTOR', 'POINT').data.foreach_set(
            "vector", [c for v in plant.vectors[name] for c in v])
    for name in gg.SCALAR_ATTRIBUTES:
        me.attributes.new(gg.gltf_name(name), 'FLOAT', 'POINT').data.foreach_set("value", plant.scalars[name])
    return finish_mesh(me)


def stage_blender_mesh(name: str, stage: StageMesh) -> bpy.types.Mesh:
    me = to_blender_mesh(name, stage.positions, stage.faces, stage.face_uvs, stage.face_materials, COLOR_ATTRIBUTE)
    me.color_attributes.new(COLOR_ATTRIBUTE, 'FLOAT_COLOR', 'POINT').data.foreach_set(
        "color", [c for rgba in stage.colors for c in rgba])
    return finish_mesh(me)


def link_collection(name: str) -> bpy.types.Collection:
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    return coll


def export_glb(ob: bpy.types.Object, path: Path) -> None:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=True, export_yup=False,
                              export_attributes=True, export_normals=True, export_texcoords=True)


def write_glb(table: gg.OrganTable, time: float, path: Path) -> None:
    plant = gg.build_plant(table, time, gg.GeometryParams(), gg.lifetime_leaves(table))
    coll = link_collection(COLLECTION_GLB)
    ob = bpy.data.objects.new(MESH_NAME, plant_blender_mesh(plant))
    coll.objects.link(ob)
    path.parent.mkdir(parents=True, exist_ok=True)
    export_glb(ob, path)
    print(f"Exported {ob.name}: {len(ob.data.vertices)} vertices (mesh {len(plant.positions)}) to {path}")
    me = ob.data
    bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(me)
    bpy.data.collections.remove(coll)


def add_label(text: str, location: Vec3, coll: bpy.types.Collection) -> None:
    curve = bpy.data.curves.new(f"Label_{text}", 'FONT')
    curve.body = text
    curve.size = LABEL_SIZE_M
    curve.align_x = 'CENTER'
    ob = bpy.data.objects.new(f"Label_{text}", curve)
    ob.location = location
    ob.rotation_euler = (math.radians(90.0), 0.0, 0.0)  # stands up, readable from -y
    coll.objects.link(ob)


def add_camera_and_light(width: float, coll: bpy.types.Collection) -> None:
    centre_x = (width - STAGE_SPACING_M) / 2.0
    cam_data = bpy.data.cameras.new("StagesCamera")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = width + STAGE_SPACING_M
    cam = bpy.data.objects.new("StagesCamera", cam_data)
    cam.location = (centre_x, -CAMERA_DISTANCE_M, CAMERA_HEIGHT_M)
    cam.rotation_euler = (math.radians(CAMERA_TILT_DEG), 0.0, 0.0)
    coll.objects.link(cam)
    bpy.context.scene.camera = cam
    sun = bpy.data.objects.new("StagesSun", bpy.data.lights.new("StagesSun", 'SUN'))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50.0), math.radians(10.0), math.radians(-25.0))
    coll.objects.link(sun)


def write_stages(table: gg.OrganTable, path: Path) -> None:
    p, decline = gg.GeometryParams(), DeclineParams.from_mapping(table.params)
    coll = link_collection(COLLECTION_STAGES)
    specs = stage_specs()
    for n, spec in enumerate(specs):
        stage = build_stage(table, spec, p, decline)  # raises OrganTableError naming the nearest sample time
        ob = bpy.data.objects.new(spec.name, stage_blender_mesh(spec.name, stage))
        ob.location = (n * STAGE_SPACING_M, 0.0, 0.0)
        coll.objects.link(ob)
        add_label(spec.name.removeprefix("Stage_"), (n * STAGE_SPACING_M, 0.0, -LABEL_SIZE_M * 1.5), coll)
        print(f"{spec.name}: {len(ob.data.vertices)} vertices")
    add_camera_and_light(len(specs) * STAGE_SPACING_M, coll)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    print(f"Saved {len(specs)} stages to {path}")


def main() -> None:
    args = parse_args(script_args())
    table = gg.load_table(args.organs)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if args.glb is not None:
        write_glb(table, table.t_end if args.time is None else args.time, args.glb)
    if args.stages is not None:
        write_stages(table, args.stages)


if __name__ == "__main__":
    main()

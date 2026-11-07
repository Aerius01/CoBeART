"""Build Glade's grass mesh from an organ table in Blender: a GLB for the shader and a scene of decline stages.

Headless:
  blender --background --python build_grass_from_table.py -- ORGANS.json --glb OUT.glb [--time T]
  blender --background --python build_grass_from_table.py -- ORGANS.json --stages OUT.blend
  (ORGANS.json comes from ../lpy/export_organs.py; --glb and --stages can be given together)

--glb      the healthy plant at T (default the table's t_end) with the lifetime leaf list (fixed topology, G28) and
           every G29 attribute as a point attribute named by grass_geometry.gltf_name (e.g. _TILLER_AZ). Z up, metres.
--stages   a row of objects along +x: growth at stages.STAGE_TIMES, then decline at stages.DECLINE_TIME for
           stages.DECLINE_VITALITIES, each vertex placed by decline.declined_vertex (the shader's path, via
           stages.py). A display-only colour attribute (green to brown) is baked; leaves whose opacity is below
           stages.HIDE_OPACITY are dropped from that stage.
The stage maths is in stages.py (no bpy); this file is argparse plus Blender, and exits 1 on any error.
"""
from __future__ import annotations

import argparse
import math
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

_PLANT_DIR = Path(__file__).resolve().parent
for _dir in (_PLANT_DIR, _PLANT_DIR.parent / "lpy"):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import grass_geometry as gg  # noqa: E402
from decline import DeclineParams  # noqa: E402
from stages import StageMesh, build_stage, stage_specs  # noqa: E402

STAGE_SPACING_M = 0.45
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

Vec3 = gg.Vec3


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


def run() -> None:
    args = parse_args(script_args())
    table = gg.load_table(args.organs)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if args.glb is not None:
        write_glb(table, table.t_end if args.time is None else args.time, args.glb)
    if args.stages is not None:
        write_stages(table, args.stages)


def main() -> None:
    """run(), but any exception prints its traceback and exits 1 (Blender's --python would exit 0)."""
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

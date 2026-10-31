"""Build Glade's reference grass plant in Blender and export it as a Z-up GLB with per-vertex wind attributes.

Headless:  blender --background --python build_grass_plant.py [-- OUTPUT.glb]
In Blender: run from the Text Editor; it replaces GrassPlant_Mesh in the open scene and exports next to this script.

Units are metres, Z up (the exporter does not convert custom attributes, so the file keeps Blender's axes).
Per-vertex attributes (what the wind shader reads):
  _PIVOT   vec3  stem/sheath: point on the stem axis at the vertex's height; leaf: the leaf's attachment point
  _AXIS    vec3  stem tangent at that height, or the leaf's initial direction
  _S       float 0..1 along the organ, base to tip
  _STEM_T  float 0..1 attachment height on the stem (own height for the stem, node height for a leaf)
  _ORGAN   float 0 stem and sheaths, 1..n node leaves, n+1 the top leaf
  _PHASE   float per-leaf random phase in 0..1 (0 on the stem)
"""
from __future__ import annotations

import math
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

Profile = Callable[[float], float]


@dataclass(frozen=True)
class LeafSpec:
    angle_from_vertical_deg: float
    length_m: float
    bend_rad: float


@dataclass(frozen=True)
class PlantParams:
    stem_height_m: float = 0.17
    stem_radius_base_m: float = 0.0022
    stem_radius_top_m: float = 0.0013
    width_scale: float = 1.3
    blade_cross_segments: int = 6
    tube_segments: int = 10
    base_azimuth_rad: float = 0.4
    leaf_seed: int = 5
    node_seed: int = 42
    phase_seed: int = 99
    leaves: tuple[LeafSpec, ...] = (
        LeafSpec(48, 0.17, 0.9),
        LeafSpec(40, 0.20, 0.8),
        LeafSpec(34, 0.21, 0.7),
        LeafSpec(28, 0.19, 0.55),
        LeafSpec(20, 0.16, 0.4),
    )
    top_leaf_length_m: float = 0.17
    top_leaf_width_m: float = 0.008
    top_leaf_azimuth_offset_rad: float = 0.8


MAT_STEM, MAT_LEAF = 0, 1
MATERIALS: tuple[tuple[str, tuple[float, float, float], float], ...] = (
    ("Grass_Stalk", (0.30, 0.40, 0.12), 0.5),
    ("Grass_Leaf", (0.10, 0.32, 0.05), 0.45),
)
COLLECTION = "GrassPlant"
ROOT = "GrassPlant"
MESH = "GrassPlant_Mesh"


@dataclass
class MeshData:
    """Accumulated geometry: positions, wind attributes, quads with per-loop UVs and material indices."""
    verts: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[tuple[int, int, int, int]] = field(default_factory=list)
    face_uvs: list[list[tuple[float, float]]] = field(default_factory=list)
    face_mats: list[int] = field(default_factory=list)
    pivot: list[tuple[float, float, float]] = field(default_factory=list)
    axis: list[tuple[float, float, float]] = field(default_factory=list)
    s: list[float] = field(default_factory=list)
    stem_t: list[float] = field(default_factory=list)
    organ: list[float] = field(default_factory=list)
    phase: list[float] = field(default_factory=list)

    def add_vert(self, p: Vector, pivot: Vector, axis: Vector, s: float, stem_t: float, organ: int,
                 phase: float) -> int:
        self.verts.append(tuple(p))
        self.pivot.append(tuple(pivot))
        self.axis.append(tuple(axis))
        self.s.append(s)
        self.stem_t.append(stem_t)
        self.organ.append(float(organ))
        self.phase.append(phase)
        return len(self.verts) - 1

    def add_quad(self, quad: tuple[tuple[int, float, float], ...], mat: int) -> None:
        self.faces.append(tuple(q[0] for q in quad))
        self.face_uvs.append([(q[1], q[2]) for q in quad])
        self.face_mats.append(mat)


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def dir_from(az: float, from_vertical: float) -> Vector:
    return Vector((math.cos(az) * math.sin(from_vertical),
                   math.sin(az) * math.sin(from_vertical),
                   math.cos(from_vertical)))


def curl_angle(s: float) -> float:
    """Cross-section arc angle: rolled at the base as if it had wrapped the stem, near flat at the tip."""
    return 0.35 + 2.6 * (1 - s) ** 1.5


def leaf_width(w: float, s: float) -> float:
    """Narrow base, widest at about a third, long taper to the tip."""
    return max(w * min(1.0, 0.5 + s * 1.6) * (1 - s) ** 0.6, 0.0004)


def sheath_offset(k: int) -> float:
    """Sheath radius above the stem for the sheath ending at node k; lower sheaths sit wider."""
    return 0.0008 - 0.00008 * k


def irregular_nodes(n: int, rng: random.Random, first: tuple[float, float] = (0.03, 0.10),
                    gap_weight: tuple[float, float] = (0.35, 1.9),
                    top_gap: tuple[float, float] = (0.06, 0.16)) -> list[float]:
    """Node positions (0..1 along the stem) with uneven internodes."""
    weights = [rng.uniform(*gap_weight) for _ in range(n - 1)]
    t0 = rng.uniform(*first)
    span = 1.0 - rng.uniform(*top_gap) - t0
    ts = [t0]
    for w in weights:
        ts.append(ts[-1] + span * w / sum(weights))
    return ts


class Stem:
    def __init__(self, params: PlantParams) -> None:
        self.p = params

    def point(self, t: float) -> Vector:
        return Vector((0.012 * t * t, 0.006 * t, self.p.stem_height_m * t))

    def radius(self, t: float) -> float:
        return lerp(self.p.stem_radius_base_m, self.p.stem_radius_top_m, t)

    def tangent(self, t: float) -> Vector:
        return (self.point(min(t + 0.01, 1.0)) - self.point(max(t - 0.01, 0.0))).normalized()


def add_tube(mesh: MeshData, stem: Stem, t0: float, t1: float, radius: Profile, rings: int, mat: int,
             segments: int) -> None:
    """Stem or sheath (organ 0); each vertex pivots on the stem axis at its own height. Seam vertices are doubled for UVs."""
    rows = []
    for i in range(rings + 1):
        t = t0 + (t1 - t0) * i / rings
        tan = stem.tangent(t)
        ref = Vector((1, 0, 0))
        n = (ref - tan * ref.dot(tan)).normalized()
        b = tan.cross(n)
        r = radius(t)
        row = []
        for j in range(segments + 1):
            a = 2 * math.pi * j / segments
            v = mesh.add_vert(stem.point(t) + r * (math.cos(a) * n + math.sin(a) * b), stem.point(t), tan, t, t, 0, 0.0)
            row.append((v, j / segments, t))
        rows.append(row)
    for i in range(rings):
        a, c = rows[i], rows[i + 1]
        for j in range(segments):
            mesh.add_quad((a[j], a[j + 1], c[j + 1], c[j]), mat)


def add_blade(mesh: MeshData, organ: int, stem_t: float, phase: float, base: Vector, d0: Vector, side0: Vector,
              bend_dir: Vector, bend: Profile, length: float, width: Profile, curl: Profile, twist: float,
              cross: int, closed_base: bool = False, rows_n: int = 24) -> None:
    """Curled ribbon leaf bending from d0 toward bend_dir.

    With closed_base the arc's centre (not its midrib) starts on the path, so a fully rolled base continues the
    stem as a tube, then shifts to the midrib as the blade opens.
    """
    bend_axis = d0.cross(bend_dir)
    bend_axis = bend_axis.normalized() if bend_axis.length > 1e-6 else Vector((1, 0, 0))
    path = base.copy()
    rows = []
    for i in range(rows_n + 1):
        s = i / rows_n
        R = Matrix.Rotation(bend(s), 3, bend_axis)
        d = R @ d0
        side = Matrix.Rotation(twist * s, 3, d) @ (R @ side0)
        nrm = side.cross(d).normalized()  # curl opens away from the stem
        theta = curl(s)
        radius = width(s) / theta  # arc length across the blade equals its width
        pos = path - nrm * radius * (1 - smoothstep(s / 0.3)) if closed_base else path
        row = []
        for j in range(cross + 1):
            phi = (j / cross - 0.5) * theta
            p = pos + side * radius * math.sin(phi) + nrm * radius * (1 - math.cos(phi))
            row.append((mesh.add_vert(p, base, d0, s, stem_t, organ, phase), j / cross, s))
        rows.append(row)
        path = path + d * (length / rows_n)
    for i in range(rows_n):
        a, c = rows[i], rows[i + 1]
        for j in range(cross):
            mesh.add_quad((a[j], c[j], c[j + 1], a[j + 1]), MAT_LEAF)


def build_plant(p: PlantParams) -> MeshData:
    """Generate the plant geometry; the three seeds are separate streams so tuning one leaves the others intact."""
    leaf_rng = random.Random(p.leaf_seed)
    node_rng = random.Random(p.node_seed)
    phase_rng = random.Random(p.phase_seed)
    stem = Stem(p)
    mesh = MeshData()
    seg, cross = p.tube_segments, p.blade_cross_segments

    add_tube(mesh, stem, 0.0, 1.0, stem.radius, 16, MAT_STEM, seg)

    node_ts = irregular_nodes(len(p.leaves), node_rng)
    prev_t = 0.0
    for i, (t, spec) in enumerate(zip(node_ts, p.leaves)):
        az = p.base_azimuth_rad + i * math.pi + leaf_rng.uniform(-0.45, 0.45)  # alternating sides
        out = Vector((math.cos(az), math.sin(az), 0))
        base = stem.point(t) + out * (stem.radius(t) + sheath_offset(i))
        d0 = dir_from(az, math.radians(spec.angle_from_vertical_deg + leaf_rng.uniform(-5, 5)))
        b_az = az + leaf_rng.uniform(-0.6, 0.6)
        bend_dir = Vector((math.cos(b_az), math.sin(b_az), -0.4)).normalized()
        bend, power = spec.bend_rad * leaf_rng.uniform(0.85, 1.2), leaf_rng.uniform(1.6, 2.2)
        length = spec.length_m * leaf_rng.uniform(0.9, 1.1)
        w = leaf_rng.uniform(0.010, 0.012) * p.width_scale
        twist = leaf_rng.uniform(-0.5, 0.5)
        add_blade(mesh, i + 1, t, phase_rng.random(), base, d0, d0.cross(Vector((0, 0, 1))).normalized(), bend_dir,
                  lambda s, b=bend, k=power: b * s ** k, length, lambda s, w=w: leaf_width(w, s), curl_angle, twist,
                  cross)
        add_tube(mesh, stem, prev_t, t, lambda tt, k=i: stem.radius(tt) + sheath_offset(k), 5, MAT_LEAF, seg)
        prev_t = t

    # Top sheath, then the youngest leaf continuing it as a closed roll that opens into a blade
    top_off = sheath_offset(len(p.leaves))
    add_tube(mesh, stem, prev_t, 1.0, lambda tt: stem.radius(tt) + top_off, 3, MAT_LEAF, seg)
    r0 = stem.radius(1.0) + top_off
    closed = 2 * math.pi * 0.97
    w_top = p.top_leaf_width_m * p.width_scale
    top_az = p.base_azimuth_rad + p.top_leaf_azimuth_offset_rad
    d0 = stem.tangent(1.0)
    side0 = Vector((-math.sin(top_az), math.cos(top_az), 0))
    side0 = (side0 - d0 * side0.dot(d0)).normalized()
    add_blade(mesh, len(p.leaves) + 1, 1.0, phase_rng.random(), stem.point(1.0), d0, side0,
              Vector((math.cos(top_az), math.sin(top_az), -0.3)).normalized(),
              lambda s: 0.22 * smoothstep(s / 0.4) + 0.2 * s * s,  # leaves the stem line early, then a gentle arch
              p.top_leaf_length_m,
              lambda s: lerp(closed * r0, leaf_width(w_top, s), smoothstep(s / 0.3)),
              lambda s: lerp(closed, curl_angle(s), smoothstep(s / 0.3)),
              0.3, cross, closed_base=True, rows_n=30)
    return mesh


def ensure_material(name: str, color: tuple[float, float, float], roughness: float) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def to_blender_mesh(data: MeshData) -> bpy.types.Mesh:
    me = bpy.data.meshes.new(MESH)
    me.from_pydata(data.verts, [], data.faces)
    for name, color, rough in MATERIALS:
        me.materials.append(ensure_material(name, color, rough))
    me.polygons.foreach_set("material_index", data.face_mats)
    me.polygons.foreach_set("use_smooth", [True] * len(data.faces))
    me.uv_layers.new(name="UVMap").data.foreach_set("uv", [c for f in data.face_uvs for uv in f for c in uv])
    vectors = {"_PIVOT": data.pivot, "_AXIS": data.axis}
    scalars = {"_S": data.s, "_STEM_T": data.stem_t, "_ORGAN": data.organ, "_PHASE": data.phase}
    for name, values in vectors.items():
        me.attributes.new(name, 'FLOAT_VECTOR', 'POINT').data.foreach_set("vector", [c for v in values for c in v])
    for name, values in scalars.items():
        me.attributes.new(name, 'FLOAT', 'POINT').data.foreach_set("value", values)
    if me.validate():
        raise RuntimeError(f"{MESH}: Blender had to correct invalid geometry; check the generator")
    me.update()
    return me


def replace_plant_object(me: bpy.types.Mesh) -> bpy.types.Object:
    """Swap the mesh object in the GrassPlant collection, creating the collection and root empty if missing."""
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
        bpy.context.scene.collection.children.link(coll)
    root = bpy.data.objects.get(ROOT)
    if root is None:
        root = bpy.data.objects.new(ROOT, None)
        root.empty_display_size = 0.08
        coll.objects.link(root)
    old = bpy.data.objects.get(MESH)
    if old is not None:
        old_mesh = old.data
        bpy.data.objects.remove(old)
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    me.name = MESH
    ob = bpy.data.objects.new(MESH, me)
    coll.objects.link(ob)
    ob.parent = root
    return ob


def export_glb(ob: bpy.types.Object, path: Path) -> None:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=True, export_yup=False,
                              export_attributes=True, export_normals=True, export_texcoords=True)


def output_path() -> Path:
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return Path(args[0]) if args else Path(__file__).resolve().with_name("grass_plant.glb")


def main() -> None:
    ob = replace_plant_object(to_blender_mesh(build_plant(PlantParams())))
    path = output_path()
    export_glb(ob, path)
    print(f"Exported {ob.name}: {len(ob.data.vertices)} vertices to {path}")


if __name__ == "__main__":
    main()

"""Grass VAT bake (GP Phase 4, WP 4.3), checked on the decoded file against fresh build_plant meshes."""
from __future__ import annotations

import dataclasses
import json
import math
import shutil
import struct
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import grass_geometry as gg
from bake_grass_vat import BakeError, bake, main, vertex_uvs
from conftest import EXPORT_TIMEOUT_S, LPY_DIR, PLANT_DIR

sys.path.insert(0, str(PLANT_DIR.parent / "vat"))
from vat_writer import NEVER, Frame, VatPlant, decode_vat, encode_vat  # noqa: E402

VAT_DIR = PLANT_DIR.parent / "vat"
# float16 stores 11 significant bits (round to nearest: relative error <= 2^-11) and has a subnormal spacing of 2^-24.
F16_RELATIVE = 2.0 ** -11
F16_ABSOLUTE = 2.0 ** -24
TOLERANCE = 1e-6


@dataclasses.dataclass(frozen=True)
class Baked:
    table: gg.OrganTable
    meshes: tuple[gg.PlantMesh, ...]
    plant: VatPlant          # what bake returned
    decoded: VatPlant        # decode_vat(encode_vat(plant))
    data: bytes              # encode_vat(plant)
    collapsed: tuple[dict[str, bool], ...]
    unborn: tuple[dict[str, bool], ...]
    last_visible: tuple[dict[str, int], ...]  # per stage, for collapsed leaves: the last stage where it was visible

    def block(self, name: str) -> Sequence[float]:
        return next(b for b in self.decoded.blocks if b.name == name).values


def make_baked(table: gg.OrganTable) -> Baked:
    p = gg.GeometryParams()
    lifetime = gg.lifetime_leaves(table)
    meshes = tuple(gg.build_plant(table, t, p, lifetime) for t in table.sample_times)
    plant = bake(table, p)
    seen: set[str] = set()
    visible_at: dict[str, int] = {}
    collapsed, unborn, last_visible = [], [], []
    for k, sample in enumerate(table.samples):
        seen.update(sample.leaves)
        visible = {lid: lid in sample.leaves and sample.leaves[lid].fade < 1.0 for lid in lifetime}
        collapsed.append({lid: not visible[lid] and lid in seen for lid in lifetime})
        unborn.append({lid: lid not in seen for lid in lifetime})
        last_visible.append({lid: visible_at[lid] for lid in lifetime if collapsed[-1][lid]})
        visible_at.update({lid: k for lid in lifetime if visible[lid]})
    data = encode_vat(plant)
    return Baked(table, meshes, plant, decode_vat(data), data, tuple(collapsed), tuple(unborn), tuple(last_visible))


@pytest.fixture(scope="module")
def baked(organ_table_path: Path) -> Baked:
    return make_baked(gg.load_table(organ_table_path))


@pytest.fixture(scope="module")
def seed2_table(tmp_path_factory: pytest.TempPathFactory) -> gg.OrganTable:
    out = tmp_path_factory.mktemp("organs_seed2") / "grass.json"
    result = subprocess.run([sys.executable, str(LPY_DIR / "export_organs.py"), str(LPY_DIR / "grass.lpy"), str(out),
                             "--seed", "2"], capture_output=True, text=True, timeout=EXPORT_TIMEOUT_S)
    if result.returncode != 0:
        raise RuntimeError(f"export_organs.py --seed 2 failed ({result.returncode}): {result.stderr.strip()}")
    return gg.load_table(out)


def leaf_vertices(rng: gg.LeafRange) -> range:
    return range(rng.sheath[0], rng.blade[1])


def vec(values: Sequence[float], i: int, n: int = 3) -> tuple[float, ...]:
    return tuple(values[i * n:(i + 1) * n])


def stage_vec(values: Sequence[float], k: int, count: int, i: int, n: int = 3) -> tuple[float, ...]:
    return vec(values, k * count + i, n)


def close(a: Sequence[float], b: Sequence[float], tol: float = TOLERANCE) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def area_sums(positions: Sequence[gg.Vec3], faces: Sequence[gg.Quad]) -> list[list[float]]:
    """Per vertex, the sum of its incident triangles' cross products ((a, b, c) and (a, c, d): the winding's side)."""
    sums = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c, d in faces:
        for tri in ((a, b, c), (a, c, d)):
            p0, p1, p2 = (positions[v] for v in tri)
            e1 = [p1[j] - p0[j] for j in range(3)]
            e2 = [p2[j] - p0[j] for j in range(3)]
            n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            for v in (a, b, c, d):
                for j in range(3):
                    sums[v][j] += n[j]
    return sums


def test_header(baked: Baked) -> None:
    d, table = baked.decoded, baked.table
    assert d.species == "grass" and d.species_version == 1 and d.frame == Frame("+Z", "m")
    assert list(d.organ_ids) == gg.lifetime_leaves(table)
    assert list(d.stage_times) == list(table.sample_times)
    assert d.preview_stage == len(table.samples) - 1
    assert d.species_params == {"model": table.model, "seed": table.seed, "params": dict(table.params),
                                "geometry": dataclasses.asdict(gg.GeometryParams())}
    assert {b.name: (b.domain, b.timing, b.components, b.storage) for b in d.blocks} == {
        "position": ("vertex", "stage", 3, "f16"), "normal": ("vertex", "stage", 3, "oct16"),
        "organ": ("vertex", "static", 1, "u32"), "kind": ("vertex", "static", 1, "u32"),
        "s": ("vertex", "static", 1, "f32"), "uv": ("vertex", "static", 2, "f32"),
        "lifespan": ("organ", "static", 2, "f32"), "senescence": ("organ", "static", 3, "f32"),
        **{n: ("organ", "static", 1, "f32") for n in ("draw", "phase", "tiller_az")},
        "tiller": ("organ", "static", 1, "u32"),
        **{n: ("organ", "static", 3, "f32") for n in ("tiller_base", "tiller_axis", "side")},
        **{n: ("organ", "stage", 3, "f32") for n in ("collar", "direction")},
        **{n: ("organ", "stage", 1, "f32") for n in ("arch", "sheath_length", "emergence")}}


def test_every_block_has_meta(baked: Baked) -> None:
    units = {"m", "rad", "P", "1", "index"}
    for b in baked.decoded.blocks:
        assert b.meta is not None, b.name
        names = b.meta["components"]
        assert len(names) == b.components and len(set(names)) == len(names) and all(names), b.name
        assert b.meta["unit"] in units, (b.name, b.meta["unit"])
    units_by_name = {b.name: b.meta["unit"] for b in baked.decoded.blocks if b.meta is not None}
    assert units_by_name["position"] == "m" and units_by_name["lifespan"] == "P" and units_by_name["arch"] == "rad"


def test_positions_equal_build_plant_and_collapsed_leaves_hold(baked: Baked) -> None:
    positions, count = baked.block("position"), len(baked.meshes[0].positions)
    worst = 0.0
    carried = 0
    for k, mesh in enumerate(baked.meshes):
        for rng in mesh.leaves:
            if baked.collapsed[k][rng.id]:
                src = baked.last_visible[k][rng.id]
                carried += 1
                for v in leaf_vertices(rng):
                    assert stage_vec(positions, k, count, v) == stage_vec(positions, src, count, v)
                continue
            for v in leaf_vertices(rng):
                for got, want in zip(stage_vec(positions, k, count, v), mesh.positions[v], strict=True):
                    error = abs(got - want)
                    assert error <= abs(want) * F16_RELATIVE + F16_ABSOLUTE, (k, rng.id, v, got, want)
                    worst = max(worst, error)
    assert carried > 0, "the default export should collapse some leaves; the carry-forward went untested"
    assert worst < 2e-4  # metres; |position| stays below 0.3 m


def test_one_triangle_list_from_the_quads(baked: Baked) -> None:
    for mesh in baked.meshes:
        assert list(baked.decoded.triangles) == [i for a, b, c, d in mesh.faces for i in (a, b, c, a, c, d)]


def test_normals_are_unit_and_on_the_underside(baked: Baked) -> None:
    normals, count = baked.block("normal"), len(baked.meshes[0].positions)
    sums = [area_sums(m.positions, m.faces) for m in baked.meshes]
    checked = 0
    for k, mesh in enumerate(baked.meshes):
        for rng in mesh.leaves:
            src = baked.last_visible[k][rng.id] if baked.collapsed[k][rng.id] else k
            for v in leaf_vertices(rng):
                n = stage_vec(normals, k, count, v)
                assert math.isclose(math.hypot(*n), 1.0, abs_tol=1e-6)
                s = sums[src][v]
                if any(s):
                    assert sum(a * b for a, b in zip(n, s)) > 0.0, (k, rng.id, v)
                    checked += 1
                else:
                    assert close(n, (0.0, 0.0, 1.0)), (k, rng.id, v, n)
                if baked.unborn[k][rng.id]:
                    assert close(n, (0.0, 0.0, 1.0))
    assert checked > 0


def test_vertex_static_blocks_equal_the_mesh(baked: Baked) -> None:
    mesh = baked.meshes[0]
    assert list(baked.block("organ")) == [int(o) - 1 for o in mesh.scalars["organ"]]
    assert list(baked.block("kind")) == [int(k) for k in mesh.scalars["kind"]]
    assert close(baked.block("s"), mesh.scalars["s"])
    uv = baked.block("uv")
    for face, face_uv in zip(mesh.faces, mesh.face_uvs):
        for v, want in zip(face, face_uv):
            assert close(vec(uv, v, 2), want)


def test_vertex_uvs_reject_a_seam(baked: Baked) -> None:
    mesh = baked.meshes[0]
    first = mesh.face_uvs[0]  # corner c of the first quad is shared with its neighbours
    seamed = dataclasses.replace(mesh, face_uvs=((first[0], first[1], (0.5, 0.5), first[3]), *mesh.face_uvs[1:]))
    with pytest.raises(BakeError, match=f"vertex {mesh.faces[0][2]} has two UVs"):
        vertex_uvs(seamed)


def test_organ_static_blocks_equal_the_mesh(baked: Baked) -> None:
    mesh = baked.meshes[-1]
    for o, rng in enumerate(mesh.leaves):
        for v in leaf_vertices(rng):
            for name in ("draw", "phase", "tiller_az"):
                assert math.isclose(baked.block(name)[o], mesh.scalars[name][v], rel_tol=1e-6, abs_tol=1e-7), name
            assert baked.block("tiller")[o] == mesh.scalars["tiller"][v]
            assert math.isclose(baked.block("lifespan")[2 * o], mesh.scalars["birth"][v], rel_tol=1e-6)
            for name in ("tiller_base", "tiller_axis", "side"):
                assert close(vec(baked.block(name), o), mesh.vectors[name][v]), name


def test_organ_stage_blocks_rebuild_pivot_axis_and_stem_t(baked: Baked) -> None:
    plateau = gg.GrassModelParams.from_mapping(baked.table.params).sheath_plateau_m
    organs = len(baked.decoded.organ_ids)
    collar, direction = baked.block("collar"), baked.block("direction")
    arch, sheath_length, emergence = baked.block("arch"), baked.block("sheath_length"), baked.block("emergence")
    base, axis = baked.block("tiller_base"), baked.block("tiller_axis")
    kind, s_block = baked.block("kind"), baked.block("s")
    for k, mesh in enumerate(baked.meshes):
        for o, rng in enumerate(mesh.leaves):
            if baked.collapsed[k][rng.id]:
                src = baked.last_visible[k][rng.id]
                for block, n in ((collar, 3), (direction, 3), (arch, 1), (sheath_length, 1), (emergence, 1)):
                    assert stage_vec(block, k, organs, o, n) == stage_vec(block, src, organs, o, n)
                continue
            for v in leaf_vertices(rng):
                assert math.isclose(stage_vec(arch, k, organs, o, 1)[0], mesh.scalars["arch"][v], abs_tol=1e-6)
                assert close(stage_vec(collar, k, organs, o), mesh.vectors["collar"][v])
                if kind[v] == gg.KIND_BLADE:
                    pivot, ax = stage_vec(collar, k, organs, o), stage_vec(direction, k, organs, o)
                    stem_t = stage_vec(emergence, k, organs, o, 1)[0] / plateau
                else:
                    pivot, ax = vec(base, o), vec(axis, o)
                    stem_t = stage_vec(sheath_length, k, organs, o, 1)[0] * s_block[v] / plateau
                assert close(pivot, mesh.vectors["pivot"][v]), (k, rng.id, v)
                assert close(ax, mesh.vectors["axis"][v]), (k, rng.id, v)
                assert math.isclose(stem_t, mesh.scalars["stem_t"][v], abs_tol=1e-6), (k, rng.id, v)


def test_lifespan_and_senescence_reproduce_sen_fade_and_the_hide_rule(baked: Baked) -> None:
    fade_duration = float(baked.table.params["FADE_DURATION"])  # type: ignore[arg-type]
    lifespan, senescence = baked.block("lifespan"), baked.block("senescence")
    for o, lid in enumerate(baked.decoded.organ_ids):
        e = baked.table.events[lid]
        times = (e.senescent_time, e.senescence_duration, e.death_time)
        assert vec(senescence, o) == pytest.approx([NEVER if x is None else x for x in times], rel=1e-6)
        assert lifespan[2 * o + 1] == pytest.approx(NEVER if e.collapse_time is None else e.collapse_time, rel=1e-6)
    hidden = 0
    for k, (t, mesh) in enumerate(zip(baked.table.sample_times, baked.meshes)):
        for o, rng in enumerate(mesh.leaves):
            appear, vanish = vec(lifespan, o, 2)
            visible = not (baked.collapsed[k][rng.id] or baked.unborn[k][rng.id])
            # Event times are on the model's step grid and stored as f32, so compare with TOLERANCE. A leaf is not in
            # the sample at its own birth time (zero size): alive by the rule there, but degenerate (zero area).
            if visible:
                assert appear - TOLERANCE <= t < vanish - TOLERANCE, (t, rng.id, appear, vanish)
            elif baked.unborn[k][rng.id]:
                assert t <= appear + TOLERANCE, (t, rng.id, appear)
            else:
                assert t >= vanish - TOLERANCE, (t, rng.id, vanish)
            hidden += not visible
            if baked.collapsed[k][rng.id]:
                continue
            senescent_time, duration, death_time = vec(senescence, o)
            sen = min(max((t - senescent_time) / duration, 0.0), 1.0)
            fade = min(max((t - death_time) / fade_duration, 0.0), 1.0)
            b = rng.blade[0]
            assert math.isclose(sen, mesh.scalars["sen"][b], abs_tol=1e-6), (t, rng.id)
            if visible:
                assert math.isclose(fade, mesh.scalars["fade"][b], abs_tol=1e-6), (t, rng.id)
    assert hidden > 0


def read_preview_positions(data: bytes) -> list[tuple[float, float, float]]:
    """The glTF POSITION accessor of a GLB (the preview mesh)."""
    json_length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + json_length])
    binary = data[28 + json_length:]
    accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = gltf["bufferViews"][accessor["bufferView"]]
    flat = struct.unpack_from(f"<{accessor['count'] * 3}f", binary, view.get("byteOffset", 0))
    return [flat[i:i + 3] for i in range(0, len(flat), 3)]


def test_preview_collapses_organs_not_alive_at_t_end(baked: Baked) -> None:
    preview = read_preview_positions(baked.data)
    positions, count, last = baked.block("position"), len(baked.meshes[0].positions), len(baked.table.samples) - 1
    t_end = baked.table.sample_times[-1]
    lifespan = baked.block("lifespan")
    collapsed = 0
    for o, rng in enumerate(baked.meshes[-1].leaves):
        appear, vanish = vec(lifespan, o, 2)
        vertices = leaf_vertices(rng)
        if appear <= t_end < vanish:
            for v in vertices:
                assert tuple(preview[v]) == stage_vec(positions, last, count, v), (rng.id, v)
        else:
            collapsed += 1
            assert all(tuple(preview[v]) == tuple(preview[vertices[0]]) for v in vertices), rng.id
    assert collapsed > 0, "the default export should have leaves collapsed by t_end"


def test_a_second_seed_bakes(baked: Baked, seed2_table: gg.OrganTable) -> None:
    other = make_baked(seed2_table)
    assert other.decoded.species_params["seed"] == 2 != baked.decoded.species_params["seed"]
    assert list(other.decoded.organ_ids) == gg.lifetime_leaves(seed2_table)
    count = len(other.meshes[0].positions)
    assert len(other.block("position")) == 3 * count * len(seed2_table.samples)
    assert other.block("position") != baked.block("position")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on the PATH")
def test_node_round_trip_matches_decode_vat(baked: Baked, tmp_path: Path) -> None:
    path = tmp_path / "grass.vat.glb"
    path.write_bytes(encode_vat(baked.plant))
    result = subprocess.run(["node", str(VAT_DIR / "tests" / "decode_vat.mjs"), str(path)],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    js = json.loads(result.stdout)
    assert js["indices"] == list(baked.decoded.triangles)
    assert js["stageTimes"] == list(baked.decoded.stage_times) and js["never"] == NEVER
    assert set(js["blocks"]) == {b.name for b in baked.decoded.blocks}
    for b in baked.decoded.blocks:
        assert js["blocks"][b.name] == pytest.approx(list(b.values), rel=1e-6, abs=1e-6), b.name


def test_cli_writes_the_file_and_prints_counts(organ_table_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "grass.vat.glb"
    result = subprocess.run([sys.executable, str(PLANT_DIR / "bake_grass_vat.py"), str(organ_table_path), str(out)],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    plant = decode_vat(out.read_bytes())
    vertices = len(next(b for b in plant.blocks if b.name == "organ").values)
    for text in (f"{vertices} vertices", f"{len(plant.organ_ids)} organs", f"{len(plant.stage_times)} stages",
                 f"{len(plant.triangles) // 3} triangles", f"{out.stat().st_size} bytes"):
        assert text in result.stdout


def test_resolution_options_change_the_vertices_per_leaf(organ_table_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "low.vat.glb"
    assert main([str(organ_table_path), str(out), "--blade-segments", "12", "--cross-segments", "6"]) == 0
    plant = decode_vat(out.read_bytes())
    p = dataclasses.replace(gg.GeometryParams(), blade_segments=12, cross_segments=6)
    per_leaf = (p.sheath_rows + 12 + 1) * (6 + 1)
    assert per_leaf == 112
    organ = next(b for b in plant.blocks if b.name == "organ").values
    assert len(organ) == per_leaf * len(plant.organ_ids)
    assert all(organ.count(o) == per_leaf for o in range(len(plant.organ_ids)))
    assert plant.species_params["geometry"]["blade_segments"] == 12
    assert plant.species_params["geometry"]["cross_segments"] == 6


def test_sheath_rows_option_gives_the_coarsest_leaf(organ_table_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "coarse.vat.glb"
    assert main([str(organ_table_path), str(out), "--blade-segments", "6", "--cross-segments", "2",
                 "--sheath-rows", "1"]) == 0
    plant = decode_vat(out.read_bytes())
    per_leaf = (1 + 6 + 1) * (2 + 1)
    assert per_leaf == 24
    organ = next(b for b in plant.blocks if b.name == "organ").values
    assert len(organ) == per_leaf * len(plant.organ_ids)
    assert all(organ.count(o) == per_leaf for o in range(len(plant.organ_ids)))
    assert plant.species_params["geometry"]["sheath_rows"] == 1


def test_profile_lists_every_block_with_its_shape(baked: Baked) -> None:
    rows = {}
    for line in (PLANT_DIR / "grass_vat_profile.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells
    assert set(rows) == {b.name for b in baked.decoded.blocks}
    for b in baked.decoded.blocks:
        _, shape, components, storage, unit, _ = rows[b.name]
        assert shape == f"{b.domain}, {b.timing}" and storage == b.storage and unit == b.meta["unit"], b.name
        assert [c.strip() for c in components.split(",")] == list(b.meta["components"]), b.name

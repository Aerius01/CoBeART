"""Smoke test of build_grass_from_table.py under headless Blender (GP Phase 3, WP 3.3, G33)."""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

import grass_geometry as gg

SCRIPT = Path(__file__).resolve().parent.parent / "build_grass_from_table.py"
BLENDER_TIMEOUT_S = 120
GLB_MAGIC, JSON_CHUNK = b"glTF", 0x4E4F534A


def glb_json(path: Path) -> dict[str, object]:
    """The JSON chunk of a GLB file."""
    data = path.read_bytes()
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    assert magic == GLB_MAGIC, f"{path} is not a GLB"
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    assert chunk_type == JSON_CHUNK
    return json.loads(data[20:20 + chunk_length])


def test_blender_builds_glb_and_stages(organ_table_path: Path, tmp_path: Path) -> None:
    blender = shutil.which("blender")
    if blender is None:
        pytest.skip("blender is not on the PATH; install it or add it to run the Blender smoke test")
    glb, stages = tmp_path / "p.glb", tmp_path / "s.blend"
    result = subprocess.run([blender, "--background", "--factory-startup", "--python", str(SCRIPT), "--",
                             str(organ_table_path), "--glb", str(glb), "--stages", str(stages)],
                            capture_output=True, text=True, timeout=BLENDER_TIMEOUT_S)
    assert result.returncode == 0, \
        f"blender failed ({result.returncode}):\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    assert glb.is_file() and stages.is_file()

    table = gg.load_table(organ_table_path)
    expected = gg.build_plant(table, table.t_end, lifetime=gg.lifetime_leaves(table))
    document = glb_json(glb)
    primitives = [prim for mesh in document["meshes"] for prim in mesh["primitives"]]  # type: ignore[index]
    assert primitives, "the GLB has no mesh primitives"
    wanted = {"POSITION", *(gg.gltf_name(n) for n in (*gg.VECTOR_ATTRIBUTES, *gg.SCALAR_ATTRIBUTES))}
    for prim in primitives:
        missing = wanted - set(prim["attributes"])
        assert not missing, f"primitive lacks attributes {sorted(missing)}"
    accessors = document["accessors"]
    total = sum(accessors[prim["attributes"]["POSITION"]]["count"] for prim in primitives)  # type: ignore[index]
    assert total == len(expected.positions), f"GLB has {total} vertices, the mesh {len(expected.positions)}"


def test_blender_exits_nonzero_on_bad_input(tmp_path: Path) -> None:
    blender = shutil.which("blender")
    if blender is None:
        pytest.skip("blender is not on the PATH; install it or add it to run the Blender smoke test")
    result = subprocess.run([blender, "--background", "--factory-startup", "--python", str(SCRIPT), "--",
                             str(tmp_path / "missing.json"), "--glb", str(tmp_path / "p.glb")],
                            capture_output=True, text=True, timeout=BLENDER_TIMEOUT_S)
    assert result.returncode != 0, f"blender exited 0 on a missing organ table:\n{result.stdout[-1000:]}"
    assert "Traceback" in result.stderr + result.stdout

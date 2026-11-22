"""Tests for the generic VAT writer (run with the lpy interpreter): codecs, container validity, errors, node parity."""
from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable

import pytest

VAT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VAT_DIR))

from make_fixture import FIXTURE_PATH, build_tiny_plant  # noqa: E402
from vat_writer import (  # noqa: E402
    NEVER, Block, VatError, VatPlant, decode_oct, decode_vat, encode_oct, encode_vat, write_vat,
)

PLANT = build_tiny_plant()
DATA = encode_vat(PLANT)
OCT_TOLERANCE_DEG = 0.01


def block(name: str, plant: VatPlant = PLANT) -> Block:
    return next(b for b in plant.blocks if b.name == name)


def with_block(name: str, **changes: object) -> VatPlant:
    return replace(PLANT, blocks=[replace(b, **changes) if b.name == name else b for b in PLANT.blocks])


def with_value(name: str, index: int, value: float | int) -> VatPlant:
    values = list(block(name).values)
    values[index] = value
    return with_block(name, values=values)


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def angle_deg(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def read_glb(data: bytes) -> tuple[dict, bytes]:
    magic, version, total = struct.unpack_from("<III", data, 0)
    assert (magic, version, total) == (0x46546C67, 2, len(data))
    json_length, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A and json_length % 4 == 0
    bin_length, bin_type = struct.unpack_from("<II", data, 20 + json_length)
    assert bin_type == 0x004E4942 and bin_length % 4 == 0
    assert 28 + json_length + bin_length == len(data)
    return json.loads(data[20:20 + json_length]), data[28 + json_length:]


def test_round_trip_within_each_storage_tolerance() -> None:
    decoded = decode_vat(DATA)
    assert decoded.species == "tiny" and decoded.species_params == PLANT.species_params
    assert tuple(decoded.organ_ids) == PLANT.organ_ids and tuple(decoded.stage_times) == PLANT.stage_times
    assert list(decoded.triangles) == list(PLANT.triangles) and decoded.preview_stage == 2
    storages = set()
    for original, back in zip(PLANT.blocks, decoded.blocks):
        assert (original.name, original.domain, original.timing, original.components, original.storage) == \
               (back.name, back.domain, back.timing, back.components, back.storage)
        storages.add(original.storage)
        if original.storage == "f32":
            assert [f32(v) for v in original.values] == list(back.values)
        elif original.storage == "u32":
            assert list(original.values) == list(back.values)
        elif original.storage == "f16":
            for a, b in zip(original.values, back.values):
                assert abs(a - b) <= abs(a) * 2 ** -11
        else:
            for k in range(0, len(original.values), 3):
                src, dst = tuple(original.values[k:k + 3]), tuple(back.values[k:k + 3])
                assert math.isclose(math.hypot(*dst), 1.0, abs_tol=1e-9)
                assert angle_deg(src, dst) < OCT_TOLERANCE_DEG
    assert storages == {"f32", "f16", "u32", "oct16"}


def test_f16_rounds_to_nearest_even() -> None:
    # 2049 lies exactly between the float16 values 2048 and 2050, and the even mantissa (2048) wins; 2051 goes to 2052
    plant = with_value("collar", 0, 2049.0)
    plant = replace(plant, blocks=[replace(b, values=[*b.values[:1], 2051.0, *b.values[2:]]) if b.name == "collar"
                                   else b for b in plant.blocks])
    assert decode_vat(encode_vat(plant)).blocks[-1].values[:2] == (2048.0, 2052.0)


@pytest.mark.parametrize("vector", [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, -1, 0), (-1, -1, -1), (1, 2, -3),
                                    (-0.3, 0.2, -0.9), (1e-6, 0, -1)])
def test_octahedral_axes_and_lower_hemisphere(vector: tuple[float, float, float]) -> None:
    assert angle_deg(vector, decode_oct(*encode_oct(*vector))) < OCT_TOLERANCE_DEG


def test_octahedral_sweep() -> None:
    worst = max(angle_deg((a, b, c), decode_oct(*encode_oct(a, b, c)))
                for a in range(-10, 11) for b in range(-10, 11) for c in range(-10, 11) if (a, b, c) != (0, 0, 0))
    assert worst < OCT_TOLERANCE_DEG


def test_bytes_are_deterministic_and_odd_f16_is_padded() -> None:
    assert encode_vat(build_tiny_plant()) == DATA
    one = VatPlant("t", {}, ["a"], [0.0], [0, 1, 2], 0, [
        Block("position", "vertex", "stage", 3, "f32", [0.0] * 9),
        Block("organ", "vertex", "static", 1, "u32", [0, 0, 0]),
        Block("odd", "organ", "static", 1, "f16", [1.5])])
    data = encode_vat(one)
    gltf, binary = read_glb(data)
    assert len(binary) % 4 == 0
    assert any(v["byteLength"] == 2 for v in gltf["bufferViews"])
    assert decode_vat(data).blocks[-1].values == (1.5,)


def test_committed_fixture_equals_fresh_write(tmp_path: Path) -> None:
    out = tmp_path / "fresh.vat.glb"
    write_vat(build_tiny_plant(), out)
    assert out.read_bytes() == FIXTURE_PATH.read_bytes() == DATA
    assert len(DATA) < 8192


def test_glb_is_valid_gltf() -> None:
    gltf, binary = read_glb(DATA)
    assert gltf["asset"]["version"] == "2.0" and gltf["buffers"] == [{"byteLength": len(binary)}]
    for view in gltf["bufferViews"]:
        assert view["buffer"] == 0 and view["byteOffset"] % 4 == 0
        assert view["byteOffset"] + view["byteLength"] <= len(binary)
    prim = gltf["meshes"][0]["primitives"][0]
    assert set(prim["attributes"]) == {"POSITION", "NORMAL", "TEXCOORD_0"} and prim["mode"] == 4
    header = gltf["extras"]["vat"]
    vertex_count = header["vertex_count"]
    sizes = {"VEC3": 3, "VEC2": 2}
    floats: dict[str, tuple[float, ...]] = {}
    for name, index in prim["attributes"].items():
        acc = gltf["accessors"][index]
        view = gltf["bufferViews"][acc["bufferView"]]
        assert acc["componentType"] == 5126 and acc["count"] == vertex_count
        assert view["byteLength"] == acc["count"] * sizes[acc["type"]] * 4
        floats[name] = struct.unpack_from(f"<{vertex_count * sizes[acc['type']]}f", binary, view["byteOffset"])
    pos = gltf["accessors"][prim["attributes"]["POSITION"]]
    assert pos["min"] == [min(floats["POSITION"][c::3]) for c in range(3)]
    assert pos["max"] == [max(floats["POSITION"][c::3]) for c in range(3)]
    idx = gltf["accessors"][prim["indices"]]
    assert idx["componentType"] == 5125 and idx["type"] == "SCALAR" and idx["count"] % 3 == 0
    indices = struct.unpack_from(f"<{idx['count']}I", binary, gltf["bufferViews"][idx["bufferView"]]["byteOffset"])
    assert max(indices) < vertex_count
    # the preview mesh is the preview stage (2) of the position and normal blocks and the static uv block
    decoded = decode_vat(DATA)
    n = vertex_count * 3
    assert list(floats["POSITION"]) == list(block("position", decoded).values[2 * n:3 * n])
    assert list(floats["NORMAL"]) == pytest.approx(list(block("normal", decoded).values[2 * n:3 * n]), abs=1e-6)
    assert list(floats["TEXCOORD_0"]) == list(block("uv", decoded).values)
    assert header["format"] == "cobeart-vat" and header["version"] == 1 and header["never"] == NEVER
    assert [b["name"] for b in header["blocks"]] == [b.name for b in PLANT.blocks]


def test_events_keep_never_as_a_finite_float() -> None:
    assert block("events", decode_vat(DATA)).values[6:] == (f32(NEVER),) * 2


ERRORS: list[tuple[str, Callable[[], VatPlant], str]] = [
    ("values length", lambda: with_block("arch", values=[0.0] * 5), r"block 'arch' has 5 values, expected 6"),
    ("non-finite", lambda: with_value("uv", 7, float("nan")), r"block 'uv' element 3, component 1: value nan"),
    ("inf stage", lambda: with_value("collar", 13, float("inf")),
     r"block 'collar' stage 2, element 0, component 1: value inf is not finite"),
    ("f16 range", lambda: with_value("collar", 6, 70000.0),
     r"block 'collar' stage 1, element 0, component 0: .*float16 range"),
    ("f32 range", lambda: with_value("birth", 0, 1e39), r"block 'birth' element 0.*float32 range"),
    ("u32 float", lambda: with_value("kind", 1, 1.5), r"block 'kind' element 1.*must be an integer"),
    ("oct zero", lambda: with_block("normal", values=[*block("normal").values[:30], 0.0, 0.0, 0.0,
                                                      *block("normal").values[33:]]),
     r"block 'normal' stage 0, element 10.*zero vector"),
    ("organ index", lambda: with_value("organ", 11, 2), r"block 'organ' element 11: organ index 2"),
    ("triangle index", lambda: replace(PLANT, triangles=[*PLANT.triangles[:-1], 12]),
     r"triangles\[23\] = 12 .* below vertex_count 12"),
    ("triangle count", lambda: replace(PLANT, triangles=[*PLANT.triangles, 0]), r"multiple of 3"),
    ("duplicate name", lambda: replace(PLANT, blocks=[*PLANT.blocks, block("arch")]),
     r"block name 'arch' is used twice"),
    ("missing position", lambda: replace(PLANT, blocks=[b for b in PLANT.blocks if b.name != "position"]),
     r"required block 'position' is missing"),
    ("missing organ", lambda: replace(PLANT, blocks=[b for b in PLANT.blocks if b.name != "organ"]),
     r"required block 'organ' is missing"),
    ("oct components", lambda: with_block("normal", components=2), r"block 'normal': oct16 requires components 3"),
    ("storage", lambda: with_block("s", storage="f64"), r"block 's': storage 'f64'"),
    ("components range", lambda: with_block("s", components=5), r"block 's': components 5"),
    ("stage times", lambda: replace(PLANT, stage_times=(0.0, 1.5, 1.5)), r"strictly increasing: stage 2"),
    ("preview stage", lambda: replace(PLANT, preview_stage=3), r"preview_stage 3 must be an index below 3"),
    ("params", lambda: replace(PLANT, species_params={"x": float("nan")}), r"species_params"),
]


@pytest.mark.parametrize(("make", "pattern"), [(e[1], e[2]) for e in ERRORS], ids=[e[0] for e in ERRORS])
def test_validation_errors_carry_context(make: Callable[[], VatPlant], pattern: str) -> None:
    with pytest.raises(VatError, match=pattern):
        encode_vat(make())


def test_decode_rejects_truncated_and_mislabelled_files() -> None:
    with pytest.raises(VatError, match="truncated"):
        decode_vat(DATA[:-8])
    with pytest.raises(VatError, match="not a glTF 2.0 binary"):
        decode_vat(b"\0" * 64)
    gltf, binary = read_glb(DATA)
    gltf["extras"]["vat"]["organ_count"] = 3
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    forged = (struct.pack("<III", 0x46546C67, 2, 28 + len(text) + len(binary))
              + struct.pack("<II", len(text), 0x4E4F534A) + text
              + struct.pack("<II", len(binary), 0x004E4942) + binary)
    with pytest.raises(VatError, match=r"block 'birth': bufferView \d+ is 8 bytes, header implies 12"):
        decode_vat(forged)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on the PATH")
def test_node_loader_matches_python_decode() -> None:
    result = subprocess.run(["node", str(VAT_DIR / "tests" / "decode_vat.mjs"), str(FIXTURE_PATH)],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    js = json.loads(result.stdout)
    decoded = decode_vat(FIXTURE_PATH.read_bytes())
    assert js["indices"] == list(decoded.triangles) and js["stageTimes"] == list(decoded.stage_times)
    assert js["never"] == NEVER
    assert set(js["blocks"]) == {b.name for b in decoded.blocks}
    for b in decoded.blocks:
        assert js["blocks"][b.name] == pytest.approx(list(b.values), abs=1e-6, rel=1e-6), b.name

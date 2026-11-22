"""Generic VAT writer and reader (format: vat_format.md). Stdlib only, so it also runs inside Blender's Python."""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

Domain = Literal["vertex", "organ"]
Timing = Literal["static", "stage"]
Storage = Literal["f32", "f16", "u32", "oct16"]

FORMAT = "cobeart-vat"
VERSION = 1
NEVER = 1.0e30
F16_MAX = 65504.0
F32_MAX = 3.4028234663852886e38
U32_MAX = 0xFFFFFFFF
SNORM16 = 32767

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
GL_FLOAT = 5126
GL_UNSIGNED_INT = 5125
GL_ARRAY_BUFFER = 34962
GL_ELEMENT_ARRAY_BUFFER = 34963

DOMAINS: tuple[str, ...] = ("vertex", "organ")
TIMINGS: tuple[str, ...] = ("static", "stage")
STORAGE_BYTES: Mapping[str, int] = {"f32": 4, "f16": 2, "u32": 4, "oct16": 4}


class VatError(ValueError):
    """A VAT file or plant that violates the format."""


@dataclass(frozen=True)
class Block:
    """One named data block. `values` are flat, stage-major for stage blocks."""

    name: str
    domain: Domain
    timing: Timing
    components: int
    storage: Storage
    values: Sequence[float | int]


@dataclass(frozen=True)
class VatPlant:
    """A whole plant: organs, stage times, triangles and blocks."""

    species: str
    species_params: Mapping[str, Any]
    organ_ids: Sequence[str]
    stage_times: Sequence[float]
    triangles: Sequence[int]
    preview_stage: int
    blocks: Sequence[Block]


def _sign_not_zero(v: float) -> float:
    return 1.0 if v >= 0.0 else -1.0


def encode_oct(x: float, y: float, z: float) -> tuple[int, int]:
    """Octahedral encoding of a non-zero vector (normalised here) to two int16 snorm."""
    length = abs(x) + abs(y) + abs(z)
    if not length > 0.0 or not math.isfinite(length):
        raise VatError(f"cannot octahedral-encode vector ({x}, {y}, {z}); pass a finite non-zero vector")
    px, py = x / length, y / length
    if z < 0.0:
        px, py = (1.0 - abs(py)) * _sign_not_zero(px), (1.0 - abs(px)) * _sign_not_zero(py)
    return (round(max(-1.0, min(1.0, px)) * SNORM16), round(max(-1.0, min(1.0, py)) * SNORM16))


def decode_oct(i: int, j: int) -> tuple[float, float, float]:
    """Unit vector from two int16 snorm octahedral values."""
    x, y = max(-1.0, i / SNORM16), max(-1.0, j / SNORM16)
    z = 1.0 - abs(x) - abs(y)
    if z < 0.0:
        x, y = (1.0 - abs(y)) * _sign_not_zero(x), (1.0 - abs(x)) * _sign_not_zero(y)
    norm = math.sqrt(x * x + y * y + z * z)
    return (x / norm, y / norm, z / norm)


def _element_count(domain: str, vertex_count: int, organ_count: int) -> int:
    return vertex_count if domain == "vertex" else organ_count


def _locate(index: int, components: int, per_stage: int, timing: str) -> str:
    """Human-readable position of flat value `index`: stage, element and component."""
    element, component = divmod(index % per_stage if timing == "stage" else index, components)
    stage = f"stage {index // per_stage}, " if timing == "stage" else ""
    return f"{stage}element {element}, component {component}"


def _check_block_values(block: Block, count: int, stage_count: int) -> None:
    per_stage = count * block.components
    expected = per_stage * (stage_count if block.timing == "stage" else 1)
    if len(block.values) != expected:
        raise VatError(
            f"block '{block.name}' has {len(block.values)} values, expected {expected} "
            f"({count} {block.domain} elements x {block.components} components"
            f"{f' x {stage_count} stages' if block.timing == 'stage' else ''})")
    for index, value in enumerate(block.values):
        where = f"block '{block.name}' {_locate(index, block.components, per_stage, block.timing)}"
        if block.storage == "u32":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= U32_MAX:
                raise VatError(f"{where}: u32 value {value!r} must be an integer in 0..{U32_MAX}")
            continue
        if not math.isfinite(value):
            raise VatError(f"{where}: value {value!r} is not finite")
        if block.storage == "f32" and abs(value) > F32_MAX:
            raise VatError(f"{where}: value {value!r} is beyond the float32 range")
        if block.storage == "f16" and abs(value) > F16_MAX:
            raise VatError(f"{where}: value {value!r} is beyond the float16 range (+-{F16_MAX}); use f32 storage")
    if block.storage == "oct16":
        for start in range(0, len(block.values), 3):
            if not any(block.values[start:start + 3]):
                raise VatError(f"block '{block.name}' {_locate(start, 3, per_stage, block.timing)}: "
                               "zero vector cannot be octahedral-encoded")


def _validate_block_shape(block: Block) -> None:
    if not isinstance(block.name, str) or not block.name:
        raise VatError(f"block name {block.name!r} must be a non-empty string")
    if block.domain not in DOMAINS:
        raise VatError(f"block '{block.name}': domain {block.domain!r} must be one of {DOMAINS}")
    if block.timing not in TIMINGS:
        raise VatError(f"block '{block.name}': timing {block.timing!r} must be one of {TIMINGS}")
    if block.storage not in STORAGE_BYTES:
        raise VatError(f"block '{block.name}': storage {block.storage!r} must be one of {tuple(STORAGE_BYTES)}")
    if not isinstance(block.components, int) or not 1 <= block.components <= 4:
        raise VatError(f"block '{block.name}': components {block.components!r} must be an integer in 1..4")
    if block.storage == "oct16" and block.components != 3:
        raise VatError(f"block '{block.name}': oct16 requires components 3, got {block.components}")


def _validate_plant(plant: VatPlant) -> int:
    """Checks the whole plant and returns its vertex count."""
    if not plant.stage_times:
        raise VatError("stage_times is empty; give at least one stage time")
    for k, t in enumerate(plant.stage_times):
        if not math.isfinite(t):
            raise VatError(f"stage_times[{k}] = {t!r} is not finite")
        if k and not t > plant.stage_times[k - 1]:
            raise VatError(f"stage_times must be strictly increasing: stage {k} ({t}) <= stage {k - 1} "
                           f"({plant.stage_times[k - 1]})")
    if isinstance(plant.preview_stage, bool) or not 0 <= plant.preview_stage < len(plant.stage_times):
        raise VatError(f"preview_stage {plant.preview_stage!r} must be an index below {len(plant.stage_times)}")
    if not plant.organ_ids or not all(isinstance(o, str) for o in plant.organ_ids):
        raise VatError("organ_ids must be a non-empty list of strings")
    for block in plant.blocks:
        _validate_block_shape(block)
    names = [b.name for b in plant.blocks]
    if len(set(names)) != len(names):
        dup = next(n for n in names if names.count(n) > 1)
        raise VatError(f"block name '{dup}' is used twice; names must be unique")
    by_name = {b.name: b for b in plant.blocks}
    organ, position = by_name.get("organ"), by_name.get("position")
    if organ is None or position is None:
        raise VatError(f"required block '{'organ' if organ is None else 'position'}' is missing")
    if (organ.domain, organ.timing, organ.components, organ.storage) != ("vertex", "static", 1, "u32"):
        raise VatError("block 'organ' must be vertex, static, 1 component, u32")
    if (position.domain, position.timing, position.components) != ("vertex", "stage", 3) \
            or position.storage not in ("f32", "f16"):
        raise VatError("block 'position' must be vertex, stage, 3 components, f32 or f16")
    vertex_count, organ_count, stage_count = len(organ.values), len(plant.organ_ids), len(plant.stage_times)
    if vertex_count == 0:
        raise VatError("block 'organ' is empty; a plant needs at least one vertex")
    for block in plant.blocks:
        _check_block_values(block, _element_count(block.domain, vertex_count, organ_count), stage_count)
    for v, o in enumerate(organ.values):
        if o >= organ_count:
            raise VatError(f"block 'organ' element {v}: organ index {o} is not below organ_count {organ_count}")
    if not plant.triangles or len(plant.triangles) % 3:
        raise VatError(f"triangles has {len(plant.triangles)} indices; need a non-empty multiple of 3")
    for k, index in enumerate(plant.triangles):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < vertex_count:
            raise VatError(f"triangles[{k}] = {index!r} is not an integer below vertex_count {vertex_count}")
    return vertex_count


def _encode_values(storage: str, values: Sequence[float | int]) -> bytes:
    n = len(values)
    if storage == "f32":
        return struct.pack(f"<{n}f", *values)
    if storage == "f16":
        return struct.pack(f"<{n}e", *values)
    if storage == "u32":
        return struct.pack(f"<{n}I", *values)
    pairs = [c for k in range(0, n, 3) for c in encode_oct(values[k], values[k + 1], values[k + 2])]
    return struct.pack(f"<{len(pairs)}h", *pairs)


def _decode_values(storage: str, data: bytes) -> tuple[float | int, ...]:
    if storage in ("f32", "f16", "u32"):
        code = {"f32": "f", "f16": "e", "u32": "I"}[storage]
        return struct.unpack(f"<{len(data) // STORAGE_BYTES[storage]}{code}", data)
    pairs = struct.unpack(f"<{len(data) // 2}h", data)
    return tuple(c for k in range(0, len(pairs), 2) for c in decode_oct(pairs[k], pairs[k + 1]))


def _byte_length(storage: str, value_count: int) -> int:
    """Stored size of `value_count` values; oct16 stores 4 bytes per xyz triple."""
    return value_count // 3 * 4 if storage == "oct16" else value_count * STORAGE_BYTES[storage]


def _pad4(data: bytearray, fill: bytes = b"\0") -> None:
    data.extend(fill * (-len(data) % 4))


def encode_vat(plant: VatPlant) -> bytes:
    """Serialises a plant to a GLB; the same input gives the same bytes."""
    vertex_count = _validate_plant(plant)
    by_name = {b.name: b for b in plant.blocks}
    encoded = {b.name: _encode_values(b.storage, b.values) for b in plant.blocks}

    binary = bytearray()
    views: list[dict[str, Any]] = []

    def add_view(data: bytes, target: int | None = None) -> int:
        _pad4(binary)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        binary.extend(data)
        views.append(view)
        return len(views) - 1

    accessors: list[dict[str, Any]] = []
    attributes: dict[str, int] = {}

    def preview(block: Block) -> tuple[float | int, ...]:
        """The block's stored values as decoded at the preview stage."""
        decoded = _decode_values(block.storage, encoded[block.name])
        if block.timing == "static":
            return decoded
        size = vertex_count * block.components
        return decoded[plant.preview_stage * size:(plant.preview_stage + 1) * size]

    def add_attribute(name: str, values: tuple[float | int, ...], kind: str, width: int, bounds: bool = False) -> None:
        accessor: dict[str, Any] = {"bufferView": add_view(struct.pack(f"<{len(values)}f", *values), GL_ARRAY_BUFFER),
                                    "componentType": GL_FLOAT, "count": vertex_count, "type": kind}
        if bounds:
            columns = [values[c::width] for c in range(width)]
            accessor["min"] = [min(c) for c in columns]
            accessor["max"] = [max(c) for c in columns]
        attributes[name] = len(accessors)
        accessors.append(accessor)

    add_attribute("POSITION", preview(by_name["position"]), "VEC3", 3, bounds=True)
    normal = by_name.get("normal")
    if normal is not None and normal.domain == "vertex" and normal.components == 3:
        add_attribute("NORMAL", preview(normal), "VEC3", 3)
    uv = by_name.get("uv")
    if uv is not None and uv.domain == "vertex" and uv.timing == "static" and uv.components == 2:
        add_attribute("TEXCOORD_0", preview(uv), "VEC2", 2)
    indices_accessor = len(accessors)
    accessors.append({"bufferView": add_view(struct.pack(f"<{len(plant.triangles)}I", *plant.triangles),
                                             GL_ELEMENT_ARRAY_BUFFER),
                      "componentType": GL_UNSIGNED_INT, "count": len(plant.triangles), "type": "SCALAR"})

    block_headers = [{"name": b.name, "domain": b.domain, "timing": b.timing, "components": b.components,
                      "storage": b.storage, "bufferView": add_view(encoded[b.name])} for b in plant.blocks]

    gltf = {
        "asset": {"version": "2.0", "generator": "cobeart vat_writer"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attributes, "indices": indices_accessor, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
        "extras": {"vat": {
            "format": FORMAT, "version": VERSION, "species": plant.species, "species_params": plant.species_params,
            "vertex_count": vertex_count, "organ_count": len(plant.organ_ids), "organ_ids": list(plant.organ_ids),
            "stage_times": list(plant.stage_times), "preview_stage": plant.preview_stage, "never": NEVER,
            "blocks": block_headers}},
    }
    try:
        text = json.dumps(gltf, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as error:
        raise VatError(f"species_params must be finite JSON-serialisable values: {error}") from error
    json_chunk = bytearray(text)
    _pad4(json_chunk, b" ")
    _pad4(binary)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return b"".join([
        struct.pack("<III", GLB_MAGIC, 2, total),
        struct.pack("<II", len(json_chunk), CHUNK_JSON), bytes(json_chunk),
        struct.pack("<II", len(binary), CHUNK_BIN), bytes(binary)])


def write_vat(plant: VatPlant, path: str | Path) -> None:
    """Writes `encode_vat(plant)` to `path`."""
    Path(path).write_bytes(encode_vat(plant))


def _parse_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20:
        raise VatError(f"file is {len(data)} bytes, too short for a GLB header")
    magic, version, total = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2:
        raise VatError(f"not a glTF 2.0 binary (magic {magic:#x}, version {version})")
    if total != len(data):
        raise VatError(f"header declares {total} bytes but the buffer has {len(data)} (truncated or padded file)")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != CHUNK_JSON or 28 + json_length > len(data):
        raise VatError("first chunk is not a JSON chunk that fits the file")
    bin_length, bin_type = struct.unpack_from("<II", data, 20 + json_length)
    start = 28 + json_length
    if bin_type != CHUNK_BIN or start + bin_length > len(data):
        raise VatError("second chunk is not a BIN chunk that fits the file")
    try:
        gltf = json.loads(data[20:20 + json_length].decode("utf-8"))
    except ValueError as error:
        raise VatError(f"JSON chunk is not valid JSON: {error}") from error
    return gltf, data[start:start + bin_length]


def decode_vat(data: bytes) -> VatPlant:
    """Parses and validates a VAT GLB; values come back decoded (`oct16` as unit xyz, `u32` as ints)."""
    gltf, binary = _parse_glb(data)
    header = (gltf.get("extras") or {}).get("vat")
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise VatError(f"extras.vat.format must be '{FORMAT}'")
    if header.get("version") != VERSION:
        raise VatError(f"extras.vat.version is {header.get('version')!r}; this reader supports {VERSION}")
    vertex_count, organ_count = header["vertex_count"], header["organ_count"]
    stage_count = len(header["stage_times"])
    views = gltf["bufferViews"]

    def view_bytes(index: int, expected: int, label: str) -> bytes:
        if not 0 <= index < len(views):
            raise VatError(f"{label}: bufferView {index} does not exist")
        view = views[index]
        if view["byteLength"] != expected:
            raise VatError(f"{label}: bufferView {index} is {view['byteLength']} bytes, header implies {expected}")
        offset = view.get("byteOffset", 0)
        if offset % 4 or offset + expected > len(binary):
            raise VatError(f"{label}: bufferView {index} (offset {offset}, {expected} bytes) is misaligned or "
                           f"outside the {len(binary)}-byte BIN chunk")
        return binary[offset:offset + expected]

    blocks = []
    for entry in header["blocks"]:
        block = Block(entry["name"], entry["domain"], entry["timing"], entry["components"], entry["storage"], ())
        _validate_block_shape(block)
        count = _element_count(block.domain, vertex_count, organ_count)
        n = count * block.components * (stage_count if block.timing == "stage" else 1)
        raw = view_bytes(entry["bufferView"], _byte_length(block.storage, n), f"block '{block.name}'")
        blocks.append(Block(block.name, block.domain, block.timing, block.components, block.storage,
                            _decode_values(block.storage, raw)))
    accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["indices"]]
    triangles = _decode_values("u32", view_bytes(accessor["bufferView"], accessor["count"] * 4, "indices"))
    plant = VatPlant(header["species"], header["species_params"], tuple(header["organ_ids"]),
                     tuple(header["stage_times"]), triangles, header["preview_stage"], tuple(blocks))
    if _validate_plant(plant) != vertex_count:
        raise VatError(f"header vertex_count {vertex_count} disagrees with block 'organ'")
    if organ_count != len(plant.organ_ids):
        raise VatError(f"header organ_count {organ_count} disagrees with organ_ids ({len(plant.organ_ids)})")
    return plant

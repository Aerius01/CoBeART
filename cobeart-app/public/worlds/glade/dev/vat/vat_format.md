# VAT format v1 (`cobeart-vat`)

A species-neutral vertex animation file for one plant over its whole lifetime: one fixed index buffer, per-stage vertex data, per-organ data and static data, all in one file. A species adapter (grass, a flower, ...) maps its own geometry onto named blocks; the writer (`vat_writer.py`) and the loader (`vat.mjs`) know nothing about any species.

Files: `vat_writer.py` (stdlib only, also runs inside Blender's Python), `vat.mjs` (ES module, no dependencies), `make_fixture.py` and `fixtures/tiny.vat.glb` (a 2-organ, 3-stage example using every storage type).

## Container

A glTF 2.0 binary (GLB), little-endian: 12-byte header, a JSON chunk, then a BIN chunk. The JSON chunk is padded with spaces and the BIN chunk with zero bytes to 4 bytes. Every buffer view starts on a 4-byte boundary. Any glTF viewer opens the file and shows the preview stage.

The glTF has one buffer (the BIN chunk), one node, one mesh with one primitive (triangles) and no materials:

- `POSITION`: f32 vec3, the positions of `preview_stage` (decoded from the `position` block), with `min` and `max`.
- `NORMAL`: f32 vec3, the `normal` block (vertex domain, 3 components, any storage) at the preview stage. Present only when that block exists.
- `TEXCOORD_0`: f32 vec2, the `uv` block (vertex, static, 2 components). Present only when that block exists.
- `indices`: u32 triangle list.

The VAT description is `extras.vat` at the glTF top level. The stage blocks live in the same BIN chunk, each in its own buffer view.

## `extras.vat`

| Field | Meaning |
| --- | --- |
| `format` | `"cobeart-vat"` |
| `version` | `1` |
| `species` | string, e.g. `"grass"` |
| `species_params` | free JSON object (model parameters, seed, ...), never interpreted by the writer or loader |
| `vertex_count` | number of vertices |
| `organ_count` | number of organs, equals `organ_ids.length` |
| `organ_ids` | strings, in organ index order |
| `stage_times` | model time of each stage, strictly increasing (the species defines the unit, phyllochrons for grass) |
| `preview_stage` | stage index shown by the glTF preview mesh |
| `never` | `1.0e30`: the stored value of an event that does not happen (finite, since GLSL infinities are unreliable) |
| `blocks` | list of `{name, domain, timing, components, storage, bufferView}`; names are unique |

## Blocks

- `domain`: `"vertex"` (one element per vertex, `count = vertex_count`) or `"organ"` (one per organ, `count = organ_count`).
- `timing`: `"static"` (one run of `count * components` values) or `"stage"` (`stage_count` such runs, stage-major: `[stage][element][component]`).
- `components`: 1 to 4.
- `storage`:

| Storage | Stored as | Decoded as |
| --- | --- | --- |
| `f32` | float32 | the same |
| `f16` | float16 (IEEE 754 binary16, round to nearest even); loaders hand out the `Uint16Array` bits | float, relative error up to 2^-11 |
| `u32` | uint32 | the same integers |
| `oct16` | two int16 snorm per vector (4 bytes per element), `components` must be 3 | unit xyz |

`oct16` is the standard octahedral mapping with sign-not-zero: project `v / (|x| + |y| + |z|)` onto the xy plane, fold the lower hemisphere (`z < 0`) over with `(1 - |y|) * sign(x), (1 - |x|) * sign(y)` where `sign(0) = +1`, and scale by 32767 with rounding. Decoding divides by 32767, rebuilds `z = 1 - |x| - |y|`, unfolds when `z < 0` and renormalises. The writer normalises its input vectors; the angular error is below 0.01 degrees.

A block's buffer view has exactly `values * bytes` bytes (`oct16`: 4 bytes per element), with no padding inside the view. Padding sits between views.

### Required blocks

- `position`: vertex, stage, 3 components, `f32` or `f16`.
- `organ`: vertex, static, 1 component, `u32`: the organ index of each vertex.

Everything else is species-defined, for example `normal` (vertex, stage, 3, `oct16`), `uv` (vertex, static, 2, `f32`), per-organ birth and event times (organ, static) and per-organ shape parameters per stage (organ, stage).

## Validation (writer and loader)

Counts and byte lengths agree with the header; stage times are strictly increasing and `preview_stage` is in range; block names are unique; the required blocks have the shapes above; every `f32` and `f16` value is finite and every `f16` value is within +-65504 (the writer rejects larger ones, use `f32` for those); `u32` values are integers in range; `oct16` input vectors are non-zero; organ indices are below `organ_count`; the triangle list is a non-empty multiple of 3 with every index below `vertex_count`. Errors name the block and, where one applies, the stage and element.

## Writer and loader

```python
plant = VatPlant(species, species_params, organ_ids, stage_times, triangles, preview_stage, blocks)
data = encode_vat(plant)          # deterministic: the same input gives the same bytes
write_vat(plant, "plant.vat.glb")
decoded = decode_vat(data)        # values decoded to floats (oct16 to unit xyz) and ints (u32)
```

```js
import { loadVat, decodeBlock } from './vat.mjs';
const vat = await loadVat('plant.vat.glb');       // or parseVat(arrayBuffer)
const block = vat.blocks.get('position');         // {name, domain, timing, components, storage, raw}
const floats = decodeBlock(block);                // raw is the stored typed array, ready for a GPU upload
```

`raw` is `Float32Array` (`f32`), `Uint16Array` (`f16` bits), `Int16Array` (`oct16` pairs) or `Uint32Array` (`u32`), a view onto the file buffer. For a stage block, stage `k` starts at `k * count * components` values (`k * count * 2` int16 for `oct16`).

## Example header

The fixture's `extras.vat`, abridged:

```json
{
  "format": "cobeart-vat",
  "version": 1,
  "species": "tiny",
  "species_params": {"seed": 1, "note": "synthetic loader fixture"},
  "vertex_count": 12,
  "organ_count": 2,
  "organ_ids": ["1", "2"],
  "stage_times": [0.0, 1.5, 3.0],
  "preview_stage": 2,
  "never": 1e+30,
  "blocks": [
    {"name": "position", "domain": "vertex", "timing": "stage", "components": 3, "storage": "f16", "bufferView": 4},
    {"name": "normal", "domain": "vertex", "timing": "stage", "components": 3, "storage": "oct16", "bufferView": 5},
    {"name": "organ", "domain": "vertex", "timing": "static", "components": 1, "storage": "u32", "bufferView": 6},
    {"name": "events", "domain": "organ", "timing": "static", "components": 4, "storage": "f32", "bufferView": 10},
    {"name": "collar", "domain": "organ", "timing": "stage", "components": 3, "storage": "f16", "bufferView": 14}
  ]
}
```

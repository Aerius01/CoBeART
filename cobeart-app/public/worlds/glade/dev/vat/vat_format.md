# VAT format v1 (`cobeart-vat`)

A species-neutral vertex animation file for one plant over its whole lifetime: one fixed index buffer, per-stage vertex data, per-organ data and static data, all in one file. A species adapter (grass, a flower, ...) maps its own geometry onto named blocks; the writer (`vat_writer.py`) and the loader (`vat.mjs`) know nothing about any species.

Files: `vat_writer.py` (stdlib only, also runs inside Blender's Python), `vat.mjs` (ES module, no dependencies), `make_fixture.py` and `fixtures/tiny.vat.glb` (a 2-organ, 3-stage example using every storage type).

An **organ** is a drawable unit with its own lifecycle and per-organ data (a grass leaf, a petal, a stem segment). Vertices belong to exactly one organ.

## Container

A glTF 2.0 binary (GLB), little-endian: 12-byte header, a JSON chunk, then a BIN chunk. The JSON chunk is padded with spaces and the BIN chunk with zero bytes to 4 bytes. Every buffer view starts on a 4-byte boundary. Any glTF viewer opens the file and shows the preview stage.

The glTF has one buffer (the BIN chunk), one node, one mesh with one primitive (triangles) and one material, `{"name": "vat_preview", "doubleSided": true}`, which the primitive references:

- The node carries `rotation: [-0.70710678, 0, 0, 0.70710678]` when `frame.up` is `"+Z"` (so a glTF viewer shows the plant upright) and no rotation for `"+Y"`. This is preview only: all blocks stay in the species frame.

- `POSITION`: f32 vec3, the positions of `preview_stage` (decoded from the `position` block), with `min` and `max`. When a `lifespan` block exists, every vertex of an organ that is not alive at `stage_times[preview_stage]` (alive means `appear <= t < vanish`) is moved onto that organ's first vertex (its lowest vertex index), and `min` and `max` cover the result. Stage blocks are not changed.
- `NORMAL`: f32 vec3, the `normal` block (vertex domain, 3 components, any storage) at the preview stage, normalised to unit length. Present only when that block exists.
- `TEXCOORD_0`: f32 vec2, the `uv` block (vertex, static, 2 components). Present only when that block exists.
- `indices`: u32 triangle list.

The VAT description is `extras.vat` at the glTF top level. The stage blocks live in the same BIN chunk, each in its own buffer view.

## `extras.vat`

| Field | Meaning |
| --- | --- |
| `format` | `"cobeart-vat"` |
| `version` | `1` |
| `species` | string, e.g. `"grass"` |
| `species_version` | integer version of the species block set, checked by the species consumer, never by the loader |
| `frame` | `{"up": "+Z" or "+Y", "unit": string}`: the frame all blocks are in (grass: `+Z`, `m`) |
| `species_params` | free JSON object (model parameters, seed, ...), never interpreted by the writer or loader |
| `vertex_count` | number of vertices |
| `organ_count` | number of organs, equals `organ_ids.length` |
| `organ_ids` | strings, in organ index order |
| `stage_times` | model time of each stage, strictly increasing (the species defines the unit, phyllochrons for grass) |
| `preview_stage` | stage index shown by the glTF preview mesh |
| `never` | the float32 value nearest 1.0e30 (`1.0000000150474662e30`, exactly representable in f32): the stored value of an event that does not happen (finite, since GLSL infinities are unreliable) |
| `blocks` | list of `{name, domain, timing, components, storage, bufferView, meta?}`; names are unique |

## Blocks

- `domain`: `"vertex"` (one element per vertex, `count = vertex_count`) or `"organ"` (one per organ, `count = organ_count`).
- `timing`: `"static"` (one run of `count * components` values) or `"stage"` (`stage_count` such runs, stage-major: `[stage][element][component]`).
- `components`: 1 to 4.
- `meta` (optional): free object describing the values. `meta.components` is a list of one name per component (length `components`), `meta.unit` a string; other keys are ignored.
- `storage`:

| Storage | Stored as | Decoded as |
| --- | --- | --- |
| `f32` | float32 | the same |
| `f16` | float16 (IEEE 754 binary16, round to nearest even); loaders hand out the `Uint16Array` bits | float, relative error up to 2^-11 |
| `u32` | uint32 | the same integers |
| `oct16` | two int16 snorm per vector (4 bytes per element), `components` must be 3 | unit xyz |

`oct16` is the standard octahedral mapping with sign-not-zero: project `v / (|x| + |y| + |z|)` onto the xy plane, fold the lower hemisphere (`z < 0`) over with `(1 - |y|) * sign(x), (1 - |x|) * sign(y)` where `sign(0) = +1`, and scale by 32767 with rounding. Decoding divides by 32767, rebuilds `z = 1 - |x| - |y|`, unfolds when `z < 0` and renormalises. The writer normalises its input vectors; the angular error is below 0.01 degrees.

A block's buffer view has exactly `values * bytes` bytes (`oct16`: 4 bytes per element), with no padding inside the view. Padding sits between views.

A block that can hold `never` must be `f32` (`f16` cannot represent it and `u32` is an integer type).

### Required blocks

- `position`: vertex, stage, 3 components, `f32` or `f16`.
- `organ`: vertex, static, 1 component, `u32`: the organ index of each vertex.

### Reserved and conventional blocks

- `lifespan` (optional, reserved): organ, static, 2 components, `f32`: `appear`, `vanish` in `stage_times` units, `never` allowed. An organ is alive at time `t` when `appear <= t < vanish`. Its only effect in the format is the preview collapse above.
- `parent` (convention, not interpreted by the writer or loader): organ, static, 1 component, `u32`: the parent organ index, or `organ_count` for a root organ.

Everything else is species-defined, for example `normal` (vertex, stage, 3, `oct16`), `uv` (vertex, static, 2, `f32`), per-organ birth and event times (organ, static) and per-organ shape parameters per stage (organ, stage).

## Forward compatibility

Readers ignore unknown `extras.vat` fields and unknown blocks (an unknown block is still parsed into the block map). Additive changes therefore keep `version` 1; a change that alters the meaning of existing data bumps it.

## Validation (writer and loader)

Counts and byte lengths agree with the header; stage times are strictly increasing and `preview_stage` is in range; block names are unique; the required blocks have the shapes above; every `f32` and `f16` value is finite and every `f16` value is within +-65504 (the writer rejects larger ones, use `f32` for those); `u32` values are integers in range; `oct16` input vectors are non-zero; organ indices are below `organ_count`; the triangle list is a non-empty multiple of 3 with every index below `vertex_count`. Errors name the block and, where one applies, the stage and element. `meta.components` must be a list of `components` strings and `meta.unit` a string; `frame.up` is `+Z` or `+Y`; `species_version` and the counts are integers; `lifespan` has the shape above. A missing or mistyped header field raises an error naming `extras.vat.<field>`; `organ_count` must equal `organ_ids.length`, the indices accessor must be u32 (5125) and the JSON chunk length a multiple of 4. Non-numbers and booleans in float blocks are rejected.

## Writer and loader

```python
plant = VatPlant(species, species_params, organ_ids, stage_times, triangles, preview_stage, blocks,
                 Frame("+Z", "m"), species_version)
data = encode_vat(plant)          # deterministic: the same input gives the same bytes
write_vat(plant, "plant.vat.glb")
decoded = decode_vat(data)        # values decoded to floats (oct16 to unit xyz) and ints (u32)
```

```js
import { loadVat, decodeBlock } from './vat.mjs';
const vat = await loadVat('plant.vat.glb');       // or parseVat(arrayBuffer)
const block = vat.blocks.get('position');         // {name, domain, timing, components, storage, raw, meta}
vat.frame; vat.speciesVersion;                    // {up, unit} and the species consumer's version check
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
  "species_version": 1,
  "frame": {"up": "+Z", "unit": "m"},
  "species_params": {"seed": 1, "note": "synthetic loader fixture"},
  "vertex_count": 12,
  "organ_count": 2,
  "organ_ids": ["1", "2"],
  "stage_times": [0.0, 1.5, 3.0],
  "preview_stage": 2,
  "never": 1.0000000150474662e+30,
  "blocks": [
    {"name": "position", "domain": "vertex", "timing": "stage", "components": 3, "storage": "f16", "bufferView": 4,
     "meta": {"components": ["x", "y", "z"], "unit": "m"}},
    {"name": "normal", "domain": "vertex", "timing": "stage", "components": 3, "storage": "oct16", "bufferView": 5},
    {"name": "organ", "domain": "vertex", "timing": "static", "components": 1, "storage": "u32", "bufferView": 6},
    {"name": "events", "domain": "organ", "timing": "static", "components": 4, "storage": "f32", "bufferView": 10,
     "meta": {"components": ["senescent_time", "senescence_duration", "death_time", "collapse_time"], "unit": "P"}},
    {"name": "collar", "domain": "organ", "timing": "stage", "components": 3, "storage": "f16", "bufferView": 15}
  ]
}
```

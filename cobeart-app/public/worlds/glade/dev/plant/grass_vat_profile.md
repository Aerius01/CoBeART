# Grass VAT profile (species `grass`, version 1)

This profile describes the blocks a grass bake writes into a `cobeart-vat` file. The container, block layout, storage types, `never` and the `lifespan` preview rule are in [`../vat/vat_format.md`](../vat/vat_format.md). It is written by `bake_grass_vat.py` (GP roadmap, Phase 4). A new species adapter, for example a flower, writes a profile like this one for its own blocks.

- `species` `"grass"`, `species_version` `1`. A consumer rejects any other version.
- `frame` `{"up": "+Z", "unit": "m"}`. Every block is in metres with Z up. Times (`stage_times`, events) are model time in phyllochrons (`P`).
- One organ per lifetime leaf, in `(birth_time, id)` order (`organ_ids`, for example `L0.3`). Each leaf is one grid: the sheath rows from the tiller base up, then the blade rows from the emergence row to the tip.
- Stages: every sample of the organ table, in order. `preview_stage` is the last stage (`t_end`).

## Blocks

Units: `m` metres, `rad` radians, `P` phyllochrons, `1` unitless, `index` an integer index or enum. `never` marks an event that does not happen by `t_end`.

| Block | Domain, timing | Components | Storage | Unit | Meaning |
| --- | --- | --- | --- | --- | --- |
| `position` | vertex, stage | x, y, z | f16 | m | healthy position (decline is applied in the shader) |
| `normal` | vertex, stage | x, y, z | oct16 | 1 | area-weighted vertex normal on the underside; +Z where a vertex has no face area |
| `organ` | vertex, static | organ | u32 | index | the leaf's organ index |
| `kind` | vertex, static | kind | u32 | index | 0 sheath, 1 blade |
| `s` | vertex, static | s | f32 | 1 | fraction along the organ: sheath row height fraction, blade row free-blade fraction |
| `uv` | vertex, static | u, v | f32 | 1 | u across the leaf, v along the whole leaf |
| `lifespan` | organ, static | appear, vanish | f32 | P | birth time; collapse time or `never` |
| `senescence` | organ, static | senescent_time, senescence_duration, death_time | f32 | P | G38 event times, `never` where null |
| `draw` | organ, static | draw | f32 | 1 | the leaf's decline draw |
| `phase` | organ, static | phase | f32 | 1 | the leaf's wind phase in [0, 1) |
| `tiller` | organ, static | tiller | u32 | index | tiller index, 0 the main shoot |
| `tiller_az` | organ, static | tiller_az | f32 | rad | tiller azimuth |
| `tiller_base` | organ, static | x, y, z | f32 | m | tiller base point |
| `tiller_axis` | organ, static | x, y, z | f32 | 1 | unit tiller axis |
| `side` | organ, static | x, y, z | f32 | 1 | the leaf's arch axis (normal of its leaf plane) |
| `collar` | organ, stage | x, y, z | f32 | m | blade midrib start at the emergence row, on the wrap wall |
| `direction` | organ, stage | x, y, z | f32 | 1 | the healthy blade direction |
| `arch` | organ, stage | arch | f32 | rad | the healthy arch |
| `sheath_length` | organ, stage | sheath_length | f32 | m | own collar height along the tiller axis |
| `emergence` | organ, stage | emergence | f32 | m | emergence height along the tiller axis |

## Rebuild rules

At model time `t`, with `FADE_DURATION` and `SHEATH_PLATEAU_M` from `species_params.params`:

- **Visibility:** hide the organ for `t < appear` or `t >= vanish`. At `t = appear` the leaf has zero size and is still degenerate (zero-area triangles at its first-seen attach).
- **Age:** `t - appear`.
- **Senescence and fade (G38):** `sen = clamp((t - senescent_time) / senescence_duration, 0, 1)` and `fade = clamp((t - death_time) / FADE_DURATION, 0, 1)`. A `never` event time gives 0.
- **Pivot and axis by `kind`:**
  - blade: pivot is `collar` and axis is `direction`;
  - sheath: pivot is `tiller_base` and axis is `tiller_axis`.
- **`stem_t`:** blade `emergence / SHEATH_PLATEAU_M`; sheath `sheath_length * s / SHEATH_PLATEAU_M`.
- **Normals:** they point to the underside, outward on the sheath and the collar wrap and down on an arched blade at rest. The Python decline rule (`../lpy/decline.py`, `declined_vertex`) does not transform them. The shader turns them by the decline row rotation and the tiller lean.
- **Blending stages:** stage blocks hold the values at each stage time. A consumer blends the two stages around `t`.
- **Carry-forward (G39):** from its collapse on, an organ keeps the positions, normals and organ-stage values of its last visible stage, so a blend never moves a dead blade to its anchor. Hide it with the visibility rule. Unborn organs are degenerate at their first-seen attach.

## `species_params`

- `model`: the L-Py model file name (`grass.lpy`).
- `seed`: the export's seed.
- `params`: every extern of the model. It includes `FADE_DURATION`, `SHEATH_PLATEAU_M`, `ELONGATION_AGE` and the decline parameters that `decline.DeclineParams.from_mapping(params)` reads (`V_DEAD`, `DECLINE_*`, `SHRIVEL_*`, `ANGLE_SENESCENT_DEG`, `DECLINE_SPLAY_DEG`).
- `geometry`: the `GeometryParams` used for the bake. `geometry.collar_bend` is the `collar_bend` argument of `declined_vertex` (a shader uniform).

## Canonical bake

```bash
cd cobeart-app/public/worlds/glade/dev
~/miniconda3/envs/lpy/bin/python lpy/export_organs.py lpy/grass.lpy OUT/grass.json --samples 49 [--seed N]
~/miniconda3/envs/lpy/bin/python plant/bake_grass_vat.py OUT/grass.json OUT/grass.vat.glb
```

Nothing generated is committed.

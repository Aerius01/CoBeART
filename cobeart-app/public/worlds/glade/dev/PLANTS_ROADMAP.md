# Glade plants roadmap (GP)

Load the `orchestrate` skill before any implementation work. The user names the phase to run.

**Roadmap ID:** `GP`. Scope: the growth chain for Glade's first 3D plant, a bushy vegetative grass (no heading, no flowering), from an L-Py model to a growth slider on a dev page. Design input: the grill of 2026-11-01 (decisions P1 to P8 below) and the reference plant in `dev/assets/build_grass_plant.py`. The wind MVP (`dev/wind-demo.html`, `dev/wind/`) is a sibling prototype and not part of this roadmap; Phase 5 reuses its renderer.

**Status (2026-11-22):** Phases 0 to 4 done and merged into `main`. Phase 5 is next; its base is the tip of `main` after the Phase 4 outcome commit.

## Project facts

- **Worktree base:** `.claude/settings.local.json` sets `worktree.baseRef: "head"` (verified 2026-11-01).
- **L-Py environment:** a dedicated conda env `lpy`, never `splat-env`. Create it with `conda create -n lpy openalea.lpy pytest jsonschema -c openalea3 -c conda-forge` (created 2026-11-02; `jsonschema` 4.26 is needed by the exporter). A dry run on 2026-11-01 resolved `openalea.lpy` 3.15.4, `openalea.plantgl` 3.23.2 and Python 3.13 with the Qt viewer. Phase 1 tests: `timeout 120 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests`. Call the interpreter directly (`~/miniconda3/envs/lpy/bin/python`), never `conda run`. The GUI is `~/miniconda3/envs/lpy/bin/lpy`.
- **Blender:** Phase 3 runs Python inside Blender (Phase 4 does not need it) (headless: `blender --background --python <script> -- <args>`), the way `build_grass_plant.py` does. Blender's bundled Python has no access to the `lpy` env, so the two sides meet only through files.
- **Ports:** `3000 + 100 * phase + wp`; 3000 is the orchestrator's. Only Phase 5 serves pages.
- **Tests outside CI:** the root `pytest` collects only `cobeart` and `tests` (`pyproject.toml`), and CI has no `lpy` env, so this roadmap's Python tests run locally with the `lpy` interpreter. Each phase's gate lists them.
- **Files no WP edits:** this file, `../BRIEF.md`, `../ROADMAP.md`, `CLAUDE.md`.
- **Gate commands:** each WP's Must-pass list, plus `~/miniconda3/envs/splat-env/bin/python -m flake8 --max-line-length 120 <the phase's Python files>` and, for phases that touch `public/`, `node scripts/fitness.mjs`. Cap every run with `timeout`.

### Constraints every agent follows

- **Fitness rule `worlds-receive-only`** scans everything under `public/worlds/`, including `.md` and `.py`: no socket client calls, socket library names or hub namespace paths.
- **Units and axes:** metres, Z up, matching `grass_plant.glb`. Time is in phyllochrons (the interval between two successive leaves on one shoot), so all rates are relative and the model has no calendar.
- **Determinism:** every random draw comes from a seeded generator; the seed is a parameter and is written into every export.
- **Parameters:** model parameters live in one place per side (the `.lpy` file's parameter block on the L-Py side, a frozen dataclass on the geometry side), never as literals scattered through rules or mesh code.
- **Code style:** the global and project CLAUDE.md rules (strict typing, pure functions, errors naming the bad value and the fix, no em-dashes in prose).

## Background: how vegetative grass grows

Settled by research on 2026-11-01; the model follows this.

- **Phytomer:** the repeating unit is node, internode, leaf sheath, leaf blade and axillary bud.
- **Pseudostem:** in a vegetative grass the internodes stay unelongated and the growing point stays at the crown. The visible "stem" is the leaf sheaths rolled inside each other. Each new leaf grows up inside the previous leaf's sheath, emerges rolled, unrolls as it lengthens, and is mature when its collar (blade to sheath junction) appears. Later sheaths are longer, so later leaves emerge higher.
- **Phyllochron:** leaves appear one at a time at a steady interval.
- **Tillering:** a tiller grows from the axillary bud of a leaf, usually inside that leaf's sheath (intravaginal), so tillers rise nearly parallel to the parent and splay slightly outward. First tillers appear at about Haun stage 2.5 to 4 (around the third main-shoot leaf). Each tiller follows the parent's rules.
- **Leaf turnover:** a tiller keeps a near-constant number of live leaves (about 3 in ryegrass, under 5 in grasses generally). When a new leaf appears beyond that number, the oldest senesces and dies.

Sources: [USDA ARS, phyllochron in grasses](https://digitalcommons.unl.edu/usdaarsfacpub/85), [NDSU, wheat development and Haun stages](https://workspaces.ndsu.edu/fileadmin/soils.del/pdfs/Goos-wheat-development.pdf), [AusGrass, grass structure](https://ausgrass2.myspecies.info/node/2844), [FarmWest, how grasses grow](https://farmwest.com/?p=2467), [DairyNZ technote 9](https://www.dairynz.co.nz/media/lezho5cz/technote-9_wb.pdf).

## Reference plant

`dev/assets/build_grass_plant.py` (with `grass_plant.blend` and `grass_plant.glb`) is the approved look for one mature shoot. Its `PlantParams` and profile functions are the source of truth for the blade cross-section curl (arc `0.35 + 2.6 * (1 - s)^1.5` rad), the blade outline (widest at about a third of the length), the arching, the sheath wrapping, the rolled top leaf that unrolls over its first 30%, and the per-vertex wind attributes (`_PIVOT`, `_AXIS`, `_S`, `_STEM_T`, `_ORGAN`, `_PHASE`, documented in its docstring). The growth chain keeps that look; what changes is the structure (P1, P2): a clump of tillers on a pseudostem instead of one shoot with real internodes.

## Decisions (grill of 2026-11-01)

- **P1 Tillering:** first-order tillers only (tillers off the main shoot, none off tillers). Second-order tillers are a later parameter change if the clump looks sparse.
- **P2 Stem model:** pseudostem. Internodes have about zero length, each leaf's sheath wraps the sheaths inside it, and a leaf's emergence height is its sheath length, which grows with leaf rank. The reference's irregular node heights become irregular sheath lengths. Wind pivots sit at the sheath tops.
- **P3 Lifecycle:** seedling to mature clump, then leaf turnover, then whole-plant decline (no new leaves, everything senesces and collapses), built in that order. Decline gives Glade's declining set and replaces the wilt colour jump `BRIEF.md` lists as a known limitation.
- **P4 Time model:** continuous. Each derivation step advances time by `dt` (a fraction of a phyllochron); modules carry their age and growth functions map age to size and shape. The unrolling top leaf of the reference is not a special organ: it is every leaf at a young age.
- **P5 L-Py's job:** structure and timing only. L-Py decides which organs exist, where they attach, and their age, size, angles and state; it outputs an organ table. Our Python generator (refactored from `build_grass_plant.py`) builds the meshes. The L-Py viewer shows a skeleton preview for tuning.
- **P6 Rendering continuous growth:** fixed-topology keyframes. The mesh holds every organ the plant ever has, at zero size before birth and after death, so all stages share one vertex order. Sample N stages (about 32), export per-stage positions and wind pivots and axes as a vertex animation texture (VAT), and blend the two stages around the plant's growth value `g` in the vertex shader. Estimated cost: about 50 lifetime leaves times 150 vertices times 32 stages, about 3 MB per plant variant, shared by its instances. **Alternative kept for later:** an analytic shader that rebuilds each blade from an exported organ table using the generator's formulas. It is truly continuous and small, but duplicates the generator in GLSL. Revisit only if the Phase 5 measurement shows VAT memory or dead-organ vertex work is the limit.
- **P7 Clump size:** 3 live leaves per tiller, main shoot plus 4 tillers (about 15 live leaves). Both are parameters.
- **P8 Staging:** the chain below, one MVP per phase; Phase 1 settles the L-Py side and is judged in the viewer before any geometry work.

## Phase 0: Commit the roadmap (no WPs; runs at the start of the Phase 1 session)

This file is untracked until committed, and untracked files do not exist in worktrees.

1. The reference plant and the wind MVP were fast-forwarded into `main` on 2026-11-01 (`2fa5d1c`, pushed).
2. Create `gp-1-lpy-model` from `main` and commit this file as `[GP 0] Add the Glade plants roadmap.`
3. That commit is the Phase 1 base; record its id in the Status line.

## Phase 1: L-Py growth model and organ-table export (2 parallel WPs)

Builds the grass L-system through seedling growth, tillering and leaf turnover (P3 steps one and two), and the exporter that samples it into organ tables. Decline is Phase 2. The orchestrator creates the `lpy` env (Project facts) before spawning; agents never create or modify conda envs.

The two WPs meet at the **module interface** and the **organ-table format** below, settled in the grill before spawning: WP 1.2 is written against them while WP 1.1 is still being built.

**Verified L-Py API (2026-11-02, `openalea.lpy` in env `lpy`):**
- `Lsystem(path, {"NAME": value})` overrides only names declared with `extern(NAME=default)` in the `.lpy`; plain globals ignore the dict. `l.context().globals()["__externs__"]` is the set of extern names, and `globals()[name]` their values.
- `derivation length:` accepts an expression of externs, e.g. `int(round(T_END / DT))`.
- `l.derive(tree, start_step, n_steps)` returns an `AxialTree`; iterating it gives modules with `.name` and positional parameters (`m[0]`, `list(m)`), which may be Python strings and dicts.
- Same seed, same L-string (checked with `str(tree)`).

**Module interface (settled, G1):** L-Py computes every organ-table field each step and stores it in the module; the exporter only copies values and never reads turtle frames.
- `Apex(tiller, rank, t_next)`: a tiller's growing point; emits a new phytomer each phyllochron.
- `Leaf(id, tiller, rank, birth_time, age, state, o)`: one leaf with its sheath. `state` is `"growing" | "mature" | "senescent" | "dead"`. `o` is a dict whose keys are exactly the leaf fields of the organ table below, recomputed every step.
- `Tiller(tiller, parent_rank, birth_time, o)`: one per tiller including the main shoot (`tiller` 0, `parent_rank` 0); `o` holds the tiller fields below.
- `Bud(tiller, rank, age)`: an axillary bud on the main shoot; becomes `Tiller` plus `Apex` per the tillering rule.
- Leaf ids are `"L<tiller>.<rank>"`, tiller ids `"T<tiller>"`; both are fixed at birth.
- All tunables are `extern(...)` in one block at the top, including `SEED`, `DT`, `T_END`. One `random.Random(SEED)` per model; each organ's random draws are made once at its birth, so the draw order is fixed. Birth draws live in module globals reset by `Start()`; all state that changes over time (age, state, senescence) lives in the modules. One `Lsystem` instance is derived forward only.

**Organ-table format (settled, G2):** one JSON file per export.
- Header: `schema_version` (1), `model` (file name), `seed`, `params` (every extern name to value), `dt`, `t_end`, `sample_times`.
- `samples`: a list of `{time, organs}`. An organ appears from its birth onward and stays in every later sample with the same `id`; the lifetime organ list is the union of all samples. Organs within a sample are sorted by `(birth_time, id)`; consumers key them by `id`.
- Every organ: `id`, `kind` (`leaf` or `tiller`), `tiller`, `rank` (for a tiller its `parent_rank`), `birth_time`, `age`, `state` (tillers are always `growing` in Phase 1).
- Leaf fields in `o` (written flat on the organ object): `attach` [x, y, z] (the collar: tiller base plus the tiller axis times the current sheath length), `direction` [x, y, z] (unit, initial blade direction in world space, authoritative), `azimuth_rad` and `angle_from_axis_rad` (spherical angles of `direction` in the tiller's frame: pole is the tiller axis, frame is the minimal rotation of +Z onto it), `sheath_length_m`, `blade_length_m`, `blade_width_m`, `unroll` (0 rolled, 1 open), `arch_rad`, `senescence` (0 green, 1 dead).
- Tiller fields in `o`: `base` [x, y, z], `direction` [x, y, z] (unit tiller axis), `azimuth_rad`, `splay_rad`.
- A dead leaf has zero `sheath_length_m`, `blade_length_m` and `blade_width_m`, `senescence` 1, and `attach` at its tiller base.

**Grill outcomes (2026-11-02):**
- **G1, G2:** the interface and format above. Attachment points come from parameters (P2 puts every node at the crown), so no turtle frames are read.
- **G3 Growth functions:** sheath and blade lengths and blade width follow `smoothstep(age / 2)` to full size; the leaf turns `mature` at age 2 (collar). `unroll = smoothstep((age - 0.6) / 1.2)`. A leaf is live while `growing` or `mature`; when a new leaf would make a tiller exceed `LIVE_LEAVES` (3), its oldest live leaf turns `senescent`. Senescence takes 2 P (`senescence` 0 to 1), then `dead`. Over the last 30% of senescence the leaf shrivels and droops: all sizes scale to zero, arch and angle from vertical grow. The user may later prefer a shrivelled blade that persists (more vertices, no pop at all); that is a parameter-level change for a later phase.
- **G4 Rank progressions:** sheath 2 cm at rank 1 rising linearly to 8 cm at rank 5, flat after, ±15% jitter; blade length 6 to 20 cm, ±10%; blade width 4 to 13 mm, ±8%. Angle from vertical follows age, not rank: about 8 deg young and rolled, 20 deg mature, easing toward 48 deg as it ages, more while senescent; ±5 deg jitter per leaf. Arch 0.4 rad rising to 0.9 rad with age. Azimuth alternates by pi within a tiller, ±0.45 rad jitter. Tillers use the same progressions from their own rank 1, scaled by `TILLER_SIZE_SCALE` 0.9.
- **G5 Tillering:** the bud in the axil of main-shoot leaf n becomes a tiller when main-shoot leaf n + 3 appears (`TILLER_DELAY` 3). Axils of main-shoot leaves 1 to 4 can tiller (`MAX_TILLERS` 4), so tillers emerge at t of about 3, 4, 5, 6. A tiller's base sits on the crown `CROWN_RADIUS_M` 4 mm from the centre at the parent leaf's azimuth, splayed outward 12 deg ± 4 deg. Tillers keep the main shoot's phyllochron, first leaf at emergence; buds on tillers never activate (P1).
- **G6 Time span:** `DT` 0.05, `T_END` 12 (the last tiller has 3 live leaves by about t = 8, then about 4 P of steady turnover). Exporter default `--samples 25`: evenly spaced over [0, T_END], snapped to the step grid. VAT stage spacing stays a Phase 4 decision.
- **G7 Preview and layout:** the viewer draws sheaths as cylinders and each blade as an 8-segment curved strip following its arch, tapering in width and narrower while rolled, coloured green to brown by `senescence` through the `.lpy` material list. Files live in `dev/lpy/` (`grass.lpy`, `export_organs.py`, `organ_table.schema.json`, `tests/`). Generated JSON is not committed in Phase 1.
- **G8 Tiller azimuth spread (user review, 2026-11-02):** with tillers at their parent leaf's azimuth the clump was a vertical fan (every leaf plane equal to the main shoot's). Real distichous grasses are radial because same-side tiller bases crowd and push apart in azimuth ([Tokuyama et al. 2021](https://academic.oup.com/insilicoplants/article/3/2/diab019/6321938)). A tiller's azimuth is the parent leaf's turned by `TILLER_AZIMUTH_SPREAD_DEG` 50 ± `TILLER_AZIMUTH_SPREAD_JITTER_DEG` 15, the sign alternating between successive tillers on the same side (from `parent_rank`). With seed 1 the tiller leaf planes sit 28 to 39 deg off the main shoot's; raise the spread for a rounder clump.

### WP 1.1: Grass L-system

- **Model:** Opus (the botany and timing are the substance of the phase).
- **Owns:** `cobeart-app/public/worlds/glade/dev/lpy/grass.lpy`.
- **Scope:** the L-system per the module interface, P1 to P4, P7 and the grill outcomes: one parameter block at the top, seeded randomness, continuous time with `dt`, the leaf life cycle with turnover at 3 live leaves, first-order tillering capped at 4, and a skeleton interpretation for the L-Py viewer. Header comment says how to open it in the GUI and what each parameter does.
- **Must pass:**
  - `~/miniconda3/envs/lpy/bin/python <script>` that loads `grass.lpy` with `openalea.lpy.Lsystem`, derives to the end time and prints per tiller the live-leaf count, with every tiller at no more than 3 live leaves and exactly 5 tillers (main plus 4) at the end.
  - The same derivation twice with the same seed gives identical L-strings; a different seed gives a different one.
  - The user steps it in the L-Py GUI and judges the progression (orchestrator hands over with exact steps).

### WP 1.2: Organ-table exporter

- **Model:** Sonnet (pinned down by the module interface and format).
- **Owns:**
  - `cobeart-app/public/worlds/glade/dev/lpy/export_organs.py`
  - `cobeart-app/public/worlds/glade/dev/lpy/organ_table.schema.json`
  - `cobeart-app/public/worlds/glade/dev/lpy/tests/` (including a small fixture `.lpy` that follows the module interface)
- **Scope:** a CLI (`export_organs.py <model.lpy> <out.json> [--samples N] [--seed S]`) that derives the model, samples it at the requested times, converts modules to organs per the format, keeps organ ids stable across samples, and validates the result against the schema before writing. Typed dataclasses for organs; errors name the module and parameter that failed.
- **Must pass:**
  - `timeout 120 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests` passes, covering: schema validity, stable ids across samples, a dead leaf staying in later samples with zero size, and determinism for a fixed seed.
  - After merge, the orchestrator runs it on `grass.lpy` and the output validates.

## Phase 2: Whole-plant decline (2 parallel WPs)

Base: `86b8c17` (tip of `main` after the Phase 1 merge). Adds P3 step three. The grill turned decline from a section of the timeline into a second axis, so the exported growth track stays healthy and decline is an analytic function applied on top of it, at any growth stage.

**Grill outcomes (2026-11-02):**
- **G10 Decline is a per-organ second axis.** A plant has a growth value (the track time, later `g`) and a **vitality** `v` (1 healthy, 0 gone), independent of each other. Glade later drives both per instance: `v` falls while a plant is starved and rises again on revival; growth simply stops advancing while it declines (the "growth freezes at onset" choice is Glade's, the model does not bake it). Each organ's look is its healthy shape at its age, deformed by its own **health** `h` (1 healthy, 0 dead). This replaces baking decline tracks from several onsets.
- **G11 One look for both causes of death.** `h = min(1 - senescence, h_decline(v))`, where `senescence` is the turnover senescence of the track (unchanged rule, G3). One function maps `h` to the look: colour (browning `1 - h`), extra angle from the tiller axis (`ANGLE_SENESCENT_DEG * (1 - h)` plus `SHRIVEL_ANGLE_DEG * shrivel`), extra arch (`SHRIVEL_ARCH_RAD * shrivel`) and size scale `lerp(1, DECLINE_RESIDUAL, shrivel)`, with `shrivel = smoothstep(1 - h / SHRIVEL_FRACTION)`. A dead organ is a dry blade at `DECLINE_RESIDUAL` (0.5) of its healthy size, not zero.
- **G12 Plant-wide decline with variation.** Every organ starts declining as soon as `v < 1`. Organ k's death point is `v_k = V_DEAD + (1 - V_DEAD) * o_k` with `o_k = clamp(DECLINE_AGE_SPREAD * min(age / DECLINE_AGE_REF, 1) + DECLINE_JITTER * decline_draw, 0, 0.95)` (0.95 is `MAX_DEATH_OFFSET`, a constant of the rule) and `h_decline = clamp((v - v_k) / (1 - v_k), 0, 1)`. `decline_draw` is a per-leaf unit draw in [-1, 1], made at birth, so `DECLINE_JITTER` stays tunable without a re-export (gate review G19). Old leaves brown and fold first, the youngest centre leaf holds out until `V_DEAD`. Defaults: `V_DEAD` 0.15, `DECLINE_AGE_SPREAD` 0.4, `DECLINE_AGE_REF` 3 (about the age of the oldest live leaf), `DECLINE_JITTER` 0.1.
- **G13 Fade and the point of no return.** A dead organ fades out once it is beyond saving. A turnover leaf is beyond saving when it dies: its `fade` rises 0 to 1 over `FADE_DURATION` (0.5 P) after death, in the track; at `fade` 1 it is collapsed (zero sizes, `attach` at its tiller base). In plant decline every organ is dead at `v = V_DEAD`; the plant fade is `clamp((V_DEAD - v) / V_DEAD, 0, 1)`, and Glade must refuse revival once `v <= V_DEAD`. Drawn opacity is `(1 - leaf fade) * (1 - plant fade)`.
- **G14 Collapse is droop plus tiller lean.** Droop is G11. Each tiller other than the main shoot leans outward (its splay grows, about its own azimuth, pivoting at its base) by `DECLINE_SPLAY_DEG` (25) `* smoothstep((1 - v) / (1 - V_DEAD))`; its leaves follow rigidly (a rotation of the tiller and its leaves about `(-sin az, cos az, 0)` through the tiller base). The main shoot stays upright. No leaf fall.
- **G15 Track semantics change (schema v2).** In the exported track a leaf's sizes, `angle_from_axis_rad` and `arch_rad` are the **healthy baseline** at its age (no senescence or shrivel terms); `senescence` and two new fields, `fade` and `decline_draw`, carry the rest, and consumers apply G11 to G14. No tiller state and no `parent_tiller` (Phase 1 open items dropped: tillers do not die in the track).
- **G16 Time span unchanged.** `T_END` 12 and `--samples` 25 stay; the track is growth only.
- **G17 Preview.** New extern `PREVIEW_DECLINE` (4.0 P; 0 turns it off): the derivation runs to `T_END + PREVIEW_DECLINE`; after `T_END` the productions freeze (modules are reproduced unchanged), and the interpretation draws with `v = 1 - (t - T_END) / PREVIEW_DECLINE`. Lower `T_END` in the GUI to preview decline from a younger stage. The exporter already samples only up to `T_END`.
- **G18 One Python home for the rule.** `dev/lpy/decline.py`, stdlib only, pure functions over a frozen `DeclineParams` dataclass whose field names are the extern names; `grass.lpy` imports it after putting its own folder on `sys.path` (L-Py sets `__file__` in the `.lpy` but does not add the model's folder to the import path; corrected in WP 2.1), Phase 3's Blender script can too, and Phase 5's GLSL mirrors it. The decline externs live in `grass.lpy`'s parameter block and reach consumers through the export's `params`.

- **G20 User review of the preview (2026-11-07):**
  - Shrinking was too fast and too strong. A dead organ is now 70% of its healthy size (`DECLINE_RESIDUAL` 0.7), and the shrink, droop and arch run over the whole health range instead of the last 30% (`SHRIVEL_FRACTION` 1.0). Both are parameter changes; judge again in the viewer.
  - Death was too rapid. The decline preview lasts half the growth track (`PREVIEW_DECLINE` 6 for `T_END` 12). In Glade the speed comes from how fast Glade lowers `v`, not from the model. A single leaf already takes as long to die (`SENESCENCE_DURATION` 2 P, plus the 0.5 P fade) as to grow (`ELONGATION_AGE` 2 P).
  - Growth and death were synchronised across shoots: every tiller emerged at a whole-number time and kept the main shoot's phyllochron of 1, so all shoots grew a leaf, and dropped their oldest leaf, in the same step. That looked like a skirt falling away all at once. Desynchronised with three seeded per-organ draws, each from its own stream like `decline_draw`, so the other draws keep their values:
    - a tiller's emergence is delayed by a uniform `[0, TILLER_EMERGENCE_JITTER]` (0.6 P) after its trigger;
    - each tiller's phyllochron is `1 ± TILLER_PHYLLOCHRON_JITTER` (0.15); the main shoot's stays 1, and time is still measured in main-shoot phyllochrons;
    - each leaf's turnover senescence lasts `SENESCENCE_DURATION * (1 ± SENESCENCE_JITTER)` (0.2).

**`decline.py` interface (settled):**
- `DeclineParams` (frozen): `V_DEAD`, `DECLINE_AGE_SPREAD`, `DECLINE_AGE_REF`, `DECLINE_RESIDUAL`, `SHRIVEL_FRACTION`, `ANGLE_SENESCENT_DEG`, `SHRIVEL_ANGLE_DEG`, `SHRIVEL_ARCH_RAD`, `DECLINE_SPLAY_DEG`; `from_mapping(names)` builds it from a dict of externs (or export `params`), raising `ValueError` naming any missing name.
- `death_point(age, decline_draw, p) -> float`, `decline_health(v, age, decline_draw, p) -> float`, `health(senescence, v, age, decline_draw, p) -> float`.
- `Look` (frozen): `size_scale`, `extra_angle_rad`, `extra_arch_rad`, `browning`; `look(h, p) -> Look`.
- `plant_fade(v, p) -> float`, `tiller_lean_rad(v, p) -> float`, `opacity(leaf_fade, v, p) -> float`.
- `declined_leaf(leaf, tiller, v, p) -> DeclinedLeaf` (gate review G19): applies the rule to one leaf from organ-table dicts (leaf fields plus `age`, tiller fields), returning the drawn attach, direction, tiller axis, sizes, arch, browning and opacity; the main shoot (`tiller` 0) never leans. The vector helpers it needs live in `decline.py` too, and `grass.lpy` uses them from there. This is the one Python definition Phases 3 and 5 follow.
- Every function validates `v` and `h` in [0, 1] and raises `ValueError` naming the value.

### WP 2.1: Decline rule and model

- **Model:** Opus (fork; the decline semantics are the substance of the phase).
- **Owns:** `dev/lpy/grass.lpy`, `dev/lpy/decline.py`, `dev/lpy/tests/test_decline.py`.
- **Scope:** `decline.py` per the interface and G10 to G14. In `grass.lpy`: the new externs (`V_DEAD`, `DECLINE_AGE_SPREAD`, `DECLINE_AGE_REF`, `DECLINE_JITTER`, `DECLINE_RESIDUAL`, `DECLINE_SPLAY_DEG`, `FADE_DURATION`, `PREVIEW_DECLINE`), documented in the header; leaf `o` gains `fade` and `decline_draw` and drops the senescence terms from sizes, angle and arch (G15); a leaf is collapsed at `fade` 1; `decline_draw` drawn once at birth from its own stream `random.Random("decline:<SEED>:<leaf id>")`, so the shared `RNG` sequence and every Phase 1 draw keep their values (a draw on the shared stream would shift later organs); the G17 preview (productions frozen after `T_END`, interpretation applying `decline.py` for health, look, tiller lean and opacity, with opacity shown through material transparency levels).
- **Must pass:**
  - `timeout 120 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests/test_decline.py`: at `v` 1 every organ has `h` 1 and the look is identity; `h` is monotonic in `v`; every organ is dead at `V_DEAD` and older leaves die at higher `v`; the plant fade is 0 above `V_DEAD` and 1 at 0; bad inputs raise.
  - A script deriving `grass.lpy` to `T_END` still gives 5 tillers and at most 3 live leaves per tiller, and the same seed twice gives identical L-strings; deriving to `T_END + PREVIEW_DECLINE` leaves the L-string after `T_END` unchanged.
  - The user steps the preview in the L-Py GUI and judges the decline (orchestrator hands over).

### WP 2.2: Exporter and schema v2

- **Model:** Sonnet (pinned down by G15 and the field list).
- **Owns:** `dev/lpy/export_organs.py`, `dev/lpy/organ_table.schema.json`, `dev/lpy/tests/test_export_organs.py`, `dev/lpy/tests/fixture_grass.lpy`, `dev/lpy/tests/test_grass_model.py`.
- **Scope:** `schema_version` 2; leaf fields gain `fade` (unit interval) and `decline_draw` (number) in the key lists, `LeafOrgan` and the schema; the leaf descriptions say sizes, angle and arch are the healthy baseline. The dead-leaf check becomes: senescence 1 if and only if `dead`; `fade > 0` only when `dead`; `fade` never decreases; at `fade` 1 the sizes are zero and `attach` equals the tiller base; once collapsed it stays collapsed. The fixture follows the new fields. `test_grass_model.py` checks the real model against the new invariants.
- **Must pass:** `timeout 120 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests/test_export_organs.py` passes; after merge with WP 2.1 the whole `tests/` folder passes and the default export of `grass.lpy` validates.

## Phase 3: Geometry from the organ table (3 WPs in 2 waves)

Base: `36f5674` (tip of `main` after the Phase 2 merge). A generator that reads an organ table and builds the healthy plant mesh at one sample time, with fixed topology and per-vertex wind and decline attributes, plus the per-vertex decline rule in `decline.py` and a Blender script that exports a GLB and builds a stages scene to judge. Verified 2026-11-07: Blender 5.2.2 (`/snap/bin/blender`, bundled Python 3.13) runs headless and imports `decline.py` after putting `dev/lpy/` on `sys.path`; the default export validates (25 samples, 51 organs).

**Grill outcomes (2026-11-07):**
- **G21 Reference stays frozen.** `assets/build_grass_plant.py`, `grass_plant.blend` and `grass_plant.glb` are not edited (`wind-demo.html` loads the GLB). The new generator lives in `dev/plant/` and owns its own copy of the reference profiles (`curl_angle`, `leaf_width`, the curled-ribbon cross-section), with a docstring pointing back to the reference.
- **G22 Healthy blade shape.** The arch lies in the leaf plane exactly as in the preview: the midrib direction at fraction s is `rotate(direction, side, arch_rad * s^2)`, integrated along the blade, where `side = to_tiller_frame((-sin az, cos az, 0), tiller axis)` with `az` the leaf's `azimuth_rad`. The cross-section follows the reference (curl, outline, curl opening away from the axis), plus a per-leaf twist of the cross-section about the midrib as in the reference (`twist * s`, uniform ±0.5 rad). Geometry-side draws come from their own per-leaf streams, `random.Random("twist:<SEED>:<id>")` and `random.Random("phase:<SEED>:<id>")`, SEED being the export's `seed`.
- **G23 Extra arch in closed form.** Under decline a blade vertex is rotated rigidly about the collar, about `side`, by `extra_angle + extra_arch * s^2 / 3` (the mean bend angle up to s). Size scaling about the collar and the extra angle are exact; only the arch is approximated. A test bounds the tip error at the default extremes (healthy arch 0.9 rad plus `SHRIVEL_ARCH_RAD` 0.8, blade 20 cm) against the exactly re-integrated declined blade: below 5% of the blade length. **Amended in WP 3.1 (2026-11-07):** the rigid rotation alone gave an 8.8% tip error at the extremes, almost all from the chord shortening as the blade curls more. So the vertex's offset from the new collar is also scaled by `k = (1 - 2 (a + Δa)^2 s^4 / 45) / (1 - 2 a^2 s^4 / 45)` (second-order chord length of a bend growing as s^2; `a` the healthy arch, `Δa` the extra arch), which needs the leaf's healthy arch per vertex (`_ARCH`). The bound tightens to 2% of the declined blade length.
- **G24 Unroll.** A blade is rolled from the collar up to `s_roll = 1 - unroll` and opens to the reference curl and outline over a transition of 0.3 of its length beyond that. At `unroll` 0 it is a closed tube (0.97 of a full turn) along its whole length with the radius of its own sheath top; at `unroll` 1 it is exactly the mature reference profile with no closed base; the rolled region shrinks monotonically from the tip down. The worker pins the blend formula and tests the three properties.
- **G25 Sheaths nest by age.** Each leaf's sheath is a tube from its tiller base to its collar along the tiller axis, radius `min(SHEATH_RADIUS_MIN_M + SHEATH_RADIUS_PER_AGE_M * age, SHEATH_RADIUS_MAX_M) * size`, `size` being the leaf's elongation smoothstep (recoverable as the ratio of its sheath length to its final one, or simply `smoothstep(age / ELONGATION_AGE)` from the export's `params`). Older leaves are outside younger ones on the same tiller with no jumps at birth or death. Defaults put the outer plateau sheath at about 2 to 2.5 mm (the reference stem). A young leaf inside older sheaths is drawn as is; its rolled tip emerges from the open top of the pseudostem. The constants are geometry parameters, not L-Py externs.
- **G26 Resolution.** `GeometryParams` defaults: 16 blade rows, 4 cross segments (5 vertices across), 8 tube sides, 2 tube rings (about 85 blade and 18 sheath vertices per leaf, about 4.7k per plant). Phase 5 may lower them from its frame-rate measurement.
- **G27 Colour.** Not baked into the GLB: the shader computes browning from the decline attributes (it needs `v`). UVs stay as in the reference. The Blender stages view bakes a display-only colour attribute.
- **G28 Fixed topology now.** Every leaf has the same vertex count and order whatever its size and state. A build at time `t` takes an optional lifetime organ list (union over samples, `(birth_time, id)` order); organs not born yet, or collapsed (`fade` 1), are built degenerate at their first-seen `attach` (zero-area triangles, nothing visible). Without the list it builds the organs present at `t`. A test checks identical vertex counts and triangle lists across all 25 samples. Phase 4 then only loops over stages and packs.
- **G29 Per-vertex attributes** (glTF custom attributes, one per quantity; Phase 5's loader packs floats into vec4s to stay under 16 locations):
  - Wind: `_PIVOT` (blade: collar; sheath: tiller base), `_AXIS` (blade: leaf `direction`; sheath: tiller axis), `_S` (0..1 along the organ), `_STEM_T` (vertex height over `SHEATH_PLATEAU_M`), `_ORGAN` (lifetime leaf index + 1, shared by a leaf's blade and sheath), `_PHASE` (per-leaf, G22).
  - Decline: `_KIND` (0 sheath, 1 blade), `_COLLAR`, `_SIDE`, `_TILLER_BASE`, `_TILLER_AXIS`, `_TILLER_AZ`, `_TILLER` (0 main shoot), `_BIRTH`, `_DRAW`, `_SEN`, `_FADE`, `_ARCH` (the leaf's healthy `arch_rad`, added by the G23 amendment).
  - `_SEN` and `_FADE` are the values at the built sample (per-stage in Phase 4).
- **G30 Blender output.** CLI `blender --background --python dev/plant/build_grass_from_table.py -- <organs.json> [--glb OUT] [--time T] [--stages OUT.blend]`. `--glb` exports the healthy mesh at `T` (default `t_end`). `--stages` saves a `.blend` with a row of growth stages at t = 1, 2, 3, 4, 6, 8, 12 and, at t = 12, decline at v = 0.8, 0.6, 0.4, 0.2 drawn through `declined_vertex` (the shader's path) with a baked browning colour; leaves at opacity below about 0.1 are hidden there. Nothing generated is committed.
- **G31 WP split.** Wave 1: WP 3.1 (rule) and WP 3.2 (pure geometry) in parallel, meeting only through G29 and the `declined_vertex` interface below. Wave 2: WP 3.3 (Blender, mesh-level decline test) builds on both.
- **G32 `declined_vertex` interface** (in `decline.py`, stdlib only):
  - `VertexInputs` (frozen): `kind` (0 sheath, 1 blade), `s`, `collar`, `side`, `tiller_base`, `tiller_axis`, `tiller_azimuth`, `tiller` (int), `birth_time`, `decline_draw`, `senescence`, `fade`, `arch_rad`: the G29 decline attributes one to one (`tiller_azimuth` is `_TILLER_AZ`).
  - `DeclinedVertex` (frozen): `position`, `browning`, `opacity`.
  - `declined_vertex(position, vin, time, v, p) -> DeclinedVertex`, with leaf age `time - birth_time`, health and look per G11 and G12, opacity per G13. Steps: a sheath vertex scales its component along the tiller axis (from the tiller base) by `size_scale`; a blade vertex shifts by `(collar - tiller_base) * (size_scale - 1)`, scales about the new collar by `size_scale`, then rotates about `side` through the new collar by `extra_angle + extra_arch * s^2 / 3` and has its offset from the new collar scaled by the G23 chord factor `k`; every vertex of a tiller other than 0 then rotates rigidly by `tiller_lean_rad(v)` about `(-sin az, cos az, 0)` through the tiller base. Normals are not transformed. It matches `declined_leaf` exactly for the collar, sizes, angle and lean.
  - Validation like the rest of `decline.py` (`ValueError` naming the bad value; `kind` must be 0 or 1; `time >= birth_time`).
- **G33 Test plumbing.** All Phase 3 tests run with the `lpy` interpreter. A session fixture in `dev/plant/tests/conftest.py` (owned by WP 3.2) exports the real `grass.lpy` into a temporary directory through `export_organs.py`; no JSON is committed. The Blender smoke test runs `blender --background` in a subprocess capped at 120 s and is skipped, with the reason shown, when `blender` is not on the path.

- **G34 Gate review (2026-11-07).** Reviewers `code-optimizer-reviewer` and `system-architect`. Applied as follow-up WP commits, in order 3.1, 3.2, 3.3:
  - **WP 3.1:** `declined_vertex` clamps age at 0 when `time < birth_time` instead of raising (the GLSL does the same; unborn leaves of a fixed-topology build have a later `birth_time`), and `TIME_TOLERANCE` goes. `VertexInputs` gains `axis` (after `side`: the `_AXIS` attribute, the healthy leaf direction for a blade, the tiller axis for a sheath). `DeclinedVertex` gains `pivot` and `axis` for the wind: blade, the declined collar and the declined direction (healthy direction turned by the extra angle about `side`, then leaned); sheath, the tiller base and the leaned tiller axis; tested against `declined_leaf`'s `attach`, `direction` and `tiller_axis`. `smoothstep` becomes public. The docstring notes for the GLSL port: each blade row turns by the mean extra bend (a slight cross-section shear, by design), `chord_factor` raises where the GLSL must clamp, and normals are not transformed.
  - **WP 3.2:** the midrib uses the direction at each step's midpoint (`rotate(d0, side, arch * s_mid^2)`), so the 16-row mesh is close to the exact integral. Every degenerate leaf (unborn or collapsed) has `fade` 1 and its `collar` and blade `pivot` at its anchor. `STATIC_ATTRIBUTES` and `STAGE_ATTRIBUTES` tuples (stage: `pivot`, `axis`, `collar`, `stem_t`, `arch`, `sen`, `fade`; static: the rest), with a test that the static ones are identical across all samples of a lifetime build. `vertex_inputs(mesh, i) -> decline.VertexInputs` lives in `grass_geometry.py` (rejecting non-integral `kind` and `tiller`). `KIND_*` and `smoothstep` come from `decline.py`; the sample tolerance is renamed `SAMPLE_TIME_TOLERANCE`. Loader: non-object samples and organs, missing tiller fields and non-integral `tiller` and `rank` raise `OrganTableError`.
  - **WP 3.3:** the stage maths moves to a bpy-free `dev/plant/stages.py`, and every stage, growth included, goes through `declined_vertex` (turnover senescence shrinks and droops a leaf at v = 1 too, as in the preview). The script and the tests use `grass_geometry.vertex_inputs`. `test_declined_mesh.py` compares with the rebuilt leaf at the 2% bound, normalises the healthy mesh error by the healthy length, and runs `declined_vertex` over a lifetime build at every sample. The smoke test asserts the GLB vertex count equals the mesh's.
  - Deferred to Phase 4 and 5 (see the carry-over): GLB vertex order versus `PlantMesh` order, per-stage normals, the wind renderer's assumptions, GLSL clamps and float16 tolerances. Dropped: the repeated passes in `build_plant` (negligible at 51 leaves).

- **G35 User review of the stages (2026-11-07): one continuous leaf.** The separate sheath tube and blade met badly: the blade's midrib started on the tiller axis with its curl offset to one side, so its base stuck out of a flat-topped, constant-width cylinder (blocky sheaths, misaligned blades). Botanically sheath and blade are one organ, and a new leaf emerges vertical and rolled from the whorl before bending away at its collar. Replaces G24's rolled-tube placement, G25's tube and G26's tube resolution; G22 and G23 otherwise hold.
  - **One grid per leaf:** `SHEATH_ROWS` (3) rows from the tiller base up to the collar, then the 17 blade rows; the sheath's top row is the blade's first row (shared vertices, no seam). Every row has `cross_segments + 1` vertices; `cross_segments` becomes 8. About 20 x 9 = 180 vertices per leaf.
  - **Cross-section everywhere:** an arc whose midrib point lies on the midrib path and which curls toward the upper side (toward the tiller axis at the collar). In the sheath it is the wrap: `rolled_turn` of a full circle around the tiller axis, its midrib on the outer wall at the leaf's azimuth, radius the G25 sheath radius at the collar times a taper rising to `SHEATH_TAPER` (1.2) at the base. Above the collar the wrap (radius the collar radius) opens to the reference curl and outline: `open(s) = min(smoothstep(s / COLLAR_OPEN), unroll_blend(s, unroll))` with `COLLAR_OPEN` 0.2, so it is wrapped at the collar for every leaf and fully rolled while young.
  - **Midrib bend:** the blade's midrib direction at blade fraction s is `rotate(tiller_axis, side, angle * smoothstep(s / COLLAR_BEND) + arch * s^2)`, `angle` being `angle_from_axis_rad` and `COLLAR_BEND` 0.15, so every blade leaves its collar along the tiller axis and turns to its angle (the table's `direction`) by s = 0.15. It is integrated from the collar point on the outer wall by the midpoint rule.
  - **Decline (`decline.py`):** a sheath vertex scales uniformly about the tiller base by `size_scale` (its radius shrinks with it, matching the blade side at the shared collar row). A blade vertex keeps G32's collar shift and scale about the collar, but rotates about `side` by `extra_angle * bend_mean(s, collar_bend) + extra_arch * s^2 / 3`, where `bend_mean(s, c)` is the closed-form mean of `smoothstep(u / c)` over u in [0, s] (0 at s = 0, so the collar row never tears). `declined_vertex(position, vin, time, v, p, collar_bend)` takes the geometry's `COLLAR_BEND` (a uniform in the shader). The chord factor may be extended to the full bend profile if the 2% bound needs it, provided it stays closed form.
  - The constants live in `GeometryParams` (`sheath_rows`, `sheath_taper`, `collar_open`, `collar_bend`); `tube_sides` and `tube_rings` go.
  - Applied as WP 3.1 and WP 3.2 refinement commits in parallel, then WP 3.3 (stages pass `collar_bend`; the mesh-level test rebuilds the declined leaf with the sheath radius scaled by `size_scale`).

- **G36 Continuous-leaf follow-ups (user, 2026-11-07, after the G35 close-ups):**
  - **Emergence point.** A young leaf's collar sits below the collars of the older leaves wrapping it, so bending at its own collar poked it through their sheaths (up to 19 mm). Each blade is measured from its emergence point: the height along the tiller axis of the outermost enclosing collar on its tiller, or its own collar if none is higher. Below it the blade stays vertical and wrapped; the collar bend, the opening and the arch start there, over the free blade length. An enclosing leaf's collar counts with weight `1 - fade` (`h_e = max(own, max_k lerp(own, h_k, 1 - fade_k))` over older non-collapsed leaves of the tiller), so the emergence point eases down as an old leaf fades instead of popping. The `collar` attribute is the emergence point and enclosed blade rows have `s` 0, so `declined_vertex` is unchanged (uniform scaling about the tiller base below it); `s` and `collar` move to `STAGE_ATTRIBUTES`. For enclosed leaves `DeclinedVertex.pivot` is the scaled emergence point, not `declined_leaf.attach`.
  - **Split sheaths.** The wrap radius keeps the G25 nesting by age, but its arc length is capped at `SHEATH_WIDTH_FACTOR` (1.3, `GeometryParams.sheath_width_factor`) times the blade's base width; beyond that the wrap is an open arc of less than a full turn (old sheaths split as the shoot thickens), and the opening into the blade starts from that arc. Removes the broad scoop on old narrow leaves.
  - **Uneven rows.** Blade row i sits at `s = (i / blade_segments)^BLADE_ROW_POWER` (1.6, `GeometryParams.blade_row_power`), denser near the emergence point, so the collar bend gets about 6 rows instead of 2.5 (no elbow). Same vertex count.

- **G37 Pivot on the wall, one GLB material (2026-11-07).** The blade's `collar` attribute (and its wind `pivot`) is the midrib's start point at the emergence row, on the outer wall of the wrap, not the tiller-axis point: the decline rule turns the blade about that point, and pivoting on the axis rotated the wall offset too (3.2% tip error on the short, drooped L4.1). Uniform scaling of enclosed rows holds for any collar point. The GLB is written with one material, because glTF splits primitives by material and the collar row shared by sheath and blade was written twice (8694 vertices against the mesh's 8280); the shader tells sheath from blade by `_KIND`. The stages scene keeps two display materials.

**`grass_geometry.py` interface (settled, WP 3.2 writes it, WP 3.3 uses it):**
- `GeometryParams` (frozen): resolution (G26), sheath radius constants (G25), cross-section constants from the reference (curl, rolled fraction 0.97, roll transition 0.3, twist range); no other literals in the mesh code.
- `VertexAttrs` / `PlantMesh` (frozen or plain dataclass of lists): positions, triangle (or quad) indices, per-loop or per-vertex UVs, material per face (sheath or blade), and every G29 attribute by its name without the underscore.
- `load_table(path) -> OrganTable` (header, samples), `lifetime_leaves(table) -> list[str]`, `build_plant(table, time, p, lifetime=None) -> PlantMesh` (time snapped to a sample; an error names the nearest samples otherwise).
- Stdlib only, importing `decline.py`'s vector helpers (`unit`, `cross`, `rotate`, `dir_from`, `to_tiller_frame`) after putting `dev/lpy/` on `sys.path`. No `bpy`.

### WP 3.1: Per-vertex decline rule

- **Model:** Opus (fork).
- **Wave:** 1.
- **Owns:** `dev/lpy/decline.py`, `dev/lpy/tests/test_decline.py`.
- **Scope:** `VertexInputs`, `DeclinedVertex` and `declined_vertex` per G32 and G23, sharing the existing health, look and lean functions (no second copy of the rule). Module docstring updated. Tests: identity at `v` 1 with `senescence` 0; the collar and blade tip of a synthetic straight-plus-arched blade (built in the test with the G22 midrib integration) agree with `declined_leaf` (collar exact, tip within the G23 bound at the default extremes, reported margin); lean leaves the tiller-0 vertices untouched and rotates others rigidly about the tiller base; sheath vertices stay on their tiller's axis line through the base after scaling; browning and opacity equal `declined_leaf`'s; bad inputs raise.
- **Must pass:** `timeout 120 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests` (all, including the Phase 1 and 2 tests); flake8 on `decline.py` and `test_decline.py`.

### WP 3.2: Pure geometry builder

- **Model:** Opus (fork; the look and the topology are the substance).
- **Wave:** 1.
- **Owns:** `dev/plant/grass_geometry.py`, `dev/plant/tests/conftest.py`, `dev/plant/tests/test_grass_geometry.py`.
- **Scope:** the interface above, G21, G22, G24 to G29. Tests: identical vertex count and index list over all samples with the lifetime list; unborn and collapsed leaves fully degenerate at their first-seen `attach`; midrib length equals `blade_length_m` and the sheath top is `attach` (within tolerance); the sheaths of one tiller nest (older radius larger at every sample); unroll 0 gives a closed tube of the sheath-top radius and unroll 1 the reference curl; every G29 attribute present with one value per vertex and `_ORGAN` shared by a leaf's blade and sheath; determinism for a fixed seed.
- **Must pass:** `timeout 180 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/plant/tests`; flake8 on the owned files.

### WP 3.3: Blender script and mesh-level decline check

- **Model:** Sonnet (fresh; the geometry and the rule are fixed by wave 1).
- **Wave:** 2.
- **Owns:** `dev/plant/build_grass_from_table.py`, `dev/plant/tests/test_declined_mesh.py`, `dev/plant/tests/test_blender_smoke.py`.
- **Uses:** `grass_geometry.py` (WP 3.2), `decline.declined_vertex` (WP 3.1), the `conftest.py` export fixture.
- **Scope:** G30: mesh from `PlantMesh` (materials as in the reference, smooth shading, UVs, every G29 attribute, `me.validate()` raising), GLB export with `export_yup=False` and attributes on, the stages scene (one object per stage, spaced along x, browning baked into a colour attribute shown by the material, hidden faded leaves), headless and GUI use like the reference. `test_declined_mesh.py`: applies `declined_vertex` to the real built mesh at t = 12 for several `v` and compares each leaf's collar and blade tip with a mesh rebuilt from `declined_leaf` values, within the G23 bound. `test_blender_smoke.py`: G33.
- **Must pass:** `timeout 300 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/plant/tests` (the smoke test must run, not skip, here); a headless `--glb` and `--stages` run on a fresh export; flake8 on the owned files.

**Phase gate:** every WP's Must-pass on the merged head; `timeout 300 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests cobeart-app/public/worlds/glade/dev/plant/tests`; `~/miniconda3/envs/splat-env/bin/python -m flake8 --max-line-length 120` on `dev/lpy/decline.py` and `dev/plant/`; `node scripts/fitness.mjs`. Then the user opens the stages `.blend` in Blender and judges growth and decline (orchestrator hands over with exact commands).

## Phase 4: Fixed-topology VAT export (3 WPs in 2 waves)

Base: `1d0f3c7` (tip of `main` after the Phase 3 outcome commit). Bakes the full-lifetime mesh (P6) into a vertex animation file: one index buffer over every organ the plant ever has, per-stage positions and normals, per-organ data, and analytic organ events, in a species-neutral format that a later flower species reuses with its own adapter. No pages are served and Blender is not needed.

**Verified 2026-11-22 (seed 1, a full-step export with `--samples 241`):**
- 46 lifetime leaves, 8,280 vertices; 31 leaves turn senescent and 17 collapse by t = 12; every collapse is exactly `FADE_DURATION` (0.5) after death.
- Of the Phase 3 `STAGE_ATTRIBUTES`, `pivot`, `axis`, `collar`, `arch`, `sen` and `fade` take one value per leaf and kind (sheath `pivot` and `axis` are the static tiller base and axis), `s` is fully static (wrongly listed as a stage attribute), and `stem_t` is per leaf for blades and a per-leaf height times the static `s` for sheaths. Only positions (and normals) vary per vertex per stage.
- In the model, `senescence` rises linearly over the leaf's drawn `senescence_duration` (a birth draw not exported so far) and `fade` linearly over `FADE_DURATION`; both are therefore exact closed forms of time given per-leaf event times.
- float16 absolute positions err by at most 0.12 mm (|position| up to 0.26 m).
- Linear blending between uniform stages, collapsed leaves held at their last visible shape, visible leaves only (worst vertex, then the 95th percentile over times): N 25: 22 / 17 mm; 33: 16 / 11 mm; 49: 11 / 6.6 mm; 97: 6.3 / 3.9 mm. The worst case is always a young blade at about age 1 (lengthening and unrolling, tip moving about 11 mm per 0.05 P). Leaves are born every 0.25 to 0.5 P after t = 6, so non-uniform spacing buys little. Without holding collapsed leaves the error is 228 mm (a full-size dead blade flying to its anchor).

**Grill outcomes (2026-11-22):**
- **G38 Analytic organ events.** Senescence and fade are not stored per stage. Each leaf carries static `senescent_time`, `senescence_duration`, `death_time` and `collapse_time`; the consumer computes `sen(t) = clamp((t - senescent_time) / senescence_duration, 0, 1)` and `fade(t) = clamp((t - death_time) / FADE_DURATION, 0, 1)`, and hides the organ for `t < birth_time` or `t >= collapse_time`. An event that does not happen by `t_end` is `null` in JSON and `NEVER` (`1.0e30`, finite because GLSL infinities are unreliable) in the binary. These formulas reproduce the track's `senescence` and `fade` at every step (the model is step-quantised but linear).
- **G39 Collapsed organs carry forward.** In every stage at or after its collapse, an organ keeps the positions, normals and per-organ stage values of the last stage in which it was visible, so a blend never flies a blade to its anchor; the consumer hides it by the G38 rule. Unborn organs stay degenerate at their first-seen attach (they are born at near-zero size).
- **G40 Three data domains.** Per vertex static (`s`, `kind`, organ index, UV), per organ static (birth, draws, tiller frame, events) and per organ per stage (collar, blade direction, arch, sheath length, emergence height). Per vertex per stage holds only position and normal. `pivot`, `axis` and `stem_t` are rebuilt from these (`stem_t` with `SHEATH_PLATEAU_M`). `grass_geometry.STAGE_ATTRIBUTES` moves `s` to `STATIC_ATTRIBUTES`.
- **G41 Pure-Python writer, GLB container.** The VAT file is written without Blender by a stdlib-only Python writer (also importable in Blender). It is a valid glTF 2.0 binary whose one mesh is the preview stage (viewable in any glTF viewer) and whose `extras.vat` describes the stage blocks in the same BIN chunk: one fetch, testable with the lpy interpreter.
- **G42 Normals baked per stage**, octahedral-encoded (`oct16`, two int16 snorm, 4 bytes per vertex per stage), computed from the quad grid (area-weighted, the generator's winding: underside). Phase 5 rotates them by the decline row rotation and the lean; the export only carries them.
- **G43 Species-neutral, self-describing format.** The header lists every block (name, domain, timing, components, storage, buffer view), the organ ids, the stage times and a free `species_params` object. The writer and loader know nothing about grass; a species adapter maps its geometry onto named blocks. Stage times are model time (phyllochrons for grass); mapping Glade's `g` onto them is Phase 5's.
- **G44 Layout.** Generic code in `dev/vat/` (`vat_format.md`, `vat_writer.py`, `vat.mjs`, `fixtures/`); the grass adapter in `dev/plant/bake_grass_vat.py`. `decline.py` stays grass-side.
- **G45 Event times come from the organ table (schema v3).** `grass.lpy` adds the leaf's static `senescence_duration` to leaf `o`; the exporter derives one step at a time and writes a top-level `organ_events` map (leaf id to the four G38 values, `null` where the event has not happened by `t_end`). The Phase 1 to 3 checks of `schema_version` move to 3.
- **G46 Stages are the export's samples.** The bake uses every sample of the organ table it is given as one stage, in order; the canonical bake exports with `--samples 49` (0.25 P spacing, about 5 MB per variant with float16 positions and oct16 normals). Phase 5's frame-rate and look check may change N; nothing else depends on it.
- **G47 Variants.** Phase 4 ships the tooling (`--seed` on the export) and one default bake; the number of variants is Glade's memory decision. Tests bake two seeds.
- **G48 Loader tests.** A tiny synthetic fixture (2 organs, 3 stages, every storage type), written by the generic writer through `dev/vat/make_fixture.py`, is committed in `dev/vat/fixtures/tiny.vat.glb`. A node test in `cobeart-app/test/glade-vat.test.js` loads it in CI; a Python test checks a fresh write is byte-identical to it. Locally, a Python test runs `node` on a real grass bake and compares the decoded values with the source arrays.

- **G49 Gate review (2026-11-22).** Reviewers `code-optimizer-reviewer` and `system-architect`; no correctness defect in encoding, layout or event derivation. Applied as follow-up WP commits (4.1 and 4.2 in parallel, then 4.3), and the format below is amended accordingly (still `version` 1, no consumers yet):
  - **Frame (user):** `VatPlant.frame` = `{up: "+Z" | "+Y", unit}` is written to `extras.vat.frame`; blocks stay in the species frame and the writer only puts a preview rotation on the glTF node (`[-0.70710678, 0, 0, 0.70710678]` for +Z up). The primitive gets one material `{name: "vat_preview", doubleSided: true}` (one material keeps G37). The preview `NORMAL` is normalised.
  - **Lifespan (user):** a format-defined optional organ-static block `lifespan` (2, `f32`: `appear`, `vanish`, `never` allowed). When present, the preview `POSITION` collapses every vertex of an organ not alive at the preview stage time (`appear <= t < vanish`) onto that organ's first vertex; stage blocks stay truthful. Grass drops its `birth` block (leaf age is `t - appear`) and its 4-component `events` becomes `senescence` (3: `senescent_time`, `senescence_duration`, `death_time`); `collapse_time` is `lifespan.vanish`.
  - **Meaning (user):** a block may carry `meta` (`components`: one name per component, `unit`); `VatPlant.species_version` (int) goes to `extras.vat.species_version` and is checked by the species consumer, not the loader. Readers ignore unknown header fields and unknown blocks, so additive changes keep `version` 1. A per-species profile doc, `dev/plant/grass_vat_profile.md`, holds the grass block table with meanings and units, the rebuild rules (pivot, axis, `stem_t`, age, sen and fade, normals on the underside) and the required `species_params` keys.
  - **Hierarchy (user):** convention only, in the spec: an organ-static `u32` block `parent` (parent organ index, `organ_count` for a root); grass does not write it. A VAT organ is defined as a drawable unit with its own lifecycle and per-organ data.
  - **Writer and loader:** `NEVER` is the f32-exact value of 1e30 in Python, the header and JS, and any block that can hold it must be `f32`; error strings are built only on failure; the preview slices one stage before decoding; malformed headers (missing or mistyped fields, `organ_count` against `organ_ids`, indices `componentType`, unpadded JSON chunk) raise `VatError` / named `Error`s; non-numbers and bools in float blocks are rejected; the oct16 zero check lives in one place with its location; `decodeBlock` drops its dead clause and per-vector allocation.
  - **Organ table:** `grass_geometry` rejects inconsistent events (a later event set while an earlier one is null, or out of order).
  - **Bake:** organ-stage values as named records (no stride arithmetic), emergence read back from the mesh (`stem_t * SHEATH_PLATEAU_M` of the blade's first vertex), `tiller` as `u32`, `meta` on every grass block, `frame` +Z in metres, `species_version` 1.
  - Not applied: a shared `is_degenerate` predicate across `grass_geometry` and the bake (the bake reads the mesh instead); a `branch` domain (waits for a flower that needs it).

**VAT format v1 (settled; WP 4.2 writes `vat_format.md` from it; amended by G49):**
- **Container:** glTF 2.0 GLB, little-endian, JSON chunk then BIN chunk, every buffer view 4-byte aligned. The glTF has one buffer (the BIN chunk), one mesh with one primitive: `POSITION` (f32 vec3, the `preview_stage` positions, with `min`/`max`), `NORMAL` (f32 vec3, decoded from the `normal` block at the preview stage, only when that block exists), `TEXCOORD_0` (f32 vec2, only when a vertex-static `uv` block with 2 components exists) and `indices` (u32 triangles). No materials.
- **`extras.vat`** at the glTF top level: `format` `"cobeart-vat"`, `version` 1, `species` (string), `species_params` (JSON object, free), `vertex_count`, `organ_count`, `organ_ids` (strings, organ index order), `stage_times` (strictly increasing), `preview_stage` (index), `never` (`1.0e30`), and `blocks`: a list of `{name, domain: "vertex" | "organ", timing: "static" | "stage", components: 1..4, storage: "f32" | "f16" | "u32" | "oct16", bufferView}`. Block names are unique.
- **Element layout:** a static block holds `count * components` values (`count` is `vertex_count` or `organ_count`); a stage block holds `stage_count` such runs, stage-major (`[stage][element][component]`). `oct16` requires `components` 3 and stores each unit vector as two int16 snorm (octahedral), 4 bytes per element. `u32` values are integers.
- **Required blocks:** `position` (vertex, stage, 3) and `organ` (vertex, static, 1, `u32`, the organ index of each vertex). Everything else is species-defined.
- **Validation (writer and loader):** counts and lengths agree, every value finite, `f16` values within the float16 range, `oct16` input vectors non-zero (normalised by the writer), organ indices below `organ_count`, triangle indices below `vertex_count`; errors name the block, and the stage and element where one applies.

**Generic writer interface (`dev/vat/vat_writer.py`, stdlib only, WP 4.2):**
- `Block` (frozen): `name`, `domain`, `timing`, `components`, `storage`, `values` (flat floats or ints, stage-major for stage blocks).
- `VatPlant` (frozen): `species`, `species_params` (mapping), `organ_ids`, `stage_times`, `triangles` (flat u32 indices), `preview_stage`, `blocks`.
- `encode_vat(plant) -> bytes` (deterministic: same input, same bytes), `write_vat(plant, path)`, `decode_vat(data: bytes) -> VatPlant` (values decoded to floats, `oct16` to unit xyz), `VatError(ValueError)`.

**JS loader interface (`dev/vat/vat.mjs`, ES module, no dependencies, WP 4.2):**
- `parseVat(arrayBuffer) -> Vat`: `{species, speciesParams, vertexCount, organCount, organIds, stageTimes, previewStage, never, indices: Uint32Array, blocks: Map<name, {domain, timing, components, storage, raw}>}` with `raw` the stored typed array (`Float32Array`, `Uint16Array` bits for `f16`, `Int16Array` for `oct16`, `Uint32Array`), ready for a GPU upload.
- `decodeBlock(block) -> Float32Array | Uint32Array` (`f16` to floats, `oct16` to unit xyz triples) and `loadVat(url)` (fetch plus `parseVat`). Errors are `Error`s naming the block and what failed.

**Grass blocks (`bake_grass_vat.py`, WP 4.3; as first planned, superseded by G49: the authoritative list is `dev/plant/grass_vat_profile.md`):**
- Vertex stage: `position` (3, `f16`), `normal` (3, `oct16`).
- Vertex static: `organ` (`u32`, lifetime leaf index, 0-based), `kind` (`u32`, 0 sheath, 1 blade), `s` (`f32`), `uv` (2, `f32`, per vertex from the grid: u across, v along the leaf).
- Organ static (`f32`): `birth`, `draw` (decline draw), `phase`, `tiller`, `tiller_az`, `tiller_base` (3), `tiller_axis` (3), `side` (3), `events` (4: `senescent_time`, `senescence_duration`, `death_time`, `collapse_time`, `NEVER` where null). G49 replaced `birth` and `events` with `lifespan` and `senescence` and made `tiller` `u32`.
- Organ stage (`f32`): `collar` (3, the G37 emergence-row midrib point), `direction` (3, the healthy blade direction), `arch`, `sheath_length`, `emergence` (height along the tiller axis).
- `species` `"grass"`; `species_params`: `model`, `seed`, `params` (every extern of the export), `geometry` (`dataclasses.asdict(GeometryParams())`, so `collar_bend` travels with it).
- Preview stage: the last stage (`t_end`).

### WP 4.1: Organ events and schema v3

- **Model:** Opus (fresh; the event semantics must match the step-quantised model exactly).
- **Wave:** 1.
- **Owns:** `dev/lpy/grass.lpy`, `dev/lpy/export_organs.py`, `dev/lpy/organ_table.schema.json`, `dev/lpy/tests/` (all files), `dev/plant/grass_geometry.py`, `dev/plant/tests/test_grass_geometry.py`.
- **Scope:** G38, G40's attribute fix and G45. `grass.lpy`: leaf `o` gains `senescence_duration` (the existing birth draw, unchanged values), documented in the header. Exporter: `schema_version` 3, leaf field `senescence_duration`, derivation one step at a time recording per leaf the first senescent step, the death step and the collapse step, and a top-level `organ_events` map `{id: {senescent_time, senescence_duration, death_time, collapse_time}}` where `senescent_time` is the step before the first senescent step (so the G38 formula holds), `null` for events not reached by `t_end`; schema and fixture follow. `grass_geometry.py`: accepts schema 3, `OrganTable` gains `events: Mapping[str, LeafEvents]` (frozen, `None` for null), `s` moves to `STATIC_ATTRIBUTES`. Output unchanged otherwise (same seed, same organ values).
- **Must pass:**
  - `timeout 300 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests cobeart-app/public/worlds/glade/dev/plant/tests` (the Blender smoke test runs), including new tests: on a `--samples 241` export of `grass.lpy` the G38 formulas reproduce `senescence` and `fade` of every leaf at every sample within 1e-6, and every leaf with `fade` 1 at a sample has `collapse_time <=` that sample; all leaf values except the new field equal the schema v2 values for seed 1.
  - flake8 (max 120) on the owned Python files.

### WP 4.2: Generic VAT writer and loader

- **Model:** Sonnet (fresh; pinned down by the format and interfaces above).
- **Wave:** 1.
- **Owns:** `dev/vat/` (all files: `vat_format.md`, `vat_writer.py`, `vat.mjs`, `make_fixture.py`, `fixtures/tiny.vat.glb`, `tests/test_vat_writer.py`), `cobeart-app/test/glade-vat.test.js`.
- **Scope:** G41, G43, G48 and the format, writer and loader interfaces above. Float16 and octahedral encoding implemented in both languages (round to nearest even for f16; standard octahedral with sign-not-zero). `vat_format.md` is the spec a new species adapter reads: the container, `extras.vat`, layouts, storage types and an example header. `make_fixture.py` writes the fixture deterministically.
- **Must pass:**
  - `timeout 120 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/vat/tests`: encode/decode round trip within each storage's tolerance (f32 exact, f16 relative 2^-11, oct16 angle below 0.01 deg), deterministic bytes, the committed fixture equals a fresh write, the GLB is valid glTF (header, chunks, accessor bounds, `POSITION` min/max), every validation error raised with its context, and a cross-language check that runs `node` on the fixture and compares decoded values (skipped with the reason when `node` is missing).
  - `cd cobeart-app && timeout 300 npm test` passes, including `glade-vat.test.js` (fixture header, block shapes, decoded values, and errors on a truncated or mislabelled buffer).
  - flake8 (max 120) on the Python files; `node scripts/fitness.mjs` 0 violations.

### WP 4.3: Grass VAT bake

- **Model:** Opus (fresh; carry-forward, normals and the mapping onto blocks are the substance).
- **Wave:** 2.
- **Owns:** `dev/plant/bake_grass_vat.py`, `dev/plant/tests/test_bake_grass_vat.py`.
- **Uses:** `vat_writer` and `vat.mjs` (WP 4.2), `OrganTable.events` and the corrected attribute tuples (WP 4.1), the `conftest.py` export fixture.
- **Scope:** G39, G40, G42, G46, G47 and the grass blocks above. CLI `~/miniconda3/envs/lpy/bin/python dev/plant/bake_grass_vat.py ORGANS.json OUT.vat.glb`; a pure `bake(table, p) -> VatPlant` the tests call. Builds every sample with the lifetime list, triangulates the quads with the generator's winding, computes area-weighted vertex normals per stage (unborn degenerate leaves get +Z), applies G39 carry-forward to positions, normals and organ stage blocks, fills `events` from `OrganTable.events`.
- **Must pass:**
  - `timeout 300 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/plant/tests`: decoded positions equal `build_plant` at every stage within the f16 tolerance for every leaf not collapsed at that stage, collapsed leaves equal their last visible stage; identical triangle list across stages and equal to the triangulated `PlantMesh.faces`; normals unit and on the underside side of their faces; organ stage and static blocks equal the `PlantMesh` attributes they replace (`pivot`, `axis`, `stem_t` rebuilt from them within 1e-6); G38 formulas from the `events` block reproduce `sen` and `fade` at every stage; two seeds bake; a `node` round trip of a real bake through `vat.mjs` matches (skipped without `node`).
  - A bake of a fresh `--samples 49` export writes the file and prints vertex, organ and stage counts and the file size; flake8 on the owned files.

**Phase gate:** every WP's Must-pass on the merged head; `timeout 400 ~/miniconda3/envs/lpy/bin/python -m pytest cobeart-app/public/worlds/glade/dev/lpy/tests cobeart-app/public/worlds/glade/dev/plant/tests cobeart-app/public/worlds/glade/dev/vat/tests`; `~/miniconda3/envs/splat-env/bin/python -m flake8 --max-line-length 120` on `dev/lpy/`, `dev/plant/` and `dev/vat/` Python files; `cd cobeart-app && npm test`; `node scripts/fitness.mjs`. Human check: open the default bake in a glTF viewer (the preview mesh) and read the printed sizes; the look of blended stages is Phase 5's.

## Phase 5: Growth slider on a dev page (outline)

Base: the tip of `main` after the Phase 4 outcome commit. Inputs: a bake per `dev/plant/grass_vat_profile.md` (canonical commands there), loaded with `dev/vat/vat.mjs`; see the carry-over to Phase 5. A dev page, next to `wind-demo.html`, that draws instanced grass from the VAT with a per-instance growth value `g` and vitality `v`, blends the two stages around `g` and applies the `decline.py` look in GLSL, under the top-down camera, with the wind from `dev/wind/` applied on top. Sliders for `g` and `v` (global and randomised per instance). Measure frame rate against instance count, and decide on the P6 alternative from that. The GLSL mirror of `decline.py` hardcodes `MAX_DEATH_OFFSET` (0.95) and clamps where Python raises; Glade refuses revival once `v <= V_DEAD`. Open for its grill: whether the wind renderer is extended or a new renderer is written, culling of zero-size triangles, and level of detail.

## Deferred (not in this roadmap)

- Glade integration: driving `g` from the energy-driven growth in `sim/`, replacing the sprite atlas with instanced 3D meshes (a GL roadmap decision: depth buffer, halos, draw order), and settling the on-floor plant size against about 3 mm per projector pixel.
- The analytic-shader alternative to the VAT (P6).
- Second-order tillers (P1).
- Other species, and a fixed maximum of about three hierarchy levels for branching ones.
- Hand (OptiTrack) wind and the remaining wind MVP steps (gust tuning, instance count), tracked outside this roadmap.

## Outcomes

### Phase 0 (2026-11-02)

`a315c36` `[GP 0] Add the Glade plants roadmap.` on `gp-1-lpy-model` from `main` `2fa5d1c`.

### Phase 1 (2026-11-02)

- **Commits:** `201e5ed` grill outcomes and verified L-Py API; `14195b6` [GP 1.1] `grass.lpy` (Opus fork); `b1843be` [GP 1.2] exporter, schema and tests (Sonnet); plus this outcome commit. Fast-forwarded into `main`.
- **Gate:** lpy pytest 23 passed (15 exporter tests on a fixture, 8 on the real `grass.lpy`); flake8 clean; `node scripts/fitness.mjs` 0 violations; default export of `grass.lpy` validates (25 samples, 47 organs at t = 12, about 380 KB). User stepped the model in the L-Py GUI and approved it after G8.
- **Model facts:** main-shoot leaf r born at t = r - 1; tillers T1 to T4 born at t = 3, 4, 5, 6; at most 3 live leaves per tiller at every step; a leaf turns senescent in the step its replacement appears and dies 2 P later (L0.1: senescent at 3, dead at 5). A rolled young leaf is at most about 18 deg off its tiller axis (G4 angle curve plus jitter).
- **Gate-review decisions (G9):** reviewers `code-optimizer-reviewer` and `system-architect`. Applied as amends of the WP commits:
  - Leaf `direction` is measured in the tiller's frame (`angle_from_axis_rad` replaces `angle_from_vertical_rad`); before, young tiller leaves pointed up to 33 deg off their own sheath.
  - `Leaf` carries `birth_time`; senescence is carried in `o["senescence"]` instead of a global (this also fixed a one-step senescence lag); no production mutates globals.
  - One `emits(t_next)` helper; `PHYLLOCHRON` is a constant, not an extern.
  - Exporter: leaf ages rounded; organs sorted by `(birth_time, id)`; `--samples` below 2 or above steps + 1 is an error; `T_END` must be a multiple of `DT`; dead-leaf invariants checked on every export; a test module for the real model.
  - Not applied, open for Phase 2: tiller `state` (schema pins it to `growing`) and a `parent_tiller` field for second-order tillers.
- **Lessons:** the L-Py API facts (externs only via `extern(...)`, `__externs__`, `derivation length` expressions) were verified before the grill and saved a round. Interface changes after review forced rebuilding the phase branch; worker worktrees on the old base had to be reset or rebased onto the new 1.1 commit, which went cleanly because each WP owns disjoint files.

### Carry-over to Phase 2 (resolved)

The tiller-state and time-varying-direction items were dropped by G10 to G15: tillers do not die in the track, and the lean is applied by `decline.py`. The field-list item is resolved by deriving the exporter's key lists from the dataclasses (G19).

- `grass.lpy` keeps birth draws in module globals (`LEAF_DRAWS`, `TILLER_O`) reset by `Start()`; time-varying state lives in modules. Keep that split when adding decline.
- Decline needs tiller state: widen `Tiller` with a `state` parameter, widen the schema's tiller `state` beyond `growing` and the exporter's `TillerOrgan`, bump `schema_version` to 2. Collapse may also need a time-varying tiller `direction`, which today is fixed at birth in `TILLER_O`.
- The organ field list exists three times (exporter key lists, dataclasses, schema); a new field edits all three.
- Phase 3 should build from `direction` (authoritative), `attach`, the sheath along the tiller axis from the tiller `base`, and treat unborn organs as collapsed at their first-seen `attach`.

### Phase 2 (2026-11-07)

- **Commits on `gp-2-decline` from `86b8c17`:**
  - `80ca360`: the grill outcomes (G10 to G18).
  - `c2d18b2` [GP 2.1]: `decline.py`, the healthy-baseline track with leaf fade, and the preview (Opus fork).
  - `b42a878` [GP 2.2]: exporter and schema v2 (Sonnet).
  - `aa43ee1`: the G18 correction.
  - `74d7a0a`: the gate-review notes.
  - `62c25f4` [GP 2.1] and `bb744ff` [GP 2.2]: the gate-review fixes.
  - `c7eaedb` and `b7de6bc` [GP 2.1]: the user's preview review (G20).
  - This outcome commit. Fast-forwarded into `main`.
- **Gate:**
  - The lpy pytest run gives 75 passed: `test_decline.py`, the exporter tests on the fixture, and `test_grass_model.py` on the real model.
  - flake8 is clean, and `node scripts/fitness.mjs` reports 0 violations.
  - The default export of `grass.lpy` validates: 25 samples, 51 organs at t = 12.
  - The user stepped the preview in the L-Py GUI and approved it after G20.
- **Model facts (replacing the Phase 1 timing facts):**
  - Main-shoot leaf r is still born at t = r - 1.
  - With seed 1, tillers T1 to T4 emerge at t = 3.05, 4.50, 5.05 and 6.55, with phyllochrons 0.87, 0.92, 0.89 and 1.02.
  - One chance coincidence remains: at t = 10.0 the main shoot, T1 and T2 emit a leaf in the same step. G20 does not rule this out; a per-leaf interval jitter would.
  - The derivation runs `(T_END + PREVIEW_DECLINE) / DT` = 360 steps; the exporter samples only up to `T_END`.
- **Gate-review decisions (G19):** reviewers `code-optimizer-reviewer` and `system-architect`. Applied as follow-up WP commits:
  - `declined_leaf` and the vector helpers moved into `decline.py`, so the leaf-level rule has one Python home. The main shoot never leans.
  - `decline_draw` is a unit draw in [-1, 1], and `DECLINE_JITTER` is a `DeclineParams` field.
  - Validation at load time: durations and `DT` must be positive, and the jitters non-negative. The first senescent step goes through the same clamp and snap as later steps.
  - The exporter checks the decline externs via `DeclineParams.from_mapping`.
  - The exporter's field lists are derived from the dataclasses, with a test that the schema matches them.
  - Explicit freeze and contract tests.
  - `importlib.reload(decline)` so edits reach a GUI reload.
  - Sheaths brown with their leaves in the preview.
  - Not applied:
    - a `beyond_saving` helper (Glade compares `v <= V_DEAD` itself);
    - recomputing the preview time per step instead of `STATE_TIME` (a one-step lag in Animate is negligible);
    - a per-vertex arch approximation and per-leaf event times, which are open in the Phase 3 and Phase 4 grills.
- **Lessons:**
  - The grill turned decline from a timeline section into an independent vitality axis. That removed the need for decline tracks from several onsets, kept the time span and sample count, and matches Glade's decline-at-any-energy and revival.
  - My check that L-Py imports sibling modules was wrong: the runner script sat in the same folder. The worker caught it. Verify imports from a different directory.
  - An `Lsystem` that is garbage-collected empties its globals, and iterating an L-string after that segfaults. Keep the `Lsystem` alive in tests and scripts.
  - Separate per-organ random streams (`decline:`, `emergence:`, `phyllochron:` and `senescence:`, each with seed and id) let new draws be added without changing the existing plant.
  - Workers must not use `git stash`: a worker's stash drop was blocked, and the user had to drop the entry.

### Carry-over to Phase 3

- **Rule and inputs:**
  - The decline rule is `decline.declined_leaf(leaf, tiller, v, p)`. `leaf` is an organ-table leaf dict (it includes `age`), `tiller` is the tiller dict, and `p = DeclineParams.from_mapping(doc["params"])`.
  - It is stdlib only. Blender's Python can import it after putting `dev/lpy/` on `sys.path`.
  - Build the healthy mesh from the track. Use `declined_leaf` only to view decline stages in Blender, or to check a shader port against it.
- **Track semantics (schema v2):**
  - Sizes, `angle_from_axis_rad` and `arch_rad` are the healthy baseline at the leaf's age.
  - `senescence` and `fade` come from turnover.
  - A leaf at `fade` 1 is collapsed: zero sizes, `attach` at its tiller base.
  - `direction` is authoritative, but `declined_leaf` rebuilds the direction from `azimuth_rad` and `angle_from_axis_rad` in the (leaned) tiller frame. Both agree in the track.
- **Per-vertex arch:** the preview integrates arch along 8 segments (`rotate(d, side, arch * s^2)` per segment in `grass.lpy`'s `blade_mesh`). The grill settles the closed form that Blender and GLSL share.
- **Shapes from Phase 1 still hold:** build from `direction`, `attach`, and the sheath along the tiller axis from the tiller `base`. Treat unborn organs as collapsed at their first-seen `attach`.
- **Leaf count:** the leaf count per lifetime changed with G20 (51 organs at t = 12 for seed 1), so Phase 4's vertex budget should use a fresh export.

### Phase 3 (2026-11-22)

- **Commits on `gp-3-geometry` from `36f5674`:**
  - `0504d9e`: the grill outcomes (G21 to G33); `f10afb2`: the G23 amendment.
  - `f77f6a9` [GP 3.1] `declined_vertex` (Opus fork), `25dbcd4` [GP 3.2] `grass_geometry.py` (Opus fork), `c8faa9b` [GP 3.3] the Blender script and the mesh-level tests.
  - `c650b64` and `514014f`, `f6e62fb`, `a7d3e22`: the gate review (G34) and its fixes.
  - `7c05b5c`, `991694d`, `3402e59`: the user's stages review, one continuous leaf (G35).
  - `a841819`, `55c866a`: the emergence point, split sheaths and uneven rows (G36).
  - `435d86f`, `ea8b6ad`, `aaac24f`: the pivot on the wrap wall and one GLB material (G37), then WP 3.3 adapted to G35 to G37.
  - This outcome commit. Fast-forwarded into `main`.
- **Gate:**
  - lpy plus plant pytest: 130 passed, with the Blender smoke tests run (Blender 5.2.2, re-run on 5.3.0 on 2026-11-22).
  - flake8 on `dev/lpy/decline.py` and `dev/plant/` is clean, and `node scripts/fitness.mjs` reports 0 violations.
  - A fresh export (25 samples, 51 organs) builds a GLB of 8,280 vertices, equal to the mesh, and the stages `.blend`.
  - Worst declined tip error 0.755% of the declined blade length (bound 2%); the worst overshoot of a young blade through an enclosing sheath is 0 mm (19 mm before G36).
  - The user judged the stages in Blender after G35 and G36 and approved them.
- **Mesh facts:** each leaf is one grid of 3 sheath rows plus 17 blade rows (shared collar row) by 9 vertices, 180 vertices per leaf, 8,280 per plant for seed 1 (46 lifetime leaves). Topology is identical at every sample.
- **Decline rule (replaces G23's chord factor):** the blade's bend profile is `angle * smoothstep(u / c) + arch * u^2`; `bend_cumulants` gives its mean, variance and third central moment up to s, and `chord_change` turns a blade vertex by Δ(mean - k3 / 6) and scales its offset by exp(-Δk2 / 2). All polynomial, so the GLSL port is closed form.
- **Lessons:**
  - Do not remove a worker's worktree until its fixes are done: resuming an agent whose worktree is gone fails, and a fresh fork has to re-derive its context.
  - The first stages passed every test and still looked wrong: tests checked consistency with the spec, not botany. Render close-ups of the critical spots (pseudostem tops, young leaves inside old sheaths) before handing over.
  - Several review rounds after the first wave were refinements of one WP's geometry; running them as wave-sized steps (3.1 and 3.2 in parallel, then 3.3) kept the file ownership clean.
  - glTF splits primitives by material, which duplicates shared vertices; a fixed-topology mesh needs one material.

### Carry-over to Phase 4

- **Per-stage attributes:** `grass_geometry.STAGE_ATTRIBUTES` (`pivot`, `axis`, `collar`, `stem_t`, `arch`, `sen`, `fade`, `s`) plus positions change per stage; `STATIC_ATTRIBUTES` are identical across a lifetime build (tested). Pack only the stage ones per stage.
- **Vertex order:** build with `build_plant(table, t, p, lifetime=lifetime_leaves(table))`. With one material the GLB keeps `PlantMesh` order and count (8,280); keep that check if the export format changes. Faces are quads in `PlantMesh` and triangulated by the exporter.
- **Normals** are per stage and are not transformed by `declined_vertex`; decide whether to bake them per stage or recompute in the shader.
- **Decline signature:** `declined_vertex(position, vin, time, v, p, collar_bend)`; `collar_bend` comes from `GeometryParams` and must travel with the export.
- **Stage spacing:** the G36 emergence point eases with `fade`, and blade rows are dense near emergence, so shape changes fast while a leaf unrolls and while an old leaf fades; the outline's open question on sampling `FADE_DURATION` still applies.

### Phase 4 (2026-11-22)

- **Commits on `gp-4-vat` from `1d0f3c7`:**
  - `f4cae40`: verified facts, grill outcomes (G38 to G48), the VAT format and the WPs.
  - `b0236e8` [GP 4.1] organ events and schema v3 (Opus), `88e866a` [GP 4.2] the generic VAT writer, loader, spec and fixture (Sonnet), `a31dd56` [GP 4.3] the grass bake (Opus).
  - `250615d`: the gate review (G49); `919cbd9` [GP 4.1], `c5d2f0c` [GP 4.2] and `82fc94c` [GP 4.3]: its fixes, plus `grass_vat_profile.md`.
  - This outcome commit. Fast-forwarded into `main`.
- **Gate:**
  - lpy, plant and vat pytest: 219 passed, with the Blender smoke test and the `node` round trips run (Blender 5.3.0).
  - `npm test` 144 passed (7 in `glade-vat.test.js`); flake8 on `dev/lpy/`, `dev/plant/` and `dev/vat/` clean; `node scripts/fitness.mjs` 0 violations.
  - Default bake (seed 1, `--samples 49`): 8,280 vertices, 46 organs, 49 stages, 13,984 triangles, 4.75 MB, baked in 1.5 s. Worst f16 position error 0.12 mm.
  - The preview imported into Blender stands upright (+Z to glTF +Y on the node), is double-sided, and shows the 29 leaves alive at t = 12 (17 collapsed leaves folded away).
- **Model facts:** with seed 1, 31 leaves turn senescent, 22 die and 17 collapse by t = 12; senescence durations 1.60 to 2.37 P; schema v3 leaves every v2 value unchanged (checked on 241- and 25-sample exports).
- **Lessons:**
  - Measuring before the grill (attribute domains, blend error versus N, f16 error) turned the outline's 3 MB estimate and its open fade-sampling question into concrete numbers, and showed that most "stage attributes" were per leaf.
  - The user's reuse goal arrived mid-grill and reshaped the format into a species-neutral container plus a per-species profile; the gate's architecture review was briefed with that goal and found the template gaps (frame, lifespan, meaning, versioning) while the format had no consumers.
  - A required new field in a shared dataclass breaks the next wave's adapter by design; integrating the generic fixes first and then the adapter fix kept each step green except the one known test file.

### Carry-over to Phase 5

- **VAT input:**
  - Load with `dev/vat/vat.mjs` (`parseVat`, `decodeBlock`; `raw` arrays are ready for `texImage2D`, with `f16` as `HALF_FLOAT` bits and `oct16` as int16 pairs). The grass contract is `dev/plant/grass_vat_profile.md`; check `species` `grass` and `species_version` 1.
  - Blend the two stages around `t` (model time in P; Glade's `g` maps onto `stage_times`). Senescence and fade are analytic from the `senescence` block and `FADE_DURATION` (G38); hide an organ for `t < lifespan.appear` or `t >= lifespan.vanish`. Age is `t - appear`.
  - `never` is 1e30 stored as f32. Keep blocks that can hold it in f32 when packing textures (half floats would turn it into Inf).
  - Organ-stage blocks are stage-major, so they map onto a `texelFetch(organ, stage)` texture; vertex-stage `position` and `normal` onto a per-stage layout of the implementer's choice.
  - The blend error at N = 49 is up to 11 mm (6.6 mm 95th percentile) on young blades; a smooth morph, not a pop. Judge it on the page and re-bake with another `--samples` if needed.
- **Wind renderer:** `wind-demo.html` and `dev/wind/` assume the frozen reference (`_ORGAN` 0 is the stem, a `Grass_Stalk` material, the stem's arc). The new mesh has no stem organ, one material, and tells sheath from blade by `_KIND`; the wind pivots each blade at its `collar` (emergence point on the wall) and each sheath at the tiller base.
- **GLSL port of `decline.py`:** clamp where Python raises (age at 0 is already a clamp), mirror `bend_mean`, `bend_cumulants` and `chord_change` as polynomials, pass `collar_bend` (`species_params.geometry.collar_bend`) as a uniform, and check float16 tolerances against the 2% bound if attributes are packed as half floats. Python does not transform normals; the baked normals (underside) should be turned by the decline row rotation and the lean in the shader (G42).
- **Resolution:** at close range a split old sheath shows a slightly jagged edge where the blade leaves it, and strongly arched tips show faint polygon corners; both are row-count effects, to settle with the frame-rate measurement (`GeometryParams`).

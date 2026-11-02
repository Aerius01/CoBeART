# Glade plants roadmap (GP)

Load the `orchestrate` skill before any implementation work. The user names the phase to run.

**Roadmap ID:** `GP`. Scope: the growth chain for Glade's first 3D plant, a bushy vegetative grass (no heading, no flowering), from an L-Py model to a growth slider on a dev page. Design input: the grill of 2026-11-01 (decisions P1 to P8 below) and the reference plant in `dev/assets/build_grass_plant.py`. The wind MVP (`dev/wind-demo.html`, `dev/wind/`) is a sibling prototype and not part of this roadmap; Phase 5 reuses its renderer.

**Status (2026-11-01):** roadmap written, no phase started. **Next phase base:** the tip of `main` (`2fa5d1c`); Phase 0 adds the roadmap commit on top.

## Project facts

- **Worktree base:** `.claude/settings.local.json` sets `worktree.baseRef: "head"` (verified 2026-11-01).
- **L-Py environment:** a dedicated conda env `lpy`, never `splat-env`. Create it with `conda create -n lpy openalea.lpy pytest -c openalea3 -c conda-forge`. A dry run on 2026-11-01 resolved `openalea.lpy` 3.15.4, `openalea.plantgl` 3.23.2 and Python 3.13 with the Qt viewer. Call the interpreter directly (`~/miniconda3/envs/lpy/bin/python`), never `conda run`. The GUI is `~/miniconda3/envs/lpy/bin/lpy`.
- **Blender:** Phases 3 and 4 run Python inside Blender (headless: `blender --background --python <script> -- <args>`), the way `build_grass_plant.py` does. Blender's bundled Python has no access to the `lpy` env, so the two sides meet only through files.
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

The two WPs meet at the **module interface** and the **organ-table format** below. The grill may change either, but must settle both before spawning, because WP 1.2 is written against them while WP 1.1 is still being built.

**Draft module interface** (L-Py modules and their parameters):
- `Apex(tiller, rank, t_next)`: a tiller's growing point; emits a new phytomer when `t_next` is reached.
- `Phytomer(tiller, rank)` followed by `Leaf(id, tiller, rank, age, state)`: one leaf with its sheath; `state` in `growing | mature | senescent | dead`.
- `Bud(tiller, rank, age)`: an axillary bud; becomes a first-order tiller (`Apex` with a new tiller id) after the tiller delay, while fewer than the tiller cap exist (P1, P7).
- Tiller placement: a tiller's base sits on the crown at its parent leaf's azimuth, splayed outward by a parameter angle.

**Draft organ-table format** (one JSON file per export, units per Constraints):
- Header: `schema_version`, `seed`, all model parameters, `dt`, and the sample times.
- Per sample time, a list of organs, each with a stable `id` that is the same in every sample (P6 needs it) and: `kind` (`leaf` or `tiller`), `tiller`, `rank`, `birth_time`, `age`, `state`, the attachment point and initial direction, azimuth, angle from vertical, sheath length, blade length and width, unroll fraction (0 rolled, 1 open), arch (bend), and senescence fraction (0 green, 1 dead).
- A dead leaf stays in the table with `state: dead` and zero size from its death time, so the lifetime organ list is the union of all samples.

**Open for the phase grill** (settle before spawning, record outcomes here):
1. **Verify first:** how a script reads module parameters from a derived L-string (`openalea.lpy.Lsystem`, `derive`, `AxialTree` iteration), and whether turtle frames (positions and headings) can be read per module. Under P2 every node sits at the crown, so the exporter may compute attachment points itself from parameters; decide which.
2. **Growth functions:** the shape of blade elongation over age (logistic, or linear to a cap), when the collar appears, how unroll fraction follows age, and how long senescence takes before death.
3. **Rank progressions:** how sheath length, blade length, blade width and angle from vertical change with leaf rank, mapped from the reference's `LeafSpec` list (lower leaves spread about 48 degrees, upper about 20; lengths 17 to 21 cm), with seeded jitter.
4. **Tillering rule:** the axil-to-emergence delay (recommendation: the tiller in leaf n's axil emerges with leaf n+3 on the parent), which axils may tiller, the splay angle, and a tiller's own leaf timing.
5. **End of Phase 1 time span:** how long the model runs (until all 4 tillers carry 3 live leaves, then a few phyllochrons of steady turnover) and the sample count and spacing.
6. **Skeleton preview:** what the viewer draws (sheaths as cylinders, blades as flat tapered strips, senescence as colour) and file layout under `dev/lpy/`.

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

## Phase 2: Whole-plant decline (outline)

Adds P3 step three to `grass.lpy`: after a lifetime parameter the apexes stop making leaves, every leaf ages into senescence and dies, and the clump collapses (droop grows with senescence). Extends the exporter only if the format needs a new field. Judged in the L-Py viewer. Likely one WP. Open for its grill: what triggers decline (fixed lifetime, or an external growth value later driven by Glade's energy), how fast it runs relative to growth, and whether collapse is droop only or also leaf fall.

## Phase 3: Geometry from the organ table (outline)

Refactors `build_grass_plant.py` into a generator that reads an organ table and builds the plant mesh at one sample: pseudostem sheaths, curled blades from the reference profiles driven by each organ's length, width, unroll, arch and senescence, and the wind attributes per vertex (pivots at sheath tops). Runs in Blender; viewed at a few stages there. Open for its grill: how the reference's rolled top leaf maps onto the unroll fraction, how a young leaf hidden inside its parent sheath is drawn, vertex count across blades (2 to 3 may suffice from above), and per-vertex colour or a texture coordinate for senescence.

## Phase 4: Fixed-topology VAT export (outline)

Builds the full-lifetime mesh (P6): one index buffer over every organ the plant ever has, per-stage positions plus wind pivots and axes for N stages, unborn and dead organs collapsed to their attachment point. Exports a GLB (or GLB plus a binary VAT) and a loader in `dev/` with tests that check the topology is identical across stages. Open for its grill: N and stage spacing (denser where shape changes fast, such as unrolling), texture layout and precision (float16 or float32), and the number of plant variants (seeds) and the memory budget.

## Phase 5: Growth slider on a dev page (outline)

A dev page, next to `wind-demo.html`, that draws instanced grass from the VAT with a per-instance growth value `g` and blends the two stages around it, under the top-down camera, with the wind from `dev/wind/` applied on top. Sliders for `g` (global and randomised per instance). Measure frame rate against instance count, and decide on the P6 alternative from that. Open for its grill: whether the wind renderer is extended or a new renderer is written, culling of zero-size triangles, and level of detail.

## Deferred (not in this roadmap)

- Glade integration: driving `g` from the energy-driven growth in `sim/`, replacing the sprite atlas with instanced 3D meshes (a GL roadmap decision: depth buffer, halos, draw order), and settling the on-floor plant size against about 3 mm per projector pixel.
- The analytic-shader alternative to the VAT (P6).
- Second-order tillers (P1).
- Other species, and a fixed maximum of about three hierarchy levels for branching ones.
- Hand (OptiTrack) wind and the remaining wind MVP steps (gust tuning, instance count), tracked outside this roadmap.

## Outcomes

None yet.

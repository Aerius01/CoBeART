# Glade implementation roadmap (GL)

Load the `orchestrate` skill before any implementation work. The user names the phase to run.

**Roadmap ID:** `GL`. Design source: `BRIEF.md` in this folder (sessions 1 to 6, all done). The brief is the source of truth for behaviour; this file holds the phases, work packages, decisions and outcomes. When they disagree, fix the brief first.

**Status (2026-10-25):** Phase 0 done (`ce14233`), Phase 1 in progress. **Phase 1 base:** the grill-decisions commit on `gl-1-foundation` that follows `ce14233` (its id is in Outcomes, Phase 1).

## Project facts

- **Worktree base:** `.claude/settings.local.json` sets `worktree.baseRef: "head"` (verified 2026-10-25).
- **Ports:** `3000 + 100 * phase + wp` (GL 1.2 uses 3102); 3000 is the orchestrator's. Start a hub with `PORT=<port> node server.js` from `cobeart-app/` and stop it by PID (`ss -ltnp | grep ':<port> '`).
- **Worktrees:** run `npm ci` in the worktree's `cobeart-app/` before tests or a hub. Playwright screenshots must be saved under the repo's `.playwright-mcp/`; the scratchpad is outside Playwright's allowed roots.
- **Files no WP edits:** `ROADMAP.md`, `BRIEF.md`, `CLAUDE.md`.
- **Gate commands:** run from the repo root unless noted, each capped with `timeout`:
  - `cd cobeart-app && npm test`
  - `node scripts/fitness.mjs` and `node --test scripts/fitness.test.mjs`
  - `~/miniconda3/envs/splat-env/bin/python -m pytest` and `~/miniconda3/envs/splat-env/bin/python -m flake8 cobeart tests`, only when a phase touches `config/` or Python
  - plus each WP's Must-pass list.

### Constraints every agent follows

- **Fitness rules that bite here:**
  - Nothing under `public/worlds/` may contain the socket client call, the socket library name or the hub namespace paths (rule `worlds-receive-only` in `scripts/fitness.mjs`); this includes `.md` files.
  - No literal `3000` in non-test `.js`/`.html` under `public/` (the arena/port rule). Arena bounds come from `window.__COBEART_CONFIG__.arena` in the browser and are passed in as arguments to every `sim/` and `render/` function.
- **Modules:** browser code is ES modules (`.mjs`), loaded without a bundler. `sim/` and `params.mjs` must not touch the DOM, `window` or WebGL, so `node --test` can import them. Tests are CommonJS files in `cobeart-app/test/` that `import()` the modules (pattern: `test/mouse.test.js`).
- **Code style:** modern JS, JSDoc types on every exported function and every typed-array field, no per-frame `console.log` unless behind the debug flag, and errors that name the parameter or value and the fix.
- **Parameters:** the names and units in the `BRIEF.md` parameter table are the contract. Use them verbatim as object keys.

## Phase 0: Commit the design (no WPs; runs at the start of the Phase 1 session)

`BRIEF.md` and this file are untracked, and untracked files do not exist in worktrees.

1. Create `gl-1-foundation` from `main`.
2. Commit `BRIEF.md` and `ROADMAP.md` as `[GL 0] Add the Glade brief and implementation roadmap.`
3. That commit is the Phase 1 base; record its id in the Status line.

## Phase 1: Foundation (3 parallel WPs)

Builds what every later WP depends on: the parameter contract, the state layout and the shared grid helpers. The art placeholder and the vendored panel ride along because they have no dependencies.

**Open for the phase grill** (settle before spawning, record outcomes here):
1. **Purity versus allocation.** At 100k plants, a step that allocates new typed arrays is about 3 MB of garbage per step at 60 Hz. Recommendation: **double buffering**. `step(prev, next, ...)` reads `prev`, writes only into `next` and returns it, never modifying its inputs, and the loop swaps the two.
2. **RNG.** Algorithm (recommendation: `sfc32`, seeded from `seed` through `splitmix32`) and where its state lives (inside the simulation state, so it is double-buffered with everything else).
3. **Grid sizing.** Should `validateParams` require `h` to divide both arena spans exactly (recommendation), or should the grid round up and treat the last row and column as partial?
4. **Pool capacity.** Initial capacity, growth by doubling into the next buffer, and whether a hard maximum should throw (recommendation: no hard maximum in the MVP; log the capacity when it grows).

**Grill decisions (2026-10-25):**
- **G1 Double buffering.** `step(prev, next, ...)` reads `prev`, writes only into `next` and returns it; inputs are never mutated observably. `next`'s old contents are scratch and every field a function owns is fully rewritten, so nothing stale survives a swap.
- **G2 RNG.** `sfc32`, its 4-word state expanded from `seed` by `splitmix32`, then 12 warm-up draws. Float in [0, 1) is `u32 / 2^32`. API is explicit-state and pure: read from one state, write the advanced state into another.
- **G3 RNG location.** Inside the simulation state as `rng: Uint32Array(4)`, copied by `cloneInto`, so `(prev, foot, params)` fully determines `next`; `resetState` reseeds it from `seed`.
- **G4 Grid sizing.** `validateParams` requires `h` to divide both arena x and y spans exactly; the error lists the nearby valid values. Origin `(x_min, y_min)`, `ix = floor((x - x_min) / h)`, with `x = x_max` clamped into the last cell.
- **G5 `plant_max`.** Not a `PARAMS` entry. `params.mjs` exports `PLANT_MAX = 1` (the energy unit, one grown plant). `kind` stays `live | structural`.
- **G6 Kinds.** Only `h` and `N_frames` are structural. `seed` is live and its description says it applies on reset (`R`); `dt` and `foot_role` are live.
- **G7 Placeholder defaults** for the brief's TBD values, each described as "provisional (GL 4 grill)": `r = 1/3`, `input_power = 50`, `lambda_max = 200`, `shadow_strength = 0.5`, `shadow_scale = 1.3`, `shadow_blur = 2`.
- **G8 Ranges.** The WP 1.1 agent picks `min`/`max`/`step`: wide enough to tune (about 0.1x to 10x the default for rates and coefficients), tight enough to exclude invalid values (no zero or negative rates, `dt` in [1/240, 1/15], `N_frames` in [2, 64]). Reviewed in the diff.
- **G9 Pool capacity.** Initial capacity 4,096. A write past `next`'s capacity reallocates its pool arrays at double size, copying the live prefix; `cloneInto` grows `dst` the same way. No hard maximum. The state exposes `capacity`; the sim never logs, `main.mjs` (GL 4.1) logs when it changes. Growth replaces arrays inside the state object, never the object itself.
- **G10 `cloneInto(src, dst)`** copies density, the pool prefix `[0, count)`, `count`, `nextId`, `rng` (and grows `dst` per G9). It throws when `nx`, `ny` or `h` differ, naming both shapes; structural changes go through `createState`/`resetState`. Slots past `count` are left as is.

### WP 1.1: Parameters, state and grid helpers

- **Model:** Opus (fresh agent or fork; the shape decisions from the grill matter).
- **Owns:**
  - `glade/params.mjs`, `glade/sim/state.mjs`, `glade/sim/rng.mjs`
  - `cobeart-app/test/glade-params.test.js`, `cobeart-app/test/glade-state.test.js`
- **Scope:**
  - **`params.mjs`:**
    - `PARAMS`: one metadata entry per parameter in the brief table, with `{ key, type: 'number' | 'int' | 'enum', default, min, max, step, unit, kind: 'live' | 'structural', options?, description }`.
    - `foot_role` is an enum over the contract roles. Read the enum from `contract/frame.schema.json` `$defs/role` at test time to check it; the module holds a literal copy, since the browser cannot read the contract.
    - `h` and `N_frames` are structural.
    - `defaultParams()` returns a frozen object.
    - `validateParams(p, arena)` throws `GladeParamError` with `param`, `value` and an actionable message. It checks range and type per entry, plus these cross-parameter rules:
      - `E_spawn_min < E_cap`
      - `E_spawn_min * h² (m²) >= seed_energy`, so ground at the threshold can pay for a seed
      - `min_spacing_mm <= h`, so the 3x3 neighbour check is enough
      - `0 <= delta < 0.5`
      - `max_steps_per_frame >= 1`
      - the grid-sizing rule from the grill
  - **`sim/state.mjs`:**
    - `createState(params, arena)`: an empty grid (`nx`, `ny`, `h`, origin, `density: Float32Array`) and an empty plant pool in struct-of-arrays layout (`x`, `y`, `energy`: Float32; `id`, `seed`: Uint32; `frame`: Uint16; `direction`: Uint8), plus `count`, `nextId` and the RNG state.
    - Helpers: `cellOf(x, y)`, `bilinear(x, y)` (four cell indices and weights, clamped at the reflecting edge), and `cloneInto(src, dst)` for the double buffer.
    - Pool order is ascending `id` by construction: appends at the end, and compaction preserves order. Later WPs rely on this for oldest-first allocation and draw order.
  - **`sim/rng.mjs`:** the grilled algorithm with explicit state.
- **Must pass:**
  - `npm test` green, including new tests for: every default validates; every documented cross rule throws with the parameter named; `bilinear` weights sum to 1 and stay inside the grid at the corners; the same seed gives the same sequence.
  - `node scripts/fitness.mjs` clean.

### WP 1.2: Placeholder atlas

- **Model:** Sonnet (fresh agent; fully specified).
- **Owns:** `glade/render/atlas.mjs`, `glade/dev/atlas-preview.html`.
- **Scope:**
  - **`generateAtlas({ nFrames, cellPx })`** returns `{ canvas, cols, rows, cellPx, gutterPx }`, drawn with Canvas 2D (`OffscreenCanvas` when available).
  - **Layout:** row 0 is the growing set and row 1 the declining set, each with frames `0..nFrames-1`. Each cell has a transparent gutter of at least `cellPx / 8`, so the coarser mipmaps the halo samples do not bleed between cells.
  - **Art:** a top-down rosette (blades or petals radiating from the centre) whose count and length grow with the frame index. Frame 0 of both sets is a barely visible speck. The declining frame `k` has the same silhouette as growing frame `k`, with colour lerped from green to straw and slightly drooped (shorter) blades.
  - **Colours** are straight (non-premultiplied) alpha; premultiplication happens at upload in GL 3.2.
  - Throws if `cols * (cellPx + gutter)` exceeds 4096 px, naming `N_frames` and `cellPx`.
  - **`dev/atlas-preview.html`** shows the atlas at `nFrames = 30` on a black background.
- **Must pass:**
  - With a hub on port 3102, Playwright opens `/worlds/glade/dev/atlas-preview.html`. The screenshot shows 2 rows of 30 cells: growth left to right, the declining row browner, frame 0 near-empty. The console is clean.
  - `node scripts/fitness.mjs` clean.

### WP 1.3: Vendored panel library and mouse defaults

- **Model:** Sonnet (fresh agent; mechanical).
- **Owns:**
  - `glade/vendor/lil-gui.esm.min.js`, `glade/vendor/LICENSE`
  - `config/cobeart.yaml` (the `sources.mouse` block only)
  - `cobeart-app/test/config.test.js` (the mouse assertion at line 37 only)
  - `cobeart/settings/tests/test_config.py` (the `MouseSourceSettings` expectation at line 46 only; found in the Phase 1 baseline check)
- **Scope:**
  - Vendor the latest stable lil-gui ESM build, at least two weeks old, fetched from the npm registry (for example `npm pack lil-gui@<version>` in the scratchpad). It is not added to `package.json`. Record the version in a header comment.
  - Copy lil-gui's MIT licence to `glade/vendor/LICENSE`.
  - Set `sources.mouse.z_initial_mm: 100` and `z_step_mm: 20`, keep `role: right_hand`, update the YAML comments if they cite values, and update both test assertions (Node and Python) to match.
- **Must pass:**
  - `npm test` and `python -m pytest` green.
  - `node -e "import('./cobeart-app/public/worlds/glade/vendor/lil-gui.esm.min.js')"` fails only for a missing `document` (it parses).
  - `node scripts/fitness.mjs` clean (the vendored file must not match the socket patterns; if it does, stop and report).

**Phase 1 gate:** the gate commands, including pytest and flake8, because the YAML changed.

## Phase 2: Simulation (2 parallel WPs)

Uses the Phase 1 state layout. Both WPs are pure modules with tests; neither touches the DOM.

**Open for the phase grill:** the exact function signatures under the double-buffer decision, and whether `field.mjs` exposes a `drawAt(grid, x, y, amount)` helper (bilinear withdrawal that returns what was actually taken) for plants to use, or plants implement withdrawal through `state.mjs` `bilinear` themselves.

### WP 2.1: Ground field

- **Model:** Opus (numerics).
- **Owns:** `glade/sim/field.mjs`, `cobeart-app/test/glade-field.test.js`.
- **Scope:** brief sections 1 and 2.
  - **Foot deposition:** sphere kernel `(1 - rho²)²`. It is normalised once at z = 0 so a planted foot injects `input_power` in total, and renormalised over inside cells near the arena edge. It handles an absent foot (`null`).
  - **Operators:** diffusion and spill as conservative 9-point (2 : 1) edge fluxes with reflecting boundaries. Each runs `ceil(3k)` substeps, where `k = D * dt / h²`.
  - **Spill** is the same operator applied to `max(0, E - E_cap)` with `D_spill`.
  - Densities are in E/m² throughout.
- **Must pass:**
  - `npm test` green, covering:
    - deposition totals `input_power * dt` at z = 0, less when lifted, and nothing at `z >= R`
    - edge renormalisation keeps the total
    - diffusion and spill conserve total energy to 1e-6 relative
    - no flux leaves the grid
    - no negative density at `D_spill` = 1e6
    - a saturated blob's above-cap region expands under spill
    - `h` = 25 and `h` = 50 give matching totals and radial profiles within tolerance
  - `node scripts/fitness.mjs` clean.

### WP 2.2: Plants

- **Model:** Opus (allocation and lifecycle semantics).
- **Owns:** `glade/sim/plants.mjs`, `cobeart-app/test/glade-plants.test.js`.
- **Scope:** brief sections 3 and 4.
  - **Feeding:** constant demand `r * dt` drawn bilinearly, oldest first. The pool is id-ordered, so pool order is the allocation order. Below full, the plant stores what it gets; at full, it consumes it; when short, it pays the shortfall from itself.
  - **Removal:** at `energy <= 0`, by order-preserving compaction.
  - **Frames:** hysteresis with `delta`; the direction comes from the last frame change.
  - **Spawning:** after feeding, per cell in fixed order. Linear ramp from `E_spawn_min` to `E_cap`, with `p = 1 - exp(-lambda * h² * dt)`. Uniform position in the cell, `min_spacing_mm` rejection through the 3x3 cells, and `seed_energy` paid bilinearly or no spawn. New plants are appended with `nextId++`.
- **Must pass:**
  - `npm test` green, covering:
    - a fed plant reaches 1 in `1/r` and holds
    - on empty ground it withers to removal in `1/r`
    - under shortage the oldest plants are fed first
    - frames do not flicker for energy oscillating within `delta` of a boundary
    - a declining plant fed again switches direction to up
    - spawn rate statistics match `lambda` within tolerance over many steps
    - spacing is never violated
    - energy is conserved across feeding and spawning, apart from consumption by full plants
    - same seed, same result
  - `node scripts/fitness.mjs` clean.

**Phase 2 gate:** the gate commands.

## Phase 3: Step and renderer (2 parallel WPs)

**Open for the phase grill:**
- the instance buffer layout and how the renderer reads the pool (direct typed-array upload versus packing)
- heatmap texture format: `R32F` is not filterable without `OES_texture_float_linear`, so `texelFetch` with manual bilinear, or `R16F`
- the renderer's public API

### WP 3.1: Simulation step

- **Model:** Opus (integration semantics and emergent-behaviour tests).
- **Owns:** `glade/sim/step.mjs`, `cobeart-app/test/glade-step.test.js`.
- **Scope:**
  - `step(prev, next, foot, params, arena)` runs, in order: deposit, diffuse, spill, feeding, spawning, frames.
  - `resolveFoot(sighting, nowMs, params)` turns the latest sighting (body plus receipt time) into a foot or `null` per `foot_timeout_ms`.
  - `resetState(params, arena)` creates a fresh state from `seed`.
- **Must pass:**
  - `npm test` green, covering:
    - multi-step energy accounting: total ground plus plants equals initial plus deposit minus consumption
    - the same seed and input give identical states
    - rescaling all energy parameters by 10 gives the same plant positions and frames
    - a foot standing still for 20 s grows a glade whose radius keeps increasing after the core saturates
    - a foot moving at walking speed leaves no cell above `E_cap`
    - after the foot leaves, plants die youngest first and the pool eventually empties
  - `node scripts/fitness.mjs` clean.

### WP 3.2: WebGL2 renderer

- **Model:** Opus, agent type `glsl-shader-expert`.
- **Owns:** `glade/render/renderer.mjs`, `glade/render/shaders.mjs`, `glade/dev/render-demo.html`.
- **Scope:** brief "Rendering".
  - **API:** `createRenderer(canvas, arena, atlas)` returns `{ render(state, params, { heatmap }), setAtlas(atlas), resize(), dispose() }`.
  - **Mapping:** stretch to fill per D18: screen x from `x_max` (left) to `x_min`, screen y from `y_min` (top) to `y_max`.
  - **Draw:** one instanced draw with 12 vertices per instance (halo quad, then sprite quad), drawn newest first (reverse pool order), premultiplied blending `(ONE, ONE_MINUS_SRC_ALPHA)`, and the atlas uploaded premultiplied with mipmaps.
  - **Per instance:** rotation, size and brightness jitter from `seed`; the atlas cell from `(frame, direction)`.
  - **Halo:** sprite alpha at `shadow_scale` with mip bias `shadow_blur`, black at `shadow_strength`, offset by `shadow_offset_mm`.
  - **Heatmap** under the plants: a ramp from 0 to `E_cap`, plus an overshoot tint.
  - **`dev/render-demo.html`** builds a synthetic state with `sim/state.mjs` (dense overlapping plants at mixed frames and directions, plus a density blob) and renders it with the GL 1.2 atlas.
- **Must pass:**
  - With a hub on port 3302, Playwright screenshots of the demo show:
    - plants at the expected screen positions for known arena coordinates (one plant at each arena corner)
    - visible darkening only where plants overlap
    - the heatmap toggled on and off
  - The console is clean (no GL errors).
  - `node scripts/fitness.mjs` clean.

**Phase 3 gate:** the gate commands.

## Phase 4: Runtime (1 WP)

**Open for the phase grill:** starting values for the parameters still marked TBD (`input_power`, `r`, `lambda_max`, `shadow_strength`, `shadow_scale`, `shadow_blur`). The orchestrator proposes values derived from target behaviours, for example "a planted foot saturates its footprint in about 2 s" and "a plant grows in about 3 s", and the user picks.

### WP 4.1: World page, loop, panel and keys

- **Model:** Opus.
- **Owns:** `glade/index.html`, `glade/main.mjs`, `glade/panel.mjs`. Adding `index.html` makes Glade appear in the launcher.
- **Scope:** brief "Runtime".
  - **Messages:** listen for `frame` postMessages and store the `foot_role` sighting with `performance.now()`; ignore `audio`.
  - **Loop:** the fixed-step accumulator with `max_steps_per_frame`, logging a rate-limited warning with the dropped ms. Double-buffer swap per step, then render the latest state.
  - **Panel:** lil-gui built from `PARAMS`, with structural entries marked. A structural edit validates and then resets; a live edit validates, and an invalid value is rejected with the error shown in the panel and the console.
  - **Keys:** `E` heatmap, `T` panel, `D` dump (JSON to the console and the clipboard), `R` reset. Log the seed at start.
  - Grill-decided defaults are written into `params.mjs` **only through the orchestrator** (a one-line follow-up commit), not by this WP.
- **Must pass:**
  - With a hub on port 3401, Playwright:
    - `/worlds.json` lists `glade`
    - on `/host/?world=glade&source=mouse`, holding a button and moving grows plants within a few seconds (screenshot)
    - releasing the button stops growth, and the plants later wither and vanish
    - `E` shows the heatmap
    - `M` then `T` opens a clickable panel
    - `R` clears the glade
    - `D` logs valid JSON that passes `validateParams`
    - the console is clean, apart from the seed and capacity log lines
  - `npm test` green; `node scripts/fitness.mjs` clean.

**Phase 4 gate:** the gate commands.

**Phase 4 close-out (outside the WP):**
- Update `CLAUDE.md`: Glade alongside Splat in the worlds list, its keys, and home testing with the mouse.
- Human check: live tuning in the Electron window on port 3000 with the mouse source. This is where the TBD parameters get their real values.

## Deferred (from the brief, not in this roadmap)

- Wilt colour bridging, the final `N_frames`, hand-drawn atlas loading, wind.
- Both feet, more species, audio.
- Ellipsoid foot field, ground half-life.
- Target plant count and performance budget, once tuned on the show machine.

## Outcomes

One subsection per phase: commit ids, test results, grill decisions, lessons, and the next phase's base commit.

### Phase 0

`ce14233` on `gl-1-foundation` (from `main` at `704fc63`): BRIEF.md and ROADMAP.md committed. The Phase 1 baseline check added `cobeart/settings/tests/test_config.py` to WP 1.3's ownership (it also pins the mouse defaults).

### Phase 1

Not run.

### Phase 2

Not run.

### Phase 3

Not run.

### Phase 4

Not run.

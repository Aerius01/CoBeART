# Glade implementation roadmap (GL)

Load the `orchestrate` skill before any implementation work. The user names the phase to run.

**Roadmap ID:** `GL`. Design source: `BRIEF.md` in this folder (sessions 1 to 6, all done). The brief is the source of truth for behaviour; this file holds the phases, work packages, decisions and outcomes. When they disagree, fix the brief first.

**Status (2026-10-26):** Phases 0 to 2 merged to `main`. Phase 3 in progress on `gl-3-step-render` (from `main` at `cab6f5b`).

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

**Open for the phase grill:**
- the exact function signatures under the double-buffer decision, and whether `field.mjs` exposes a `drawAt(grid, x, y, amount)` helper (bilinear withdrawal that returns what was actually taken) for plants to use, or plants implement withdrawal through `state.mjs` `bilinear` themselves
- **Scratch buffers** (from the GL 1 review): diffusion/spill need an nx·ny ping-pong and an excess buffer; spawning needs a cell-to-plant bucket index (e.g. `cellStart: Int32Array(nx*ny+1)`, `cellItems: Int32Array(capacity)`) that includes plants spawned earlier in the same step; WP 3.1 needs per-step accounting (deposited, consumed). Recommendation: a separate `Scratch` object from `createScratch(grid, capacity)`, grown with the pool, not part of the double-buffered state. Who owns `createScratch` (a WP 2.x file, or an extension of `state.mjs` ownership)?
- **Float32 versus Float64 density** (from the GL 1 review): WP 2.1 asks for conservation to 1e-6 relative over up to ~21 substeps; Float32 rounding may exceed that. Options: Float64 density plus a Float32 copy at heatmap upload, or Float32 with float64 accumulation and per-step tolerances. Settle before the tests are written; changing the density type touches `state.mjs`.

**Carry-over from Phase 1 (API as built):**
- `state.mjs`: `createState(params, arena)`, `ensureCapacity(state, needed, live)` (copies `[0, live)`, because `next.count` is stale while a step fills `next`), `cloneInto(src, dst)`, `cellOf(grid, x, y)` (returns `iy * nx + ix`), `createBilinear()` and `bilinear(grid, x, y, out)` with `out` required. Grid is `{ nx, ny, h, x0, y0, density }`; `DIRECTION = { UP: 0, DOWN: 1 }` is also the atlas row; `INITIAL_CAPACITY = 4096`.
- `rng.mjs`: `seedRng(seed, out)`, `nextU32(in, out)`, `nextFloat(in, out)`; `in` and `out` may be the same array (`next.rng` after `cloneInto`).
- Pool positions are Float32: derive a plant's cell from the stored (`Math.fround`ed) value, never from the double used at spawn, or the bucket index and later `cellOf(pool.x[i])` can disagree at a cell edge.
- `id`/`nextId` are Uint32 (wrap after 2^32 spawns, days at the maximum rate); accepted for the MVP.

**Grill decisions (2026-10-26):**
- **G11 Copy, then in place.** `step` (GL 3.1) runs `cloneInto(prev, next)` first; every operator then mutates only the grid or state it is handed (always `next`), so `next.count` is valid throughout. Pinned API:
  - `field.mjs`: `createFieldScratch(grid)`, `deposit(grid, foot, params, scratch) -> E deposited`, `diffuse(grid, params, scratch)`, `spill(grid, params, scratch)`, `totalEnergy(grid) -> E` (density sum times cell area). `foot` is `{ x, y, z }` in mm or `null`.
  - `plants.mjs`: `createPlantScratch(grid, capacity)`, `drawAt(grid, x, y, amount, bil) -> E taken`, `feed(state, params, scratch) -> E consumed`, `spawn(state, params, scratch) -> spawned count`, `updateFrames(state, params)`.
  - Implementations may add exports (e.g. `substeps(D, dt, h)`), but must not change these signatures.
- **G12 `drawAt` in `plants.mjs`.** Bilinear withdrawal: cell `i` is asked for `w_i * amount` and gives `min(ask, density_i * area)`; a short cell is not made up by the others.
- **G13 Scratch per module**, not double-buffered: field scratch holds Float64 nx·ny buffers; plant scratch holds the cell-bucket index (built from the stored Float32 positions) plus a per-cell "spawned this step" slot so spacing sees same-step spawns, and grows with the pool. Accounting is via return values.
- **G14 Float64 density** (`[GL 2]` commit to `state.mjs`); plant energy stays Float32. The Phase 3 heatmap converts at upload.
- **G15 Analytic deposit.** Total `input_power * dt * (1 - z²/R²)³` (the kernel's exact integral relative to z = 0, zero for `z >= R`), spread over the in-grid cells by `f(centre) / Σ inside f`. One rule covers lifting, the edge and h-independence. When no cell centre lies inside the footprint, nothing is deposited and the return is 0. **Amended at the gate review:** the edge boost `fullSum / insideSum` is capped at 4 (the corner case), so a foot mostly outside the arena fades continuously to 0 instead of dumping the whole total into the last few cells; on the floor the cap never binds. `deposit` throws on a non-finite foot coordinate.
- **G16 Isotropic 4 : 1 stencil** for both operators: per substep an orthogonal edge carries `(2k/3)·Δ` and a diagonal edge `(k/6)·Δ`, `k = D·(dt/n)/h²` with `n = ceil(10·D·dt/(3·h²))`, so k ≤ 0.3 (h in mm, D in mm²/s). First decided as Oono-Puri 2 : 1 per the brief; WP 2.1 measured +0.83% fourth-moment anisotropy for 2 : 1 against ~0 for 4 : 1, and the user switched (brief updated). Spill uses the excess `max(0, ρ - E_cap)` frozen at the start of each substep, so a cell never loses more than its excess.
- **G17 Seed payment by the spawning cell only** (brief updated): the candidate's own cell must hold `seed_energy`, or there is no spawn and no charge. Bilinear payment would refuse most fringe spawns next to empty cells. Feeding stays bilinear.
- **G18 Consumption.** A short plant's energy clamps at 0; consumption is exactly what leaves the system: `x + min(energy, shortfall)` when short, the demand when full, and the overflow above `PLANT_MAX` when nearly full. Computed from the stored Float32 values so the books close to float64 precision. `feed` removes plants at `energy <= 0` by order-preserving compaction.
- **G19 Frames** loop the hysteresis rule until stable (several frames per step are possible at extreme settings), clamped to `[0, N_frames - 1]`; direction from the last change. New plants start at frame 0, `UP`, energy `seed_energy`, `seed` from `nextU32`.

### WP 2.1: Ground field

- **Model:** Opus (numerics).
- **Owns:** `glade/sim/field.mjs`, `cobeart-app/test/glade-field.test.js`.
- **Scope:** brief sections 1 and 2.
  - **Foot deposition:** sphere kernel `(1 - rho²)²`. Analytic total and inside-cell distribution per G15 (amended: edge boost capped at 4). It handles an absent foot (`null`).
  - **Operators:** diffusion and spill as conservative 9-point (4 : 1, G16) edge fluxes with reflecting boundaries. Each runs `ceil(10k/3)` substeps, where `k = D * dt / h²`.
  - **Spill** is the same operator applied to `max(0, E - E_cap)` with `D_spill`.
  - Densities are in E/m² throughout.
- **Must pass:**
  - `npm test` green, covering:
    - deposition totals `input_power * dt` at z = 0, less when lifted, and nothing at `z >= R`
    - edge renormalisation keeps the total (on the floor; G15 cap outside)
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
  - **Spawning:** after feeding, per cell in fixed order. Linear ramp from `E_spawn_min` to `E_cap`, with `p = 1 - exp(-lambda * h² * dt)`. Uniform position in the cell, `min_spacing_mm` rejection through the 3x3 cells, and `seed_energy` paid by the spawning cell or no spawn (G17). New plants are appended with `nextId++`.
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

**Baseline (verified 2026-10-26):** `main` = `origin/main` = `cab6f5b`; `npm test` 114 pass, fitness 0 violations, `fitness.test.mjs` 16 pass. The hub injects `window.__COBEART_CONFIG__` into pages under `/worlds/glade/dev/`. The Playwright MCP browser is **Firefox** (Electron is Chromium): it supports WebGL2, `R16F`/`R32F` uploads from FLOAT data and a 96x96x60 `TEXTURE_2D_ARRAY` with mipmaps, and prints its own WebGL performance warnings.

**Measured at the defaults** (orchestrator script with the Phase 2 operators; foot at the arena centre, z = 100 mm, 20 s): above-cap cells for about 5 s, then the plants drain the ground and settle near 100 plants (about `input_power * lift / r`) with the spawnable radius shrinking from 414 to about 250 mm. No expanding glade. With `input_power` 200 and `lambda_max` 20 the spawnable radius keeps growing (965 mm at 10 s, 1,188 mm at 20 s) with the core above the cap. Input to the Phase 4 tuning, not a reason to change defaults now.

**Grill decisions (2026-10-26):**
- **G20 No rescale test.** With `PLANT_MAX` = 1 (G5), multiplying the energy parameters by 10 is a different world, not a unit change; rescaling E is a code change. WP 3.1 instead tests step-level h-independence: h = 25 and h = 50 give matching total ground energy and plant count within tolerance.
- **G21 Step API.**
  - `createStepScratch(grid, capacity) -> { field, plants }`.
  - `step(prev, next, foot, params, scratch) -> { deposited, consumed, spawned }`: `cloneInto(prev, next)`, then deposit, diffuse, spill, feed, spawn, frames on `next`. No `arena` (the grid carries it). Amends G1: the caller already holds `next`, so the step returns its accounting.
  - `resetState(params, arena) -> { a, b, scratch }`: two fresh states seeded from `seed` plus the scratch; used at start, on `R` and after a structural edit.
  - `totalPlantEnergy(state) -> E`.
  - `resolveFoot(sighting, nowMs, params) -> Foot`, with `sighting` `{ x, y, z, receivedMs } | null`; `null` when `nowMs - receivedMs > foot_timeout_ms` (receipt clock, brief "Runtime").
- **G22 Emergent tests pin their own values** for every parameter the behaviour depends on, so tuning the defaults in Phase 4 cannot break CI. They test the mechanism, not the look. The worker reports which behaviours the defaults show.
- **G23 Atlas as a `TEXTURE_2D_ARRAY`**, one layer per picture, layer `direction * N_frames + frame`, cut out of the `generateAtlas` canvas (art square only, no gutter), premultiplied at upload, full mip chain per layer. No bleed at any LOD; `atlas.mjs` and the `shadow_blur` range are unchanged. A heavily blurred halo is clipped at its layer's square (accepted).
- **G24 Instance data** (orchestrator decision): each frame one O(n) pass packs the pool in reverse (newest first, so the oldest draw on top) into a persistent 16-byte-per-instance staging buffer grown by doubling: `x`, `y` as f32, `seed` as u32, `frame | direction << 16` as u32; `bufferSubData`, integers through `vertexAttribIPointer`; an integer hash of `seed` in the shader gives rotation, size and brightness. `state.plants.*` is read every frame, never cached (`ensureCapacity` replaces the arrays).
- **G25 Heatmap blocky:** `R32F` with `NEAREST` (each cell a flat square, as the model stores it), from a persistent Float32 staging array. Ramp 0 to `E_cap`, separate tint above the cap.
- **G26 Halo offset direction:** screen bottom right (light from the top left), i.e. arena `(-1, +1) / √2 * shadow_offset_mm` under D18; negative flips it.
- **G27 Renderer details:** throws a named error without WebGL2; drawing buffer = client size x `devicePixelRatio`; sprite size maps through the independent x and y scales (oval on a non-square monitor, accepted); brightness jitter multiplies RGB only; no context-loss recovery, but `webglcontextlost` logs a `console.error` telling the operator to reload.
- **G28 Draw order stays by age** (decided at the gate, after the user noticed young-looking plants over older ones in the demo, whose synthetic sizes ignored age; the demo now assigns sizes that fall with id). Oldest on top, never reshuffled. Known case: a wilting old plant shrinks but stays above larger young neighbours. The alternative, ordering by current frame, pops layers whenever two overlapping plants cross sizes. Revisit in tuning if the wilting case shows.

### WP 3.1: Simulation step

- **Model:** Opus (integration semantics and emergent-behaviour tests). Fork.
- **Owns:** `glade/sim/step.mjs`, `cobeart-app/test/glade-step.test.js`.
- **Scope:** the G21 API.
- **Must pass:**
  - `npm test` green, covering:
    - multi-step energy accounting: total ground plus plants equals initial plus deposit minus consumption
    - the same seed and input give identical states
    - h = 25 and h = 50 give matching total ground energy and plant count (G20)
    - a foot standing still for 20 s grows a glade whose radius keeps increasing after the core saturates (pinned values, G22)
    - a foot moving at walking speed leaves no cell above `E_cap`
    - after the foot leaves, plants die youngest first and the pool eventually empties
    - `resolveFoot` returns `null` past `foot_timeout_ms` and for no sighting
  - `node scripts/fitness.mjs` clean.

### WP 3.2: WebGL2 renderer

- **Model:** Opus, agent type `glsl-shader-expert`. Fresh agent.
- **Owns:** `glade/render/renderer.mjs`, `glade/render/shaders.mjs`, `glade/dev/render-demo.html`.
- **Scope:** brief "Rendering", with G23 to G27.
  - **API:** `createRenderer(canvas, arena, atlas)` returns `{ render(state, params, { heatmap }), setAtlas(atlas), resize(), dispose() }`.
  - **Mapping:** stretch to fill per D18: screen x from `x_max` (left) to `x_min`, screen y from `y_min` (top) to `y_max`.
  - **Draw:** one instanced draw with 12 vertices per instance (halo quad, then sprite quad), drawn newest first (G24), premultiplied blending `(ONE, ONE_MINUS_SRC_ALPHA)`, the atlas as a premultiplied, mipmapped texture array (G23).
  - **Per instance:** rotation, size and brightness jitter from `seed`; the layer from `(frame, direction)`.
  - **Halo:** sprite alpha at `shadow_scale` with mip bias `shadow_blur`, black at `shadow_strength`, offset by `shadow_offset_mm` (G26).
  - **Heatmap** under the plants (G25).
  - **`dev/render-demo.html`** builds a synthetic state with `sim/state.mjs` (dense overlapping plants at mixed frames and directions, plus a density blob) and renders it with the GL 1.2 atlas.
- **Must pass:**
  - With a hub on port 3302, Playwright screenshots of the demo show:
    - plants at the expected screen positions for known arena coordinates (one plant at each arena corner)
    - visible darkening only where plants overlap
    - the heatmap toggled on and off
  - The console has no errors and no warnings caused by the renderer (Firefox's own driver notices are listed in the report, not hidden).
  - `node scripts/fitness.mjs` clean.

**Phase 3 gate:** the gate commands.

## Phase 4: Runtime (1 WP)

**Open for the phase grill:**
- **Substep budget** (GL 2 review): diffusion/spill cost scales with `substeps * nx * ny`, i.e. `1/h⁴`; at `h` = 10, `D_spill` = 1e6, `dt` = 1/15 (all in range) one step needs ~2,200 substeps over 360k cells and freezes the page from a slider. Spill now relaxes only the above-cap bounding box, which fixes small hot spots but not a wide saturated area. Decide a `validateParams` budget (e.g. `substeps(D_spill, dt, h) <= 32` and the same for `D`) with an error naming `D_spill`, `h` and `dt`; that is a `params.mjs` change for WP 4.1 or an orchestrator commit.
- At the current defaults a planted foot does not grow an expanding glade (Phase 3 measurement above); the tuning decides whether that changes.
- Starting values for the parameters still marked TBD (`input_power`, `r`, `lambda_max`, `shadow_strength`, `shadow_scale`, `shadow_blur`). The orchestrator proposes values derived from target behaviours, for example "a planted foot saturates its footprint in about 2 s" and "a plant grows in about 3 s", and the user picks.

### WP 4.1: World page, loop, panel and keys

- **Model:** Opus.
- **Owns:** `glade/index.html`, `glade/main.mjs`, `glade/panel.mjs`. Adding `index.html` makes Glade appear in the launcher.
- **Scope:** brief "Runtime".
  - **Messages:** listen for `frame` postMessages and store the `foot_role` sighting with `performance.now()`; ignore `audio`.
  - **Loop:** the fixed-step accumulator with `max_steps_per_frame`, logging a rate-limited warning with the dropped ms. Double-buffer swap per step, then render the latest state.
  - **Panel:** lil-gui built from `PARAMS`, with structural entries marked. A structural edit validates and then resets; a live edit validates, and an invalid value is rejected with the error shown in the panel and the console.
  - **Keys:** `E` heatmap, `T` panel, `D` dump (JSON to the console and the clipboard), `R` reset. Log the seed at start.
  - **Atlas size:** pick `cellPx = min(desired, maxCellPx(N_frames))` from `render/atlas.mjs` before a structural `N_frames` edit regenerates the atlas, since `N_frames` up to 64 is valid but a fixed cellPx is not.
  - lil-gui snaps edited values to `step`, so `dt` (1/60) and `r` (1/3) change slightly once touched in the panel; acceptable, but the D dump then differs from the defaults.
  - Grill-decided defaults are written into `params.mjs` **only through the orchestrator** (a one-line follow-up commit), not by this WP.
- **Carry-over from Phase 3 (API as built and traps from the gate review):**
  - Loop per step: `const stats = step(a, b, resolveFoot(sighting, performance.now(), params), params, scratch); [a, b] = [b, a];` then `renderer.render(a, params, { heatmap })`. Log capacity growth (G9) by comparing `a.capacity` before and after a step.
  - `R` and an `h` edit: replace `{ a, b, scratch }` with `resetState(params, arena)` (it validates first). The renderer rebuilds its density texture when `nx`/`ny` change, and throws if the state's grid does not cover its arena.
  - An `N_frames` edit, in one synchronous handler: validate a candidate params object, `generateAtlas({ nFrames, cellPx: min(desired, maxCellPx(n)) })`, `renderer.setAtlas(atlas)`, commit `N_frames`, `resetState`. `render` throws when `params.N_frames` differs from the atlas.
  - **lil-gui writes into its bound object on every drag tick.** Bind the panel to a candidate copy and commit to the live `params` only after `validateParams`; commit structural keys on finish. Binding straight to `params` would make `render` throw every frame mid-drag.
  - The renderer does not watch for resizes: call `renderer.resize()` from a `ResizeObserver` (catches devicePixelRatio changes across monitors too).
  - `shadow_strength` 0 skips the halo quads; `dispose()` makes later calls throw.
  - Not done, measure first: skipping the instance upload on frames without a step (0.6 ms pack and 1.6 MB upload at 100k plants), and the redundant clear under the opaque heatmap.
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

**Commits** on `gl-1-foundation`: `984ada5` grill decisions (the Phase 1 base), `29f0087` GL 1.1, `701dde3` GL 1.2, `36f507e` GL 1.3, then the `[GL 1]` outcomes commit (this text).

**Grill decisions:** G1 to G10 above.

**Gate review** (code-optimizer-reviewer and system-architect on `984ada5..` the WP head). Fixed by amending the owning WP:
- GL 1.1:
  - `ensureCapacity` copied `[0, state.count)`, which is stale while a step fills `next`, so growth lost plants; it now takes `live` explicitly.
  - A NaN printed as `null` in `GladeParamError`.
  - `cloneInto` ignored the grid origin.
  - `bilinear` hid a per-call allocation when `out` was omitted; `out` is now required.
  - `GladeParams` is now an explicit typedef.
  - Ranges: `E_spawn_min` min is 1 and `foot_timeout_ms` min is 50.
  - `h` suggestions come only from values the slider can reach.
  - Stronger tests: bounds in isolation, a non-square arena, a 1xN grid, seed bounds.
- GL 1.2: `N_frames` up to 64 is valid but a fixed cellPx overflowed 4096 px; `maxCellPx(nFrames)` is exported and tested (`test/glade-atlas.test.js`, ownership extended).

Deferred items went to the Phase 2 and Phase 3 grill lists and the WP 4.1 scope.

**Gate results:**
- `npm test`: 83 pass.
- Fitness: 0 violations, and `fitness.test.mjs` 16 pass.
- flake8: clean.
- pytest: 243 pass on 3 consecutive full runs.

**Known flake (pre-existing, not this phase):** `tests/e2e/test_motion_pipeline.py::test_body_that_stops_reaches_zero_norm_velocity` failed in 2 of 7 full pytest runs, once while reviewers loaded the machine. It passes in isolation, and nothing on its path reads the changed mouse config. Tracked in the repo-root `TODO.md`.

**Notes:**
- The vendored lil-gui 0.21.0 matches the npm registry integrity (sha512); `dist/lil-gui.esm.min.js` sha256 is `609142ae...a954d`. The package's licence file is `LICENSE.md`, copied as `vendor/LICENSE`.
- Splat side effect of the mouse defaults, accepted by the brief: the mouse now starts at z 100 (near Splat's largest splat radius) and needs many more wheel notches to reach its radius knee. This is not a regression.
- `atlas-preview.html` renders 3300 px wide; use a viewport of about 1700 px for a screenshot of all 30 cells.

**Lessons:**
- A worktree check found a second test pinning the mouse defaults (the Python one). Grep for every consumer of a changed config value when assigning ownership.
- Under double buffering, any helper that reads `state.count` from the output buffer is suspect; pass live lengths explicitly.

### Phase 2

**Commits** on `gl-2-simulation` (from `main` at `81de254`):
- `8075dac`: grill decisions G11 to G19, and Float64 density in `state.mjs` (G14). This is the WP base.
- `bee01a8`: G16 switched to the 4 : 1 stencil.
- `ad657b5`: GL 2.1.
- `01af17d`: GL 2.2.
- `42d96f0`: the G15 edge cap and the deferred review items.
- Then the `[GL 2]` outcomes commit (this text).

**Grill decisions:** G11 to G19 above. Two were amended mid-phase on evidence from the WPs:
- **G16:** 2 : 1 measured +0.83% fourth-moment anisotropy, so the stencil is now 4 : 1, with k ≤ 0.3 and `n = ceil(10k/3)`.
- **G15:** the edge boost is capped at 4.

**API as built** (adds to the G11 pin):
- `field.mjs` also exports `substeps`, `K_MAX = 0.3` and `EDGE_BOOST_MAX = 4`.
- `deposit` returns the energy actually deposited, which is less than the total only beyond the arena edge, and throws on a non-finite foot.
- Both scratches carry `nx`/`ny` and their operators throw on a shape mismatch.
- `PlantScratch` grows its own index arrays to `state.capacity`.

**Gate review** (code-optimizer-reviewer and system-architect on `81de254..` the WP head): no correctness defects. Fixed by amending the owning WP:
- GL 2.1:
  - The G15 cap.
  - The non-finite foot throws.
  - Spill relaxes only the above-cap box plus one cell; the result is identical to the full-grid version to 1e-15.
  - No per-substep `delta.fill`.
- GL 2.2:
  - The scratch shape check compares `nx` and `ny`.
  - The bucket index is built lazily on a step's first spawn candidate.
  - The cell is cached per plant while building the index.

Deferred to the Phase 3 grill: the rescale test versus G5, the step signature and scratch, and renderer inputs. Deferred to the Phase 4 grill: the substep budget. Both are listed in those phases. Accepted and not pursued:
- `Math.exp` is not required to round identically in every engine, so "same seed, same result" holds within one engine (Electron), not across browsers.
- `feed` (about 3.5 ms at 100k plants) is the largest per-step cost. Caching per-plant bilinear data was judged not worth an extra `cloneInto` copy without a browser profile.

**Measured** (reviewer bench, defaults, 120x120 grid, 100k live plants, before the lazy-index and spill-box fixes): about 5.9 ms per step. The breakdown: `feed` 3.5, `spawn` 1.3, `spill` 0.42, `updateFrames` 0.36, `diffuse` 0.15, `cloneInto` 0.12.

**Gate results:**
- `npm test`: 114 pass (12 field and 15 plant tests are new).
- Fitness: 0 violations, and `fitness.test.mjs` 16 pass.
- pytest (no Python changed): 242 pass, with 1 failure from the known `TODO.md` e2e flake, which passes in isolation.

**Lessons:**
- A ratio quoted from the brief ("2 : 1, isotropic") was taken on trust in the grill. The WP measured it and found it wrong. Ask workers to verify numerical claims they implement, and surface rather than silently follow a contradiction.
- The "own cell pays" decision (G17) came from walking through a fringe-spawn example in the grill. Concrete scenarios find behaviour changes that the wording hides.

### Phase 3

**Commits** on `gl-3-step-render` (from `main` at `cab6f5b`):
- `41389f2`: baseline, grill decisions G20 to G27 and the default-parameter measurement (the WP base).
- `06d3fd8`: GL 3.1 (amended after the gate review).
- `a8fe0e5`: GL 3.2 (amended after the gate review).
- Then the `[GL 3]` outcomes commit (this text).

**Grill decisions:** G20 to G27 above; G28 (draw order by age) at the gate.

**API as built:**
- `sim/step.mjs`: as G21. `step` throws when `prev === next`. `resolveFoot` treats an age exactly equal to `foot_timeout_ms` as present.
- `render/renderer.mjs`: `createRenderer(canvas, arena, atlas)` returns `{ render, setAtlas, resize, dispose }` and exports `GladeWebGL2Error`. It checks the atlas shape (canvas size, even gutter), that the grid covers the arena, and that `N_frames` matches the atlas. The context is `antialias: false`.
- `render/shaders.mjs`: PCG hash per instance; atlas `u = 1 - corner.x` so the art is not mirrored under D18.
- `dev/render-demo.html`: synthetic scene (corners, an 80-plant cluster with sizes falling with id, isolated plants, two density blobs); `E` or `?heatmap` toggles the heatmap; numeric URL overrides such as `?sprite_size_mm=240` (an extra, kept).

**Gate review** (code-optimizer-reviewer and system-architect on `41389f2..aa07b2b`): no blocking defects. Fixed by amending the owning WP:
- GL 3.1: the "glade keeps widening" test passed by 2 mm for seed 1 and failed for seed 7; it now averages seeds 1 to 3 (3-seed means gain 21 to 33 mm in the last interval and 105 to 126 mm from 10 to 20 s, over seeds 1 to 9) and asserts strict growth plus over 50 mm from 10 to 20 s.
- GL 3.2:
  - Sprites were mirrored (u followed arena +x, which is screen left); verified fixed with an asymmetric test atlas.
  - Checks for grid/arena agreement, atlas canvas size and gutter parity, and calls after `dispose()`.
  - `antialias: false`; halo quads skipped at `shadow_strength` 0.
  - Demo cluster sizes now fall with id (the user's draw-order observation, G28).

Deferred to Phase 4 (in WP 4.1's carry-over): the lil-gui candidate-copy trap, resize observation, and the unmeasured upload and clear savings. Dropped: per-vertex hash cost, pure-function extraction for node tests of the renderer.

**Measured** (reviewer bench, 6 x 6 m arena, h = 50, 100k plants): `step` 3.5 ms without spawning, `cloneInto` 0.1 ms, instance pack 0.6 ms per frame, density copy 0.01 ms. The GPU was not profiled.

**What the defaults show** (G22, foot at z = 100 mm): standing still gives no expanding glade (core above the cap for about 5 s, radius about 260 mm then shrinking, about 100 plants); walking works (peak 86 E/m²); die-off empties the pool about 5 s after the foot leaves, with youngest-first only about 2 : 1 by same-cell pairs (about 5 : 1 at `input_power` 200, `lambda_max` 20). At those pinned values a walking foot at z = 0 peaks at 414 E/m², just over the cap, so "walking never saturates" depends on foot height and `input_power`.

**Gate results:**
- `npm test`: 125 pass (11 step tests new).
- Fitness: 0 violations, and `fitness.test.mjs` 16 pass.
- No Python or config changed, so pytest was not run.
- Browser: Firefox (Playwright) and Electron 37.4 / Chromium 138 at devicePixelRatio 2 (orchestrator script with a hidden `BrowserWindow` and `capturePage`) both render the demo correctly with a clean console, apart from Electron's generic development security notice.

**Lessons:**
- Synthetic demo data must respect the model's invariants (here: older plants are not smaller); otherwise a correct renderer looks wrong.
- A test margin chosen from one seed is a measurement of that seed. Check margins over several seeds before asserting.
- Electron can serve as the Chromium check: a short script with a hidden window, `capturePage` and `console-message` covers what Playwright's Firefox cannot.

### Phase 4

Not run.

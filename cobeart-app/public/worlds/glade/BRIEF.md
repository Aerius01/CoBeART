# Glade: project brief

Living document for the Glade world. Update it as decisions are made; record each one under "Decisions".

## Vision

Vegetation grows around a performer's feet. A foot carries an energy field. Where that field touches the floor, the ground charges with energy, and plants sprout and feed on it. Walking leaves a short-lived trail of growth that withers once the ground is drained. Standing still saturates the ground, and the growth blooms outward into a glade that keeps widening slowly while the foot stays. The two behaviours are two parameter sets of one model, the `walking` and `standing` presets, switched by the operator (GL 5 grill G35).

## Scope

1. **MVP:**
   - one foot and one abstract species
   - energy field, cell grid, spawning, growth and decay
   - instanced rendering with procedural placeholder plants
   - a debug view of the energy grid and a live tuning panel
2. **Expansion:** outward growth from saturated ground. Cap overflow (below) should produce it without a separate zone or timer, so it is part of the MVP model. The MVP phase tunes it.
3. **Later:**
   - both feet
   - several species
   - hand-drawn sprites from the drawing pad
   - wind
   - audio reactivity

## Fit with the system

- A world folder, `public/worlds/glade/`. It receives `frame` postMessages only, so it needs no hub or contract changes. It appears in the launcher once it has an `index.html`.
- Input (MVP): one role from `frame.bodies` (x, y, z in mm, arena axes), set by the `foot_role` parameter. A missing role means no input that frame. `audio` messages are ignored in the MVP.
- Arena bounds come from `window.__COBEART_CONFIG__.arena`, never literals.
- Drawing follows the canonical top-down view (D18).
- Home testing: `foot_role` defaults to `right_hand`, the shipped mouse role, so choosing Mouse in the launcher drives the field while a button is held. The wheel sets height: wheel down plants the "foot", wheel up lifts it. The shipped `sources.mouse` changes to `z_initial_mm: 100` and `z_step_mm: 20` (10 notches from contact to nothing with `R` = 300); this alters Splat's mouse behaviour too, which is accepted.

**Live-testing checklist:**
- Set `foot_role` to `left_foot`.
- Calibrate `R` and `input_power` to the marker mount (raw marker z is used, section 1).

## Model

Everything runs on a fixed simulation timestep `dt`, independent of frame arrival (`bridge_framerate`) and display rate (see Runtime). Each step uses the latest known foot position. The step is deterministic, `step(prev, next, foot, params, scratch)`: it reads `prev`, writes only `next` (double buffering) and draws from a seeded RNG held in the state, so runs are reproducible and testable (GL grill G1, G21).

### 1. Foot energy field

The foot is a sphere of radius `R` centred on the foot marker. A floor point at horizontal distance `d` from a marker at height `z` gets weight

```
rho = sqrt(d^2 + z^2) / R
f   = (1 - rho^2)^2   for rho < 1, else 0
```

`f` is 1 under the centre and falls to 0 with zero slope at the edge, so the footprint has no visible rim.

- **Height:** raw marker z, with no rest-height parameter. The marker sits about 100 mm up when the foot is flat; `R` = 300 mm still gives about 79% of the peak there. Calibration of `R` and `input_power` absorbs the mount offset.
- **Footprint:** a disk of radius `sqrt(R^2 - z^2)` (about 283 mm at rest), which shrinks and fades as the foot lifts. No contact threshold or hysteresis is needed. A swinging foot leaves a weaker wake, and that is intended: the ground decides whether it is enough to grow anything.
- **Power:** `input_power` is the total power (E/s) of a foot at z = 0. The total at height z is the kernel's exact integral relative to z = 0, `input_power * (1 - z^2/R^2)^3`, so changing `R` or the cell size keeps the energy budget, and lifting the foot lowers the total naturally.
- **Distribution and arena edge:** each step's total is spread over the cells inside the grid in proportion to `f` at their centres (normalised by the inside sum), so a foot near a wall injects the same total as anywhere else (GL 2 grill). The boost `full sum / inside sum` is capped at 4 (a foot at a corner), so a foot mostly outside the arena fades out continuously instead of concentrating its whole power in the last few cells inside.
- **Presence:** the foot is absent when its role is missing or its last sighting is older than `foot_timeout_ms`, so a dropped source never leaves a permanent energy source.
- **Speed:** no modulation in the MVP. Time spent over a cell already rewards staying put; a speed multiplier can be added later.
- An ellipsoid (separate horizontal and vertical radii) is the upgrade path if lift-off and footprint size need separate tuning.

### 2. Cell grid

The arena floor is discretised into square cells of size `h` = 50 mm (120 x 120 cells for the 6 m x 6 m arena), in arena axes. The D18 view is applied only when rendering. The grid starts empty.

**Units:**
- A cell stores energy **density** (E/m²) in a `Float64Array` (GL 2 grill: conservation precision; the heatmap converts to Float32 at upload). A plant draws absolute energy as `density * cell area`, so `h` is a pure resolution knob.
- **1 E = one fully grown reference plant** (`plant_max = 1`). `E_cap` therefore reads as plants per m² of saturated ground, `input_power` as plants' worth per second, and the rates `r`, `r_maintain` and `decline_rate` as fractions of a plant per second.
- Rescaling E later is one multiplication across all energy parameters.

**Stencil:** diffusion and spill use 8 neighbours weighted orthogonal : diagonal = 4 : 1 (2/3 and 1/6, the 9-point stencil whose error is isotropic; GL 2 grill, after measuring that 2 : 1 is not). With 4 neighbours, fronts would grow as diamonds.

**Coefficients and stability:**
- Both coefficients are physical, in mm²/s. Energy spreads over roughly `sqrt(4 * D * t)` in t seconds; for example, D = 2,500 mm²/s spreads about 100 mm in one second.
- The per-step factor is `k = D * dt / h^2`. Above `k = 0.3` the 9-point scheme would overshoot (negative energy, checkerboard oscillation), so each operator runs `n = ceil(10 * k / 3)` substeps of `dt / n`.
- Tuning `D`, `h` or `dt` therefore keeps the same look and cannot destabilise the field.

Per step, in order:

1. **Deposit:** add the foot's power (section 1) times `dt`, sampled at cell centres.
2. **Diffuse:** a slow always-on diffusion with coefficient `D` smooths the field, so neighbouring cells never jump from full to empty. Fluxes are computed per edge and applied antisymmetrically, so energy is conserved.
3. **Spill:** "pressure" diffusion of the excess `max(0, E - E_cap)`, with a fast coefficient `D_spill` much larger than `D`.
   - Excess flows through saturated cells down its own gradient until it reaches cells with room, where it fills them. The saturated region therefore expands at a rate set by input power, with no iteration loop.
   - The cap is soft: cells under a feeding foot sit slightly above it, in proportion to how strongly the foot feeds. This also keeps the saturated core a gradient.
4. **Decay:** the ground half-life, below.
5. **Plant exchange:** see section 3.

**Boundary:** reflecting (no flux). Neither diffusion nor spill crosses the arena edge, so energy reaching the edge stays in the grid and backs up along it.

**Ground half-life** (G39): every step, every cell's density is multiplied by `2^(-dt / ground_half_life_s)`, including under the foot. The decayed energy leaves the system and is reported with the step's accounting. Walking sets the maximum, 600 s (slow: about 7% per minute); standing sets tens of seconds, which bounds how long a standing glade lingers after the foot leaves and keeps the floor from being "primed" just under `E_spawn_min`.

### 3. Plants

One abstract species for the MVP. Each plant is a record in a typed-array pool:

| Field | Meaning |
|---|---|
| `x, y` | position in mm, jittered within its cell |
| `id` | birth order, monotonically increasing (also the age priority) |
| `seed` | per-plant randomness (shape, tint, timing) |
| `energy` | internal energy in `[0, 1]`; 1 is fully grown |
| `frame` | displayed animation frame, `0..N_frames-1` |
| `direction` | `up` (growing sprite set) or `down` (declining sprite set) |
| `mature` | set the first time `energy` reaches 1, never cleared; a new plant is young |

The simulation state is `energy` and `mature`. Growing or declining is a visual property derived from `direction`, which still follows the frame changes.

**Demand** (G36, G43): every plant has two needs per step, both drawn over the four nearest cells by bilinear weights, so plants never see cell edges:
- **Upkeep** `u = r_maintain * dt`: consumed, it leaves the system.
- **Growth**, only below full: `g = min(r * dt, 1 - energy)`, stored 1:1. Capping at the headroom means a plant never draws what it cannot store and reaches exactly 1 when fully fed.

**Young and mature** (G37): with `m_u` the missed upkeep and `m_g` the missed growth,
- **Young** needs upkeep and growth: `energy += taken growth - decline_rate * dt * (m_u + m_g) / (u + g)`.
- **Mature** needs only upkeep: `energy += taken growth - decline_rate * dt * m_u / u`. Growth is a bonus: spare ground lets a declined mature plant regrow, otherwise it holds. There is no demand cliff when a mature plant dips below 1.
- Energy is clamped to `[0, 1]`; energy lost to decline counts as consumed.

What the rates mean: a fed seed grows to full in about `1/r` seconds; a grown plant costs `r_maintain` per second; on empty ground a plant withers from full to dead in about `1/decline_rate` seconds. With `r_maintain = r` (walking) the plant count settles at about `input_power * lift / r`; cheap upkeep (standing) lets the glade keep widening, because grown plants leave energy for spill to carry outward.

**Allocation** (G38): two passes over the pool, oldest first (ascending `id`). Pass 1 serves every plant's upkeep, pass 2 every plant's growth; decline is applied once per plant after both. Survival comes before growth, so a drying patch thins out from the young fringe while an older, sparser stand holds on. Pass 2 serves a declined mature plant's regrowth before a younger plant's growth, even though the young plant declines without it; this is intended (G48), revisit only if a drying glade's fringe dies back too hard. The result is deterministic and independent of update order.

**Birth and death:**
- A plant spawns with `seed_energy`, taken from the ground at its position (section 4), so spawning is an energy transaction too.
- At `energy <= 0` the plant is removed from the pool. How death looks is a rendering decision (session 5).

**Display hysteresis:** the displayed frame moves only when energy clears a frame boundary by a margin `delta` (a fraction of one frame band):

```
step up   when energy >= (frame + 1 + delta) / N_frames  ->  frame += 1, direction = up
step down when energy <  (frame - delta) / N_frames      ->  frame -= 1, direction = down
```

Energy 1 shows the top frame. Jitter at a boundary flips neither the frame nor the sprite set. Revival needs no rule: a declining plant that is fed again climbs a frame and switches back to the growing set.

### 4. Spawning

Spawning runs after the plant exchange, so a new plant is first fed on the next step.

- **Rate:** `lambda` is a density, in spawns per m² per second, so `h` does not change how fast the glade fills. It ramps linearly from 0 at `E_spawn_min` to `lambda_max` at `E_cap`, and stays flat above the cap. The glade's edge therefore shows sparse sprouts thickening towards the core.
- **Per cell:** each cell spawns as a Poisson process with probability `p = 1 - exp(-lambda(rho) * h^2 * dt)` per step, using the cell's own density, with at most one spawn per cell per step. Cells are visited in a fixed order with a seeded PRNG.
- **Position:** uniform random within the cell.
- **Spacing:** a candidate within `min_spacing_mm` of another plant (checked through the neighbouring cells) is rejected and charges nothing. This gives even, blue-noise spacing; spawning slows by itself as an area fills.
- **Payment:** the candidate's own cell pays `seed_energy` (spawning is decided per cell, and validation guarantees a cell at `E_spawn_min` can pay). If the cell cannot pay, it does not spawn. Feeding stays bilinear (section 3).

**Density:** the energy budget (`E_cap`, `input_power`, `r_maintain`) sets how many plants a patch sustains; spacing only prevents stacking (at 15 mm the implied ceiling is about 5,000 plants per m²). If the glade looks thin, tune sprite size first, then `E_cap` and `input_power`, and spacing last.

### Emergent behaviour (expected, to be verified by tuning)

- A walking foot never saturates cells, so it leaves a trail and the glade does not expand.
- A planted foot saturates the ground beneath it, and the spill pushes growth outward. With walking values (`r_maintain = r`) the plant count tends to `input_power * (1 - z²/R²)³ / r_maintain` and the glade settles. With standing values (cheap upkeep, a ground half-life) it keeps widening, slowing as it grows (measured at the live-tuned standing values, `input_power` 3000, `r_maintain` 0.05, `ground_half_life_s` 15, z = 100 mm on the 6 x 6 m arena: 90% radius 610 mm at 1 s, 1,587 mm at 15 s, 1,794 mm at 30 s, about 1.85 m from 60 s with about 29,000 plants; empty 13 s after the lift. The grill's slower start point, `input_power` 200 and `r_maintain` 0.01, still widened at 120 s: 841 mm, empty 36 s after the lift). A lower `lambda_max` lets spill carry energy further before plants eat it, giving a wider bloom.
- Spill reaching the reflecting edge backs up along it and fills inward, giving asymmetric growth near walls and corners.
- After the foot leaves, plants live on the leftover ground, which upkeep and the half-life drain, then wither over `1/decline_rate`. Dense patches run dry sooner than sparse ones and thin out youngest first.

## Rendering

**Pipeline:**
- WebGL2, one instanced draw call for all plants.
- Each frame, the live plants are uploaded as an instance buffer (`bufferSubData`) holding position, displayed frame, direction and seed.
- The vertex shader places, rotates and scales each quad from the instance data and picks the atlas cell from `(frame, direction)`.
- Black background, since an unlit floor is black under projection.

**Mapping:** the arena is stretched to fill the canvas with independent x and y scales, as in Splat. When the projected image covers the arena floor, this is the physically correct mapping, so sizes in mm come out right on the floor; only a monitor with a different aspect ratio shows them squashed. Orientation follows D18.

**Viewpoint:** plants are seen top-down (rosettes, tufts, blooms opening from above), each at a random rotation from its seed. Scattered side-on "pressed flowers" are an option later.

**Atlas:**
- A colour atlas with one cell per frame per sprite set (growing, declining).
- For the MVP it is generated at startup with Canvas 2D, with `N_frames` as a parameter. Drawn PNG strips later replace only the atlas loader.
- The art carries all colour, including the shift to straw or brown in the declining set.
- Growth comes from the frames: each frame is drawn at the full sprite canvas, with a sprout small inside its frame and a grown plant filling it.

**Birth and death:** handled by the art. Frame 0 of both sets is nearly nothing (a speck, a dry wisp), so appearing and removal do not show. There are no fades and no extra state.

**Per-plant variation (from the seed):**
- size: `sprite_size_mm * (1 +- size_jitter)`
- rotation: 0 to 360 degrees
- brightness: `* (1 +- brightness_jitter)`

**Draw order:** each frame the live plants are compacted in order (dead ones filtered out, relative order kept), which is O(n) and cannot reshuffle neighbours. They are drawn newest first, so the oldest are on top: the mature canopy covers a shaded understory.

**Depth halos:** each instance draws two quads in order within the same draw call, first a halo and then the sprite.
- The halo is the sprite's own alpha, scaled by `shadow_scale` and softened by sampling a coarser mipmap level (`shadow_blur` as a bias), drawn black at opacity `shadow_strength`.
- With premultiplied alpha it multiplies whatever is beneath by `1 - a`.
- On the black floor it is invisible, so it shows only where it falls on another plant, and stacked layers darken progressively.
- It is centred (ambient occlusion, the same for every viewer). `shadow_offset_mm` exists at 0 for a directional look later.

**Debug view:** `E` toggles the energy field under the plants. The cell grid is uploaded as an `R32F` texture and drawn on a colour ramp from 0 to `E_cap`, with a separate tint for the soft-cap overshoot.

**Known MVP limitation (wilt colour jump):** a declining plant shows the declining frame for its current energy, and the declining art bakes in a wilting stage. A plant that turns back at 0.4 E therefore jumps from healthy green straight to an already-browned frame. The likely fix is to separate the two: size stays driven by energy, while a separate wilt progress (energy lost since the decline began, or time declining) drives colour, for example a flat overlay blending towards straw across the first few declining frames. The MVP accepts the jump.

**Later:** wind as a shear weighted by distance from the plant's base, so the base stays fixed (needs a pivot convention for top-down art).

## Runtime

**Loop:**
- A `requestAnimationFrame` loop accumulates elapsed real time and runs fixed steps of `dt` to consume it, normally one per frame. The glade therefore grows at real-time speed on any display rate.
- Each frame renders the latest state. There is no render interpolation: plants do not move, they only change frames.
- When more than `max_steps_per_frame` steps are owed (a hidden browser tab, a debugger pause, a genuine stall), the loop runs that many and drops the rest, so the simulation resumes as if no time had passed. Every drop logs a rate-limited warning with the dropped milliseconds, so a real stall is visible and its root cause can be fixed. Electron has background throttling disabled, so in the show setup drops should not occur.

**Frames:** on each `frame` message the world stores the `foot_role` body (if present) with its receipt time (`performance.now()`). The step treats the foot as absent once that sighting is older than `foot_timeout_ms`. The receipt clock answers "is data still arriving here" and is immune to clock offsets between machines.

**Parameters:**
- `params.mjs` holds one metadata definition per parameter: type, range, unit, and whether it is live or structural.
- From it come the frozen defaults and `validateParams(p)`, which throws an error naming the parameter and the fix (for example, `seed_energy` larger than `E_spawn_min` ground can pay at the current `h`).
- **Presets** (G40): `PRESETS = { walking, standing }`, each a complete parameter set; walking is the defaults, standing overrides only what differs, and both share every structural value. The world starts in walking. A switch applies live without a reset and keeps the current `foot_role` (a site setting), `seed` and structural values (`h`, `N_frames`: the live grid and atlas). Each preset remembers its panel edits until the page reloads (G46): a switch stores the active set under its name and loads the other preset's stored set, or its code values if it was never edited.
- **Live** parameters apply on the next step (rates, coefficients, thresholds, jitter, halos). **Structural** ones rebuild state and therefore reset the glade (`h` reshapes the grid, `N_frames` regenerates the atlas).

**Tuning panel:** lil-gui, vendored as a single ES module in `vendor/`, built by looping over the parameter metadata (a dropdown for `foot_role`). Structural parameters are marked. The panel is HTML over the canvas: it edits the `params` object, the simulation reads it each step, and the renderer passes visual values to the shaders as uniforms each frame.

**Keys:**

| Key | Action |
|---|---|
| `E` | toggle the energy heatmap |
| `T` | toggle the tuning panel |
| `D` | dump both presets as one JSON object `{ walking, standing }` (the active one as edited, the other as stored) with the active name to the console; the clipboard gets the JSON only. To keep them, the walking set goes into the `PARAMS` defaults and standing into its override list, as the keys that differ from the final walking (G47) |
| `R` | reset: clear the ground and plants, keep the parameters, reseed the RNG from `seed` |
| `1` / `2` | switch to the walking / standing preset (no reset) |

`B` and `M` stay with the host. The mouse layer captures clicks, so press `M` to use the panel and `M` again to paint, as with Splat's GUI.

**Seed:** the `seed` parameter (default 1) is logged at start, so a run with the same input replays identically.

## Parameters

All values are starting guesses, tuned live through the panel (see Runtime). The Start column is the walking preset; standing overrides `input_power` 3000, `r_maintain` 0.05, `decline_rate` 1, `ground_half_life_s` 15 (GL 5 live tuning).

| Parameter | Role | Start |
|---|---|---|
| `foot_role` | the tracked body (`left_foot` for live testing) | `right_hand` |
| `h` | cell size (mm) | 50 |
| `R` | foot field sphere radius (mm) | 300 |
| `input_power` | total power of a foot at z = 0 (E/s, plants' worth per second) | 800 |
| `foot_timeout_ms` | sighting age after which the foot is absent | 200 |
| `ground_half_life_s` | half-life of ground energy, everywhere (s); the maximum 600 is slow, about 7% per minute | 600 |
| `E_cap` | soft ground cap (E/m², plants per m²) | 400 |
| `D` | background diffusion (mm²/s) | 2,500 |
| `D_spill` | excess (spill) diffusion (mm²/s) | 100,000 |
| `r` | growth rate below full, stored 1:1 (E/s); growth time `1/r` | 2 |
| `r_maintain` | upkeep every plant consumes (E/s) | 2 |
| `decline_rate` | energy loss of a plant whose needs go entirely unmet (E/s); wither time `1/decline_rate` | 2 |
| `plant_max` | plant full energy (the unit, fixed) | 1 |
| `seed_energy` | energy a new plant takes from the ground (E) | 0.02 |
| `N_frames` | animation frames per sprite set (open: depends on how many will be drawn) | 30 |
| `delta` | frame hysteresis margin (fraction of a frame band) | 0.25 |
| `E_spawn_min` | spawn threshold (E/m²) | 40 |
| `lambda_max` | spawn rate at `E_cap` (spawns per m² per second) | 200 |
| `min_spacing_mm` | minimum distance between plants (mm) | 15 |
| `sprite_size_mm` | plant sprite size on the floor (mm) | 60 |
| `size_jitter` | per-plant size variation (fraction) | 0.2 |
| `brightness_jitter` | per-plant brightness variation (fraction) | 0.15 |
| `shadow_strength` | halo opacity | 0.5 |
| `shadow_scale` | halo size relative to the sprite | 1.3 |
| `shadow_blur` | halo softness (mipmap bias) | 2 |
| `shadow_offset_mm` | halo offset for a directional look | 0 |
| `dt` | simulation step (s) | 1/60 |
| `max_steps_per_frame` | catch-up limit before time is dropped | 4 |
| `seed` | RNG seed, used at start and on reset | 1 |

## Planned layout

```
public/worlds/glade/
  BRIEF.md          this document
  index.html        page, postMessage listener
  main.js           wiring: messages, fixed-step loop, render loop, keys
  params.mjs        parameter metadata, defaults, validation
  panel.mjs         lil-gui panel built from the metadata
  vendor/           lil-gui (ES module)
  sim/field.mjs     foot deposition, diffusion, spill (pure)
  sim/plants.mjs    plant pool, spawning, allocation, demand, frame hysteresis (pure)
  sim/step.mjs      one simulation step
  render/           WebGL2 instanced renderer, atlas, debug heatmap
test/glade-sim.test.js   node --test on the pure simulation
```

Tests cover:
- energy conservation (deposit, plant consumption and decay aside)
- no flux across the reflecting boundary
- stability under automatic substepping (no negative energy at large `D`)
- the same result after changing `h`
- spill front propagation
- oldest-first allocation under shortage
- the spawn ramp, spacing rejection and seed payment
- growth, holding and withering under upkeep, growth and decline, young and mature
- ground decay at the half-life
- frame hysteresis, including revival
- seeded reproducibility
- parameter validation errors

The renderer is not under CI. It is checked in a real browser with Playwright (mouse input, a screenshot, the console) as part of finishing each change.

## Design sessions

Each part gets its own grill session. Its outcome updates the relevant section above and the decision list below.

| # | Part | Covers | Status |
|---|---|---|---|
| 1 | Foot energy field | falloff shape, `R`, the floor reference height, whether speed modulates input, input power units | done |
| 2 | Ground field | cell size, energy units, cap, diffusion, spill rule and iteration, reflecting boundary | done |
| 3 | Plant lifecycle | phases, uptake, maintenance, decline, revival, death, allocation | done |
| 4 | Spawning | rate curve, threshold, density cap, placement within cells | done |
| 5 | Rendering | instanced pipeline, placeholder look, mapping `(frame, direction)` to visuals, death visuals, debug heatmap, D18 mapping | done |
| 6 | Runtime and tooling | fixed-step loop, frame handling, parameter module and tuning panel, tests, mouse testing workflow | done |

All sessions are done. Next: an implementation plan.

## Open questions

- Wilt colour bridging for plants that start declining before full growth (see Rendering, known MVP limitation).
- Final `N_frames`, once the drawing budget is known.
- Target plant count and frame time on the show machine (left to tuning).
- Ellipsoid foot field, if lift-off and footprint size need separate tuning.
- A dwell-dependent automatic switch between the walking and standing presets.
- Later extensions: both feet, more species with their own demand and `decline_rate`, wind, audio.

## Decisions

- Raw WebGL2 with instancing from the start, for foliage density.
- Energy model instead of foot-contact detection and a dwell zone.
- Cap overflow (spill) drives expansion; diffusion keeps the field a smooth gradient.
- Reflecting arena boundary: energy is never lost at the edge.
- Simulation on the CPU in typed arrays; rendering on the GPU.
- Parameters are world-local, with a live tuning panel.
- MVP: one foot, one abstract species, no audio.
- Target plant count is left to tuning.
- Session 1 (foot field): sphere, `R` = 300 mm, falloff `(1 - rho^2)^2`, raw marker z, no speed modulation, `input_power` normalised at z = 0, renormalised at the arena edge (since GL 2: analytic total, edge boost capped at 4), foot absent after `foot_timeout_ms`.
- Session 2 (ground field): `h` = 50 mm, cells store density (E/m²), 1 E = one grown plant, 9-point stencil (4 : 1 since the GL 2 grill), physical `D` and `D_spill` with automatic substepping, spill as pressure diffusion of the excess (soft cap), reflecting boundary, no passive decay (a ground half-life since GL 5).
- Session 3 (plant lifecycle): state is only `energy`; constant demand `r` in every stage, stored 1:1 below full, shortfall paid from the plant; oldest-first allocation; `seed_energy` taken from the ground at birth; removal at 0; frame hysteresis `delta`, with the sprite set from the direction of the last frame change (revival needs no rule).
- Session 4 (spawning): `lambda` as a density with a linear ramp from `E_spawn_min` to `E_cap`, at most one spawn per cell per step, uniform position in the cell, `min_spacing_mm` rejection (energy sets real density), `seed_energy` paid by the spawning cell or no spawn (changed from bilinear in the GL 2 grill, so fringe cells next to empty ground can still spawn).
- Session 5 (rendering): stretch-to-fill mapping (D18), top-down art at random rotation, Canvas 2D generated colour atlas (`N_frames` = 30 for now), birth and death handled by frame 0 of the art, per-seed size, rotation and brightness, order-preserving compaction with the oldest on top, black premultiplied depth halos visible only on overlap, `E` toggles the heatmap, the wilt colour jump accepted for the MVP.
- Session 6 (runtime and tooling): fixed-step accumulator with `max_steps_per_frame` = 4 and a logged drop beyond it, no render interpolation, foot sighting on the receipt clock, parameter metadata driving defaults, validation and a vendored lil-gui panel, live versus structural parameters, keys `E` `T` `D` `R`, `seed` = 1, `foot_role` = `right_hand` for home testing with shipped mouse z 100 mm and 20 mm steps, `node --test` on `sim/` plus a Playwright browser check.
- GL 4 grill: the glade is bounded by `input_power / r` for the MVP (a model change is deferred to after live tuning); starting values `input_power` 100, `r` 1/3, `lambda_max` 20, halos 0.5 / 1.3 / 2; `validateParams` caps each diffusion operator at 2,000,000 cell updates per step; Glade's keys are ignored while the focus is in a panel field.
- GL 4 live tuning: `input_power` 800, `r` 2, `lambda_max` 200. No setting gives a slowly widening glade (G29); a growth-model change is proposed as GL Phase 5.
- GL 5 grill: upkeep `r_maintain` and growth `r` split; young and mature plants with `decline_rate`, two allocation passes (upkeep before growth); ground half-life; `walking` and `standing` presets switched with `1`/`2` and the panel, no reset, each preset remembering its edits for the session; `D` dumps both; standing starts at `input_power` 200, `r_maintain` 0.01, `decline_rate` 1, `ground_half_life_s` 15; no `r_maintain <= r` rule.
- GL 5 live tuning: walking unchanged; standing `input_power` 3000, `r_maintain` 0.05 (with `decline_rate` 1, `ground_half_life_s` 15).

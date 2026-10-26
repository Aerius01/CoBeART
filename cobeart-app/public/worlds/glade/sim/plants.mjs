// Glade plants: bilinear feeding, removal, frame hysteresis and spawning (BRIEF.md sections 3 and 4).
// Operators mutate only the state they are handed, which the step has already filled with cloneInto (GL grill G11).
import { PLANT_MAX } from '../params.mjs';
import { nextFloat, nextU32 } from './rng.mjs';
import { DIRECTION, bilinear, cellOf, createBilinear, ensureCapacity } from './state.mjs';

/** Relative tolerance under which a bilinear draw counts as the full demand (weights sum to 1 only to rounding). */
const FULL_DEMAND_TOLERANCE = 1e-9;

/**
 * Per-module scratch (G13), not double-buffered. The bucket index lists the pool slots per cell (counting sort of
 * the stored Float32 positions); `spawned` holds the slot of the plant a cell spawned this step, or -1.
 * @typedef {object} PlantScratch
 * @property {number} nx grid shape the scratch was built for
 * @property {number} ny
 * @property {import('./state.mjs').Bilinear} bil
 * @property {Int32Array} cellStart bucket offsets into cellItems, length nx * ny + 1
 * @property {Int32Array} cellItems pool slots grouped by cell, length >= the pool capacity
 * @property {Int32Array} plantCell cell of each pool slot while building the index, same length as cellItems
 * @property {Int32Array} spawned per cell: the pool slot spawned there this step, or -1
 */

/**
 * @param {import('./state.mjs').Grid} grid
 * @param {number} capacity pool capacity to size the bucket index for (grows later with the pool)
 * @returns {PlantScratch}
 */
export function createPlantScratch(grid, capacity) {
  if (!Number.isSafeInteger(capacity) || capacity < 0) {
    throw new RangeError(`createPlantScratch: capacity ${capacity} must be a non-negative integer (state.capacity)`);
  }
  const cells = grid.nx * grid.ny;
  return {
    nx: grid.nx,
    ny: grid.ny,
    bil: createBilinear(),
    cellStart: new Int32Array(cells + 1),
    cellItems: new Int32Array(capacity),
    plantCell: new Int32Array(capacity),
    spawned: new Int32Array(cells),
  };
}

/** @param {import('./state.mjs').Grid} grid @returns {number} cell area in m² */
const cellArea = (grid) => (grid.h * grid.h) / 1e6;

/**
 * Bilinear withdrawal (G12): cell i is asked for w_i * amount and gives min(ask, its energy); a short cell is not
 * made up by the others. Writes `grid.density`.
 * @param {import('./state.mjs').Grid} grid
 * @param {number} x mm
 * @param {number} y mm
 * @param {number} amount E requested
 * @param {import('./state.mjs').Bilinear} bil scratch from createBilinear()
 * @returns {number} E taken, at most `amount`
 */
export function drawAt(grid, x, y, amount, bil) {
  bilinear(grid, x, y, bil);
  const area = cellArea(grid);
  const density = grid.density;
  let taken = 0;
  for (let k = 0; k < 4; k++) {
    const w = bil.weight[k];
    if (w <= 0) continue;
    const c = bil.index[k];
    const ask = w * amount;
    const available = density[c] * area;
    if (ask >= available) {
      taken += available;
      density[c] = 0;
    } else {
      taken += ask;
      density[c] -= ask / area;
    }
  }
  return taken;
}

/**
 * Feeds every plant oldest first (pool order) with demand r * dt drawn bilinearly, then removes plants at energy 0
 * by order-preserving compaction. Full demand is stored up to PLANT_MAX; a short plant pays the shortfall from itself
 * (G18). Writes `state` (density, pool, count).
 * @param {import('./state.mjs').GladeState} state
 * @param {import('../params.mjs').GladeParams} params
 * @param {PlantScratch} scratch
 * @returns {number} E that left the system (consumed), from the stored Float32 energies
 */
export function feed(state, params, scratch) {
  const { grid, plants } = state;
  const { x, y, energy, id, seed, frame, direction } = plants;
  const demand = params.r * params.dt;
  const fullThreshold = demand * (1 - FULL_DEMAND_TOLERANCE);
  let consumed = 0;
  let write = 0;
  for (let i = 0; i < state.count; i++) {
    const before = energy[i];
    const taken = drawAt(grid, x[i], y[i], demand, scratch.bil);
    const target = taken >= fullThreshold
      ? Math.min(PLANT_MAX, before + demand)
      : Math.max(0, before - (demand - taken));
    const stored = Math.fround(target);
    consumed += taken - (stored - before);
    if (stored <= 0) continue;
    if (write !== i) {
      x[write] = x[i];
      y[write] = y[i];
      id[write] = id[i];
      seed[write] = seed[i];
      frame[write] = frame[i];
      direction[write] = direction[i];
    }
    energy[write] = stored;
    write++;
  }
  state.count = write;
  return consumed;
}

/**
 * Frame hysteresis (G19): steps up while energy >= (frame + 1 + delta) / N_frames, down while
 * energy < (frame - delta) / N_frames, within [0, N_frames - 1]; direction follows the last change. Writes `state`.
 * @param {import('./state.mjs').GladeState} state
 * @param {import('../params.mjs').GladeParams} params
 */
export function updateFrames(state, params) {
  const { energy, frame, direction } = state.plants;
  const n = params.N_frames;
  const { delta } = params;
  const top = n - 1;
  for (let i = 0; i < state.count; i++) {
    const e = energy[i] * n;
    let f = Math.min(frame[i], top);
    let changed = -1;
    while (f < top && e >= f + 1 + delta) {
      f++;
      changed = DIRECTION.UP;
    }
    if (changed === -1) {
      while (f > 0 && e < f - delta) {
        f--;
        changed = DIRECTION.DOWN;
      }
    }
    frame[i] = f;
    if (changed !== -1) direction[i] = changed;
  }
}

/**
 * Rebuilds the cell-to-slot bucket index from the stored positions of the live pool and clears `spawned`,
 * growing the index arrays to the pool capacity first.
 * @param {import('./state.mjs').GladeState} state
 * @param {PlantScratch} scratch
 */
function buildBuckets(state, scratch) {
  const { grid, plants, count } = state;
  if (scratch.cellItems.length < state.capacity) {
    scratch.cellItems = new Int32Array(state.capacity);
    scratch.plantCell = new Int32Array(state.capacity);
  }
  const { cellStart, cellItems, plantCell, spawned } = scratch;
  const cells = grid.nx * grid.ny;
  cellStart.fill(0);
  spawned.fill(-1);
  for (let i = 0; i < count; i++) {
    const c = cellOf(grid, plants.x[i], plants.y[i]);
    plantCell[i] = c;
    cellStart[c + 1]++;
  }
  for (let c = 0; c < cells; c++) cellStart[c + 1] += cellStart[c];
  // Each bucket's start doubles as its fill cursor; afterwards every start sits at the next bucket's start, so
  // shifting the array right by one restores the offsets.
  for (let i = 0; i < count; i++) cellItems[cellStart[plantCell[i]]++] = i;
  for (let c = cells; c > 0; c--) cellStart[c] = cellStart[c - 1];
  cellStart[0] = 0;
}

/**
 * True when a plant at (px, py) mm is closer than `spacing` to any plant in the 3x3 cells around cell (ix, iy),
 * including plants spawned earlier in this step.
 * @param {import('./state.mjs').GladeState} state
 * @param {PlantScratch} scratch
 * @param {number} ix
 * @param {number} iy
 * @param {number} px
 * @param {number} py
 * @param {number} spacing mm
 * @returns {boolean}
 */
function tooClose(state, scratch, ix, iy, px, py, spacing) {
  const { grid, plants } = state;
  const { nx, ny } = grid;
  const { cellStart, cellItems, spawned } = scratch;
  const limit = spacing * spacing;
  for (let jy = Math.max(0, iy - 1); jy <= Math.min(ny - 1, iy + 1); jy++) {
    for (let jx = Math.max(0, ix - 1); jx <= Math.min(nx - 1, ix + 1); jx++) {
      const c = jy * nx + jx;
      for (let k = cellStart[c]; k < cellStart[c + 1]; k++) {
        const j = cellItems[k];
        const dx = plants.x[j] - px;
        const dy = plants.y[j] - py;
        if (dx * dx + dy * dy < limit) return true;
      }
      const s = spawned[c];
      if (s >= 0) {
        const dx = plants.x[s] - px;
        const dy = plants.y[s] - py;
        if (dx * dx + dy * dy < limit) return true;
      }
    }
  }
  return false;
}

/**
 * Spawning (BRIEF.md section 4, G17): cells in index order; a cell above E_spawn_min draws one float against
 * p = 1 - exp(-lambda * h² * dt), with lambda ramping linearly to lambda_max at E_cap. A hit draws a uniform position
 * in the cell, is rejected (no charge) within min_spacing_mm of a plant in the 3x3 cells, and spawns only if its own
 * cell can pay seed_energy. New plants are appended with nextId. Writes `state` and `scratch`.
 * @param {import('./state.mjs').GladeState} state
 * @param {import('../params.mjs').GladeParams} params
 * @param {PlantScratch} scratch
 * @returns {number} plants spawned
 */
export function spawn(state, params, scratch) {
  const { grid, rng } = state;
  const { nx, ny, h, x0, y0, density } = grid;
  const cells = nx * ny;
  if (scratch.nx !== nx || scratch.ny !== ny) {
    throw new Error(`spawn: scratch was built for a ${scratch.nx}x${scratch.ny} grid but the grid is ${nx}x${ny}; ` +
      'build it with createPlantScratch(state.grid, state.capacity) after a structural change');
  }

  const area = cellArea(grid);
  const { E_spawn_min: eMin, E_cap: eCap, lambda_max: lambdaMax, dt, min_spacing_mm: spacing } = params;
  const seedEnergy = Math.fround(params.seed_energy);
  const seedDensity = seedEnergy / area;
  let spawned = 0;
  // The bucket index is built when the first candidate reaches the spacing check, before any spawn this step,
  // so a step without candidates skips the O(n) rebuild.
  let bucketsBuilt = false;
  for (let c = 0; c < cells; c++) {
    const rho = density[c];
    if (!(rho > eMin)) continue;
    const lambda = lambdaMax * Math.min(1, (rho - eMin) / (eCap - eMin));
    const p = 1 - Math.exp(-lambda * area * dt);
    if (nextFloat(rng, rng) >= p) continue;
    const ix = c % nx;
    const iy = (c - ix) / nx;
    const px = Math.fround(x0 + (ix + nextFloat(rng, rng)) * h);
    const py = Math.fround(y0 + (iy + nextFloat(rng, rng)) * h);
    // Float32 rounding can push a position onto the next cell's edge; the stored value must stay in this cell.
    if (cellOf(grid, px, py) !== c) continue;
    if (!bucketsBuilt) {
      buildBuckets(state, scratch);
      bucketsBuilt = true;
    }
    if (tooClose(state, scratch, ix, iy, px, py, spacing)) continue;
    if (density[c] < seedDensity) continue;
    density[c] -= seedDensity;
    const i = state.count;
    ensureCapacity(state, i + 1, i);
    const plants = state.plants;
    plants.x[i] = px;
    plants.y[i] = py;
    plants.energy[i] = seedEnergy;
    plants.id[i] = state.nextId;
    plants.seed[i] = nextU32(rng, rng);
    plants.frame[i] = 0;
    plants.direction[i] = DIRECTION.UP;
    state.nextId++;
    state.count = i + 1;
    scratch.spawned[c] = i;
    spawned++;
  }
  return spawned;
}

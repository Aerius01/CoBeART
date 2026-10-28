// Glade simulation step: one fixed tick of the field and plant operators on the double buffer (GL grill G21).
// Pure module: no DOM, window or WebGL, so node --test can import it.
import { createFieldScratch, decay, deposit, diffuse, spill } from './field.mjs';
import { createPlantScratch, feed, spawn, updateFrames } from './plants.mjs';
import { cloneInto, createState } from './state.mjs';

/**
 * Per-operator scratch for one grid shape, not double-buffered (G13).
 * @typedef {object} StepScratch
 * @property {import('./field.mjs').FieldScratch} field
 * @property {import('./plants.mjs').PlantScratch} plants
 */

/**
 * Energy accounting of one step, in E.
 * @typedef {object} StepStats
 * @property {number} deposited energy the foot added to the ground
 * @property {number} consumed energy that left the system through plants (upkeep and decline)
 * @property {number} decayed energy that left the ground through its half-life
 * @property {number} spawned plants appended this step
 */

/**
 * Latest sighting of the `foot_role` body: arena mm plus its receipt time on the local clock.
 * @typedef {{ x: number, y: number, z: number, receivedMs: number } | null} Sighting
 */

/**
 * The two double-buffered states and their shared scratch.
 * @typedef {object} SimBuffers
 * @property {import('./state.mjs').GladeState} a
 * @property {import('./state.mjs').GladeState} b
 * @property {StepScratch} scratch
 */

/**
 * @param {import('./state.mjs').Grid} grid
 * @param {number} capacity pool capacity to size the plant index for (it grows with the pool)
 * @returns {StepScratch}
 */
export function createStepScratch(grid, capacity) {
  return { field: createFieldScratch(grid), plants: createPlantScratch(grid, capacity) };
}

/**
 * One step: copies `prev` into `next`, then runs deposit, diffuse, spill, decay, feed, spawn and frames on `next`.
 * `prev` is never modified.
 * @param {import('./state.mjs').GladeState} prev
 * @param {import('./state.mjs').GladeState} next overwritten
 * @param {import('./field.mjs').Foot} foot
 * @param {import('../params.mjs').GladeParams} params
 * @param {StepScratch} scratch
 * @returns {StepStats}
 */
export function step(prev, next, foot, params, scratch) {
  if (prev === next) {
    throw new Error('step: prev and next are the same state; pass the two buffers from resetState and swap them');
  }
  cloneInto(prev, next);
  const deposited = deposit(next.grid, foot, params, scratch.field);
  diffuse(next.grid, params, scratch.field);
  spill(next.grid, params, scratch.field);
  const decayed = decay(next.grid, params);
  const consumed = feed(next, params, scratch.plants);
  const spawned = spawn(next, params, scratch.plants);
  updateFrames(next, params);
  return { deposited, consumed, spawned, decayed };
}

/**
 * Two fresh states seeded from `params.seed` plus their scratch; for start, `R` and structural edits.
 * @param {import('../params.mjs').GladeParams} params
 * @param {import('../params.mjs').Arena} arena
 * @returns {SimBuffers}
 */
export function resetState(params, arena) {
  const a = createState(params, arena);
  const b = createState(params, arena);
  return { a, b, scratch: createStepScratch(a.grid, a.capacity) };
}

/**
 * Energy stored in the live plants, in E.
 * @param {import('./state.mjs').GladeState} state
 * @returns {number}
 */
export function totalPlantEnergy(state) {
  const { energy } = state.plants;
  let sum = 0;
  for (let i = 0; i < state.count; i++) sum += energy[i];
  return sum;
}

/**
 * The foot for this step: the sighting's position, or null when there is none or it is older than
 * `foot_timeout_ms` on the receipt clock.
 * @param {Sighting} sighting
 * @param {number} nowMs same clock as `sighting.receivedMs` (performance.now())
 * @param {import('../params.mjs').GladeParams} params
 * @returns {import('./field.mjs').Foot}
 */
export function resolveFoot(sighting, nowMs, params) {
  if (sighting === null) return null;
  if (nowMs - sighting.receivedMs > params.foot_timeout_ms) return null;
  return { x: sighting.x, y: sighting.y, z: sighting.z };
}

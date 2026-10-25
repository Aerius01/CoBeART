// Glade simulation state: the energy grid, the struct-of-arrays plant pool and the RNG (GL grill G1, G3, G9, G10).
// The loop double-buffers two states; functions write only into the state they are given as output.
import { validateParams } from '../params.mjs';
import { RNG_WORDS, seedRng } from './rng.mjs';

/** Pool capacity of a fresh state, in plants. */
export const INITIAL_CAPACITY = 4096;

/** Values of the plant `direction` field; also the atlas row (0 growing, 1 declining). */
export const DIRECTION = Object.freeze({ UP: 0, DOWN: 1 });

/**
 * @typedef {object} Grid
 * @property {number} nx cells along arena x
 * @property {number} ny cells along arena y
 * @property {number} h cell size in mm
 * @property {number} x0 arena x_min in mm (grid origin)
 * @property {number} y0 arena y_min in mm (grid origin)
 * @property {Float32Array} density energy density per cell in E/m², index iy * nx + ix
 */

/**
 * Plant pool in struct-of-arrays layout; slots [0, count) are live and in ascending `id` order.
 * @typedef {object} PlantPool
 * @property {Float32Array} x position in mm
 * @property {Float32Array} y position in mm
 * @property {Float32Array} energy internal energy in [0, PLANT_MAX]
 * @property {Uint32Array} id birth order, ascending through the pool
 * @property {Uint32Array} seed per-plant randomness
 * @property {Uint16Array} frame displayed frame, 0..N_frames-1
 * @property {Uint8Array} direction DIRECTION.UP or DIRECTION.DOWN
 */

/**
 * @typedef {object} GladeState
 * @property {Grid} grid
 * @property {PlantPool} plants
 * @property {number} capacity length of every pool array
 * @property {number} count live plants
 * @property {number} nextId id of the next plant to spawn
 * @property {Uint32Array} rng sfc32 state (RNG_WORDS words)
 */

/**
 * Bilinear sample: the four nearest cell centres and their weights (sum 1).
 * @typedef {object} Bilinear
 * @property {Int32Array} index four cell indices
 * @property {Float64Array} weight four weights
 */

/** Keys of the PlantPool arrays. */
const POOL_KEYS = /** @type {readonly (keyof PlantPool)[]} */ (
  Object.freeze(['x', 'y', 'energy', 'id', 'seed', 'frame', 'direction']));

/** @param {number} capacity @returns {PlantPool} */
function allocatePool(capacity) {
  return {
    x: new Float32Array(capacity),
    y: new Float32Array(capacity),
    energy: new Float32Array(capacity),
    id: new Uint32Array(capacity),
    seed: new Uint32Array(capacity),
    frame: new Uint16Array(capacity),
    direction: new Uint8Array(capacity),
  };
}

/**
 * Copies the live prefix [0, count) of every pool array from `src` into `dst`.
 * @param {PlantPool} src
 * @param {PlantPool} dst
 * @param {number} count
 */
function copyPrefix(src, dst, count) {
  for (const key of POOL_KEYS) {
    dst[key].set(src[key].subarray(0, count));
  }
}

/**
 * An empty state for `params` (validated first): zero density, an empty pool, the RNG seeded from `params.seed`.
 * @param {import('../params.mjs').GladeParams} params
 * @param {import('../params.mjs').Arena} arena
 * @returns {GladeState}
 */
export function createState(params, arena) {
  validateParams(params, arena);
  const { h } = params;
  const nx = (arena.x[1] - arena.x[0]) / h;
  const ny = (arena.y[1] - arena.y[0]) / h;
  return {
    grid: { nx, ny, h, x0: arena.x[0], y0: arena.y[0], density: new Float32Array(nx * ny) },
    plants: allocatePool(INITIAL_CAPACITY),
    capacity: INITIAL_CAPACITY,
    count: 0,
    nextId: 0,
    rng: seedRng(params.seed, new Uint32Array(RNG_WORDS)),
  };
}

/**
 * Grows `state`'s pool by doubling until it holds `needed` plants, keeping slots [0, live). `live` is passed
 * explicitly because `state.count` is stale while a step is filling `state` as the next buffer. Replaces the
 * arrays inside `state` (the object stays the same); a no-op when the capacity already suffices.
 * @param {GladeState} state
 * @param {number} needed
 * @param {number} live slots already written that must survive the growth
 * @returns {GladeState} `state`
 */
export function ensureCapacity(state, needed, live) {
  if (!Number.isSafeInteger(needed) || needed < 0) {
    throw new RangeError(`ensureCapacity: needed ${needed} must be a non-negative safe integer`);
  }
  if (!Number.isSafeInteger(live) || live < 0 || live > state.capacity) {
    throw new RangeError(`ensureCapacity: live ${live} must be an integer in [0, capacity ${state.capacity}]`);
  }
  if (needed <= state.capacity) return state;
  let capacity = state.capacity;
  while (capacity < needed) capacity *= 2;
  const plants = allocatePool(capacity);
  copyPrefix(state.plants, plants, live);
  state.plants = plants;
  state.capacity = capacity;
  return state;
}

/**
 * Copies `src` into `dst` (density, live pool prefix, count, nextId, rng), growing `dst` to `src`'s capacity.
 * Throws when the grids differ in shape; a structural change needs a new state instead.
 * @param {GladeState} src
 * @param {GladeState} dst
 * @returns {GladeState} `dst`
 */
export function cloneInto(src, dst) {
  const a = src.grid;
  const b = dst.grid;
  if (a.x0 !== b.x0 || a.y0 !== b.y0) {
    throw new Error(`cloneInto: grid origins differ (src (${a.x0}, ${a.y0}) mm, dst (${b.x0}, ${b.y0}) mm); ` +
      'after an arena change, build a new state with createState instead');
  }
  if (a.nx !== b.nx || a.ny !== b.ny || a.h !== b.h) {
    throw new Error(`cloneInto: grid shapes differ (src ${a.nx}x${a.ny} at h ${a.h}, dst ${b.nx}x${b.ny} ` +
      `at h ${b.h}); after changing h, build a new state with createState instead`);
  }
  b.density.set(a.density);
  ensureCapacity(dst, src.capacity, 0);
  copyPrefix(src.plants, dst.plants, src.count);
  dst.count = src.count;
  dst.nextId = src.nextId;
  dst.rng.set(src.rng);
  return dst;
}

/**
 * Index (iy * nx + ix) of the cell containing (x, y) mm; points on or beyond the arena edge clamp to edge cells.
 * @param {Grid} grid
 * @param {number} x
 * @param {number} y
 * @returns {number}
 */
export function cellOf(grid, x, y) {
  const ix = Math.min(grid.nx - 1, Math.max(0, Math.floor((x - grid.x0) / grid.h)));
  const iy = Math.min(grid.ny - 1, Math.max(0, Math.floor((y - grid.y0) / grid.h)));
  return iy * grid.nx + ix;
}

/** @returns {Bilinear} scratch for bilinear() */
export function createBilinear() {
  return { index: new Int32Array(4), weight: new Float64Array(4) };
}

/**
 * Bilinear weights of (x, y) mm over the four nearest cell centres, written into `out`; weights sum to 1
 * and indices stay inside the grid.
 * @param {Grid} grid
 * @param {number} x
 * @param {number} y
 * @param {Bilinear} out scratch from createBilinear(), reused across calls
 * @returns {Bilinear} `out`
 */
export function bilinear(grid, x, y, out) {
  // Position in cell-centre units, clamped so that within half a cell of the edge all weight falls on the
  // edge cell (reflecting boundary). Inlined per axis: this runs per plant per step and must not allocate.
  const { nx, ny, h } = grid;
  const fx = Math.min(nx - 1, Math.max(0, (x - grid.x0) / h - 0.5));
  const fy = Math.min(ny - 1, Math.max(0, (y - grid.y0) / h - 0.5));
  const ix0 = Math.min(Math.floor(fx), Math.max(0, nx - 2));
  const iy0 = Math.min(Math.floor(fy), Math.max(0, ny - 2));
  const ix1 = Math.min(ix0 + 1, nx - 1);
  const iy1 = Math.min(iy0 + 1, ny - 1);
  const tx = ix1 === ix0 ? 0 : fx - ix0;
  const ty = iy1 === iy0 ? 0 : fy - iy0;
  out.index[0] = iy0 * nx + ix0;
  out.index[1] = iy0 * nx + ix1;
  out.index[2] = iy1 * nx + ix0;
  out.index[3] = iy1 * nx + ix1;
  out.weight[0] = (1 - tx) * (1 - ty);
  out.weight[1] = tx * (1 - ty);
  out.weight[2] = (1 - tx) * ty;
  out.weight[3] = tx * ty;
  return out;
}

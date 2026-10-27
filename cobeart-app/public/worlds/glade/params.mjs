// Glade parameter contract: metadata, frozen defaults and validation (BRIEF.md "Parameters").
// Pure module: no DOM, window or WebGL, so node --test can import it.
// sim/field.mjs must import only types from this module, or the two form an ES module cycle.
import { K_MAX, substeps } from './sim/field.mjs';

/**
 * Contract roles, a literal copy of contract/frame.schema.json $defs/role (the browser cannot read the contract).
 * @type {readonly Role[]}
 */
export const ROLES = Object.freeze([
  'left_hand', 'right_hand', 'head', 'chest_front', 'chest_back', 'left_foot', 'right_foot', 'stick',
]);

/** Full energy of one grown plant: the energy unit (1 E), fixed, so not a tunable parameter. */
export const PLANT_MAX = 1;

/**
 * @typedef {'number' | 'int' | 'enum'} ParamType
 * @typedef {'live' | 'structural'} ParamKind
 * @typedef {{
 *   key: string, type: ParamType, default: number | string,
 *   min: number | null, max: number | null, step: number | null,
 *   unit: string, kind: ParamKind, options?: readonly string[], description: string
 * }} ParamSpec
 * @typedef {'left_hand' | 'right_hand' | 'head' | 'chest_front' | 'chest_back' | 'left_foot' | 'right_foot' | 'stick'} Role
 * @typedef {{
 *   foot_role: Role, h: number, R: number, input_power: number, foot_timeout_ms: number, E_cap: number,
 *   D: number, D_spill: number, r: number, seed_energy: number, N_frames: number, delta: number,
 *   E_spawn_min: number, lambda_max: number, min_spacing_mm: number, sprite_size_mm: number,
 *   size_jitter: number, brightness_jitter: number, shadow_strength: number, shadow_scale: number,
 *   shadow_blur: number, shadow_offset_mm: number, dt: number, max_steps_per_frame: number, seed: number
 * }} GladeParams  one value per PARAMS entry, keyed by `key`
 * @typedef {{ x: readonly number[], y: readonly number[], z: readonly number[] }} Arena  arena bounds in mm
 */

/** Most cell updates (substeps x cells) one diffusion operator may cost per step, so no edit can freeze the page. */
export const MAX_CELL_SUBSTEPS = 2_000_000;

/** @type {(spec: ParamSpec) => Readonly<ParamSpec>} */
const param = (spec) => Object.freeze(spec);

/** One entry per parameter in the BRIEF.md table, in table order. */
export const PARAMS = Object.freeze([
  param({ key: 'foot_role', type: 'enum', default: 'right_hand', min: null, max: null, step: null, unit: '',
    kind: 'live', options: ROLES, description: 'tracked body that carries the energy field (left_foot for live testing)' }),
  param({ key: 'h', type: 'int', default: 50, min: 10, max: 200, step: 5, unit: 'mm',
    kind: 'structural', description: 'cell size; must divide both arena spans' }),
  param({ key: 'R', type: 'number', default: 300, min: 50, max: 1000, step: 10, unit: 'mm',
    kind: 'live', description: 'foot field sphere radius' }),
  param({ key: 'input_power', type: 'number', default: 100, min: 1, max: 5000, step: 1, unit: 'E/s',
    kind: 'live', description: "total power of a foot at z = 0, plants' worth per second" }),
  param({ key: 'foot_timeout_ms', type: 'int', default: 200, min: 50, max: 2000, step: 10, unit: 'ms',
    kind: 'live', description: 'sighting age after which the foot is absent' }),
  param({ key: 'E_cap', type: 'number', default: 400, min: 40, max: 4000, step: 10, unit: 'E/m²',
    kind: 'live', description: 'soft ground cap, plants per m² of saturated ground' }),
  param({ key: 'D', type: 'number', default: 2500, min: 250, max: 25000, step: 50, unit: 'mm²/s',
    kind: 'live', description: 'background diffusion coefficient' }),
  param({ key: 'D_spill', type: 'number', default: 100000, min: 10000, max: 1000000, step: 1000, unit: 'mm²/s',
    kind: 'live', description: 'spill (excess above E_cap) diffusion coefficient' }),
  param({ key: 'r', type: 'number', default: 1 / 3, min: 0.02, max: 5, step: 0.01, unit: 'E/s',
    kind: 'live', description: 'per-plant demand in every stage; growth and wither time 1/r' }),
  param({ key: 'seed_energy', type: 'number', default: 0.02, min: 0.001, max: 0.5, step: 0.001, unit: 'E',
    kind: 'live', description: 'energy a new plant takes from the ground' }),
  param({ key: 'N_frames', type: 'int', default: 30, min: 2, max: 64, step: 1, unit: 'frames',
    kind: 'structural', description: 'animation frames per sprite set; regenerates the atlas' }),
  param({ key: 'delta', type: 'number', default: 0.25, min: 0, max: 0.49, step: 0.01, unit: 'frame band',
    kind: 'live', description: 'frame hysteresis margin, a fraction of one frame band (below 0.5)' }),
  param({ key: 'E_spawn_min', type: 'number', default: 40, min: 1, max: 4000, step: 1, unit: 'E/m²',
    kind: 'live', description: 'spawn threshold; below E_cap, and ground at it must pay seed_energy per cell' }),
  param({ key: 'lambda_max', type: 'number', default: 20, min: 1, max: 5000, step: 1, unit: 'spawns/(m²·s)',
    kind: 'live', description: 'spawn rate at E_cap' }),
  param({ key: 'min_spacing_mm', type: 'number', default: 15, min: 0, max: 200, step: 1, unit: 'mm',
    kind: 'live', description: 'minimum distance between plants; at most h' }),
  param({ key: 'sprite_size_mm', type: 'number', default: 60, min: 5, max: 500, step: 1, unit: 'mm',
    kind: 'live', description: 'plant sprite size on the floor' }),
  param({ key: 'size_jitter', type: 'number', default: 0.2, min: 0, max: 0.9, step: 0.01, unit: 'fraction',
    kind: 'live', description: 'per-plant size variation, size * (1 ± size_jitter)' }),
  param({ key: 'brightness_jitter', type: 'number', default: 0.15, min: 0, max: 0.9, step: 0.01, unit: 'fraction',
    kind: 'live', description: 'per-plant brightness variation, brightness * (1 ± brightness_jitter)' }),
  param({ key: 'shadow_strength', type: 'number', default: 0.5, min: 0, max: 1, step: 0.01, unit: 'opacity',
    kind: 'live', description: 'depth halo opacity' }),
  param({ key: 'shadow_scale', type: 'number', default: 1.3, min: 1, max: 3, step: 0.01, unit: 'x sprite',
    kind: 'live', description: 'depth halo size relative to the sprite' }),
  param({ key: 'shadow_blur', type: 'number', default: 2, min: 0, max: 8, step: 0.1, unit: 'mip bias',
    kind: 'live', description: 'depth halo softness (mipmap bias)' }),
  param({ key: 'shadow_offset_mm', type: 'number', default: 0, min: -100, max: 100, step: 1, unit: 'mm',
    kind: 'live', description: 'depth halo offset for a directional look' }),
  param({ key: 'dt', type: 'number', default: 1 / 60, min: 1 / 240, max: 1 / 15, step: 0.0001, unit: 's',
    kind: 'live', description: 'fixed simulation step' }),
  param({ key: 'max_steps_per_frame', type: 'int', default: 4, min: 1, max: 16, step: 1, unit: 'steps',
    kind: 'live', description: 'catch-up limit per display frame before owed time is dropped' }),
  param({ key: 'seed', type: 'int', default: 1, min: 0, max: 0xffffffff, step: 1, unit: '',
    kind: 'live', description: 'RNG seed, used at start and applied on reset (R)' }),
]);

const SPEC_BY_KEY = new Map(PARAMS.map((spec) => [spec.key, spec]));

/** Raised by validateParams; `param` names the offending parameter and `value` its value. */
export class GladeParamError extends Error {
  /**
   * @param {string} param
   * @param {unknown} value
   * @param {string} message
   */
  constructor(param, value, message) {
    super(`${param} = ${typeof value === 'string' ? JSON.stringify(value) : String(value)}: ${message}`);
    this.name = 'GladeParamError';
    /** @type {string} */
    this.param = param;
    /** @type {unknown} */
    this.value = value;
  }
}

/** @returns {Readonly<GladeParams>} a frozen object holding every parameter's default */
export function defaultParams() {
  return Object.freeze(/** @type {GladeParams} */ (Object.fromEntries(PARAMS.map((spec) => [spec.key, spec.default]))));
}

/**
 * @param {Readonly<ParamSpec>} spec
 * @param {unknown} value
 */
function checkEntry(spec, value) {
  const { key } = spec;
  if (spec.type === 'enum') {
    if (!spec.options?.includes(/** @type {string} */ (value))) {
      throw new GladeParamError(key, value, `must be one of ${spec.options?.join(', ')}`);
    }
    return;
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new GladeParamError(key, value, 'must be a finite number');
  }
  if (spec.type === 'int' && !Number.isInteger(value)) {
    throw new GladeParamError(key, value, 'must be an integer');
  }
  if (value < /** @type {number} */ (spec.min) || value > /** @type {number} */ (spec.max)) {
    throw new GladeParamError(key, value, `must lie in [${spec.min}, ${spec.max}] ${spec.unit}`.trimEnd());
  }
}

const H_SPEC = /** @type {Readonly<ParamSpec> & { min: number, max: number, step: number }} */ (SPEC_BY_KEY.get('h'));

/**
 * Slider values of `h` (min + k * step) that divide both spans, nearest to `h` (up to two below and two above).
 * @param {number} h
 * @param {number} spanX
 * @param {number} spanY
 * @returns {number[]}
 */
function nearbyDivisors(h, spanX, spanY) {
  const valid = [];
  for (let c = H_SPEC.min; c <= H_SPEC.max; c += H_SPEC.step) {
    if (spanX % c === 0 && spanY % c === 0) valid.push(c);
  }
  return [...valid.filter((c) => c < h).slice(-2), ...valid.filter((c) => c > h).slice(0, 2)];
}

/**
 * Throws GladeParamError unless `p` holds exactly the PARAMS keys, each valid and all cross rules satisfied.
 * @param {Record<string, unknown>} p
 * @param {Arena} arena
 */
export function validateParams(p, arena) {
  if (p === null || typeof p !== 'object') {
    throw new GladeParamError('params', p, 'must be an object holding every Glade parameter');
  }
  for (const key of Object.keys(p)) {
    if (!SPEC_BY_KEY.has(key)) throw new GladeParamError(key, p[key], 'is not a Glade parameter; remove it');
  }
  for (const spec of PARAMS) {
    if (!(spec.key in p)) {
      throw new GladeParamError(spec.key, undefined, `is missing; the default is ${JSON.stringify(spec.default)}`);
    }
    checkEntry(spec, p[spec.key]);
  }

  const v = /** @type {GladeParams} */ (/** @type {unknown} */ (p));
  const spanX = arena.x[1] - arena.x[0];
  const spanY = arena.y[1] - arena.y[0];
  if (spanX % v.h !== 0 || spanY % v.h !== 0) {
    const nearby = nearbyDivisors(v.h, spanX, spanY);
    throw new GladeParamError('h', v.h, `must divide both arena spans (${spanX} x ${spanY} mm); ` +
      (nearby.length > 0 ? `nearby valid values: ${nearby.join(', ')}` : 'no value in range divides both spans'));
  }
  if (v.E_spawn_min >= v.E_cap) {
    throw new GladeParamError('E_spawn_min', v.E_spawn_min, `must be below E_cap (${v.E_cap})`);
  }
  const cellPayable = v.E_spawn_min * (v.h * v.h) / 1e6;
  if (cellPayable < v.seed_energy) {
    throw new GladeParamError('seed_energy', v.seed_energy, `exceeds what one cell at E_spawn_min can pay ` +
      `(E_spawn_min * h² = ${cellPayable} E); lower seed_energy or raise E_spawn_min or h`);
  }
  if (v.min_spacing_mm > v.h) {
    throw new GladeParamError('min_spacing_mm', v.min_spacing_mm,
      `must not exceed h (${v.h} mm), or the 3x3 neighbour check misses plants; lower it or raise h`);
  }
  const cells = (spanX / v.h) * (spanY / v.h);
  for (const key of /** @type {const} */ (['D', 'D_spill'])) {
    const work = substeps(v[key], v.dt, v.h) * cells;
    if (work > MAX_CELL_SUBSTEPS) {
      const fit = Math.floor(Math.floor(MAX_CELL_SUBSTEPS / cells) * K_MAX * v.h * v.h / v.dt);
      const lower = fit >= /** @type {number} */ (SPEC_BY_KEY.get(key)?.min) ? `lower ${key} to at most ${fit}, ` : '';
      throw new GladeParamError(key, v[key], `costs ${work} cell updates per step at h = ${v.h} mm and ` +
        `dt = ${v.dt} s (budget ${MAX_CELL_SUBSTEPS}); ${lower}raise h or lower dt`);
    }
  }
}

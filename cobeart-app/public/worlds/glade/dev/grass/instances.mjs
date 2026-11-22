// Per-instance growth g and vitality v for the grass demo (GP roadmap, G59). Pure module: no DOM, no WebGL.
// Each plant gets a seeded offset pair in [-1, 1]; "uniform" mode scales it by the spread, "spatial" mode replaces it
// by value noise over the arena so that neighbours look alike.

import { mulberry32, valueNoise } from '../wind/wind.mjs';

/** Lattice offset that decorrelates the v noise from the g noise. */
const V_NOISE_OFFSET = Object.freeze({ x: 31.7, y: -12.3 });

/**
 * @typedef {object} GrowthField
 * @property {number} count
 * @property {Float32Array} r per-plant offset for g, in [-1, 1]
 * @property {Float32Array} q per-plant offset for v, in [-1, 1]
 */

/**
 * @typedef {object} GrowthParams
 * @property {number} g global growth in [0, 1]
 * @property {number} g_spread
 * @property {number} v global vitality in [0, 1]
 * @property {number} v_spread
 * @property {boolean} spatial value-noise offsets instead of the seeded random ones
 * @property {number} noise_scale_mm size of a noise patch (spatial mode)
 */

/**
 * @param {number} count
 * @param {number} seed
 * @returns {GrowthField}
 */
export function createGrowthField(count, seed) {
  const rand = mulberry32(seed);
  const r = new Float32Array(count);
  const q = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    r[i] = 2 * rand() - 1;
    q[i] = 2 * rand() - 1;
  }
  return { count, r, q };
}

/** @param {number} value */
const clamp01 = (value) => Math.min(1, Math.max(0, value));

/**
 * Writes g_i, v_i (clamped to [0, 1]) for every plant into out[2 * i], out[2 * i + 1].
 * @param {GrowthField} field
 * @param {GrowthParams} params
 * @param {ArrayLike<number>} x plant positions, arena mm
 * @param {ArrayLike<number>} y
 * @param {Float32Array} out at least 2 * field.count floats
 */
export function fillGrowth(field, params, x, y, out) {
  const { g, g_spread: gSpread, v, v_spread: vSpread, spatial, noise_scale_mm: scale } = params;
  if (spatial && !(scale > 0)) throw new Error(`fillGrowth: noise_scale_mm must be > 0, got ${scale}`);
  if (out.length < 2 * field.count) {
    throw new Error(`fillGrowth: out holds ${out.length} floats, needs ${2 * field.count}`);
  }
  for (let i = 0; i < field.count; i++) {
    let r = field.r[i];
    let q = field.q[i];
    if (spatial) {
      r = 2 * valueNoise(x[i] / scale, y[i] / scale) - 1;
      q = 2 * valueNoise(x[i] / scale + V_NOISE_OFFSET.x, y[i] / scale + V_NOISE_OFFSET.y) - 1;
    }
    out[2 * i] = clamp01(g + gSpread * r);
    out[2 * i + 1] = clamp01(v + vSpread * q);
  }
}

// Seeded sfc32 RNG with explicit state (GL grill G2): the state is a Uint32Array(4) held in the simulation state.

/** Words of RNG state. */
export const RNG_WORDS = 4;

const WARMUP_DRAWS = 12;
const TWO_POW_32 = 0x100000000;

/**
 * Seeds an sfc32 state from `seed` (expanded by splitmix32, then 12 warm-up draws) and writes it into `out`.
 * @param {number} seed integer in [0, 2^32 - 1]
 * @param {Uint32Array} [out] receives the state
 * @returns {Uint32Array} `out`
 */
export function seedRng(seed, out = new Uint32Array(RNG_WORDS)) {
  if (!Number.isInteger(seed) || seed < 0 || seed > 0xffffffff) {
    throw new RangeError(`seed ${seed} must be an integer in [0, 4294967295]`);
  }
  let sm = seed | 0;
  for (let i = 0; i < RNG_WORDS; i++) {
    sm = (sm + 0x9e3779b9) | 0;
    let z = sm;
    z = Math.imul(z ^ (z >>> 16), 0x21f0aaad);
    z = Math.imul(z ^ (z >>> 15), 0x735a2d97);
    out[i] = z ^ (z >>> 15);
  }
  for (let i = 0; i < WARMUP_DRAWS; i++) nextU32(out, out);
  return out;
}

/**
 * One sfc32 draw: reads `stateIn`, writes the advanced state into `stateOut` (the two may be the same array).
 * @param {Uint32Array} stateIn
 * @param {Uint32Array} stateOut
 * @returns {number} unsigned 32-bit integer
 */
export function nextU32(stateIn, stateOut) {
  let a = stateIn[0] | 0;
  let b = stateIn[1] | 0;
  let c = stateIn[2] | 0;
  let d = stateIn[3] | 0;
  const t = (((a + b) | 0) + d) | 0;
  d = (d + 1) | 0;
  a = b ^ (b >>> 9);
  b = (c + (c << 3)) | 0;
  c = (c << 21) | (c >>> 11);
  c = (c + t) | 0;
  stateOut[0] = a;
  stateOut[1] = b;
  stateOut[2] = c;
  stateOut[3] = d;
  return t >>> 0;
}

/**
 * One draw as a float in [0, 1), u32 / 2^32; state handling as in nextU32.
 * @param {Uint32Array} stateIn
 * @param {Uint32Array} stateOut
 * @returns {number}
 */
export function nextFloat(stateIn, stateOut) {
  return nextU32(stateIn, stateOut) / TWO_POW_32;
}

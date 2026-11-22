// Wind prototype: the wind field (global direction and speed plus travelling noise gusts) and one damped spring per
// plant for whole-plant lean. Pure module: no DOM, no WebGL. Positions in arena mm, wind in m/s, lean in radians.

/**
 * @typedef {object} WindParams
 * @property {number} speed_mps global wind speed
 * @property {number} direction_deg direction the wind blows toward, arena axes (0 = +x, 90 = +y)
 * @property {number} gust_mps peak extra speed the noise gusts add along the wind
 * @property {number} gust_scale_mm size of a gust patch
 * @property {number} gust_travel_mps how fast gust patches travel downwind
 * @property {number} max_lean_deg stem tip angle the plant approaches in very strong wind
 * @property {number} speed_ref_mps wind speed giving about 76% of max lean (tanh(1))
 * @property {number} spring_hz natural sway frequency of a plant at scale 1
 * @property {number} damping damping ratio of the sway spring (below 1 overshoots)
 */

/** Deterministic 32-bit generator (mulberry32). */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Hash of an integer lattice point to [0, 1). */
function hash2(ix, iy) {
  let h = Math.imul(ix, 0x27d4eb2d) ^ Math.imul(iy, 0x165667b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h ^= h >>> 13;
  return (h >>> 0) / 4294967296;
}

/** Smooth value noise in [0, 1]. */
export function valueNoise(x, y) {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = x - ix;
  const fy = y - iy;
  const ux = fx * fx * (3 - 2 * fx);
  const uy = fy * fy * (3 - 2 * fy);
  const a = hash2(ix, iy);
  const b = hash2(ix + 1, iy);
  const c = hash2(ix, iy + 1);
  const d = hash2(ix + 1, iy + 1);
  return a + (b - a) * ux + (c - a) * uy + (a - b - c + d) * ux * uy;
}

/**
 * Wind vector (m/s) at arena (x, y) mm and time t s, written into out[0..1].
 * Gusts are noise patches scrolling downwind, shaped so calm stretches separate the gusts.
 * @param {WindParams} p
 * @param {number} x
 * @param {number} y
 * @param {number} t
 * @param {Float64Array} out
 */
export function windAt(p, x, y, t, out) {
  const a = p.direction_deg * Math.PI / 180;
  const dx = Math.cos(a);
  const dy = Math.sin(a);
  const travel = p.gust_travel_mps * 1000 * t;
  const u = (x - dx * travel) / p.gust_scale_mm;
  const v = (y - dy * travel) / p.gust_scale_mm;
  const n = 0.65 * valueNoise(u, v) + 0.35 * valueNoise(2.1 * u + 17.3, 2.1 * v - 5.7);
  const gust = p.gust_mps * Math.max(0, (n - 0.4) / 0.6) ** 2;
  const speed = p.speed_mps + gust;
  out[0] = dx * speed;
  out[1] = dy * speed;
}

/**
 * @typedef {object} PlantField
 * @property {number} count
 * @property {Float32Array} x arena mm
 * @property {Float32Array} y arena mm
 * @property {Float32Array} rotation rad
 * @property {Float32Array} scale relative to the mean plant size
 * @property {Float32Array} freq natural-frequency multiplier
 * @property {Float32Array} leanX tip angle components, arena axes, rad
 * @property {Float32Array} leanY
 * @property {Float32Array} velX
 * @property {Float32Array} velY
 * @property {Float32Array} windX wind sampled at the plant this step, m/s
 * @property {Float32Array} windY
 */

/**
 * Scatters plants uniformly over a region, at rest.
 * @param {number} count
 * @param {{x: [number, number], y: [number, number]}} region arena mm
 * @param {number} sizeJitter fraction, e.g. 0.2 for +-20%
 * @param {number} seed
 * @returns {PlantField}
 */
export function createPlants(count, region, sizeJitter, seed) {
  const rand = mulberry32(seed);
  const f = () => new Float32Array(count);
  const field = { count, x: f(), y: f(), rotation: f(), scale: f(), freq: f(), leanX: f(), leanY: f(), velX: f(),
    velY: f(), windX: f(), windY: f() };
  for (let i = 0; i < count; i++) {
    field.x[i] = region.x[0] + rand() * (region.x[1] - region.x[0]);
    field.y[i] = region.y[0] + rand() * (region.y[1] - region.y[0]);
    field.rotation[i] = rand() * 2 * Math.PI;
    field.scale[i] = 1 + sizeJitter * (2 * rand() - 1);
    field.freq[i] = 0.85 + 0.3 * rand();
  }
  return field;
}

/**
 * Samples the wind at every plant and steps its lean spring (semi-implicit Euler). Taller plants lean further
 * and sway slower. Mutates the field's lean, velocity and wind arrays in place.
 * @param {PlantField} field
 * @param {WindParams} p
 * @param {number} t s
 * @param {number} dt s
 */
export function stepPlants(field, p, t, dt) {
  const w = new Float64Array(2);
  const maxLean = p.max_lean_deg * Math.PI / 180;
  const limit = 1.25 * maxLean;
  for (let i = 0; i < field.count; i++) {
    windAt(p, field.x[i], field.y[i], t, w);
    field.windX[i] = w[0];
    field.windY[i] = w[1];
    const speed = Math.hypot(w[0], w[1]);
    const s = field.scale[i];
    const target = speed > 1e-6 ? maxLean * Math.tanh(speed * s / p.speed_ref_mps) / speed : 0;
    const omega = 2 * Math.PI * p.spring_hz * field.freq[i] / Math.sqrt(s);
    const k = omega * omega;
    const c = 2 * p.damping * omega;
    field.velX[i] += (k * (w[0] * target - field.leanX[i]) - c * field.velX[i]) * dt;
    field.velY[i] += (k * (w[1] * target - field.leanY[i]) - c * field.velY[i]) * dt;
    let lx = field.leanX[i] + field.velX[i] * dt;
    let ly = field.leanY[i] + field.velY[i] * dt;
    const lean = Math.hypot(lx, ly);
    if (lean > limit) {
      lx *= limit / lean;
      ly *= limit / lean;
    }
    field.leanX[i] = lx;
    field.leanY[i] = ly;
  }
}

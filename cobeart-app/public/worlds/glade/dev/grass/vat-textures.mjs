// Grass VAT consumer helpers (GP roadmap, Phase 5, G51 and G52): the grass profile check
// (../plant/grass_vat_profile.md), the parameters the shader needs, the stage pair around a model time, and the
// texture packing of the organ blocks. Pure module: no DOM, no WebGL, testable in node.

/** @typedef {import('../vat/vat.mjs').Vat} Vat */
/** @typedef {import('../vat/vat.mjs').VatBlock} VatBlock */

export const GRASS_SPECIES = 'grass';
export const GRASS_SPECIES_VERSION = 1;

/**
 * Every block of the grass profile: [domain, timing, components, storage].
 * @type {Readonly<Record<string, readonly ['vertex' | 'organ', 'static' | 'stage', number, string]>>}
 */
export const GRASS_BLOCKS = Object.freeze({
  position: ['vertex', 'stage', 3, 'f16'],
  normal: ['vertex', 'stage', 3, 'oct16'],
  organ: ['vertex', 'static', 1, 'u32'],
  kind: ['vertex', 'static', 1, 'u32'],
  s: ['vertex', 'static', 1, 'f32'],
  uv: ['vertex', 'static', 2, 'f32'],
  lifespan: ['organ', 'static', 2, 'f32'],
  senescence: ['organ', 'static', 3, 'f32'],
  draw: ['organ', 'static', 1, 'f32'],
  phase: ['organ', 'static', 1, 'f32'],
  tiller: ['organ', 'static', 1, 'u32'],
  tiller_az: ['organ', 'static', 1, 'f32'],
  tiller_base: ['organ', 'static', 3, 'f32'],
  tiller_axis: ['organ', 'static', 3, 'f32'],
  side: ['organ', 'static', 3, 'f32'],
  collar: ['organ', 'stage', 3, 'f32'],
  direction: ['organ', 'stage', 3, 'f32'],
  arch: ['organ', 'stage', 1, 'f32'],
  sheath_length: ['organ', 'stage', 1, 'f32'],
  emergence: ['organ', 'stage', 1, 'f32'],
});

/** The field names of decline.py's DeclineParams, read from `species_params.params`. */
export const DECLINE_PARAM_NAMES = Object.freeze([
  'V_DEAD', 'DECLINE_AGE_SPREAD', 'DECLINE_AGE_REF', 'DECLINE_JITTER', 'DECLINE_RESIDUAL', 'SHRIVEL_FRACTION',
  'ANGLE_SENESCENT_DEG', 'SHRIVEL_ANGLE_DEG', 'SHRIVEL_ARCH_RAD', 'DECLINE_SPLAY_DEG',
]);

const REBAKE = 'rebake with plant/bake_grass_vat.py (see plant/grass_vat_profile.md)';

/**
 * Throws an Error naming the field unless `vat` is a grass bake of profile version 1 with every profile block.
 * @param {Vat} vat
 */
export function checkGrassVat(vat) {
  if (vat.species !== GRASS_SPECIES) {
    throw new Error(`grass vat: species is ${JSON.stringify(vat.species)}, expected '${GRASS_SPECIES}'; load a grass bake`);
  }
  if (vat.speciesVersion !== GRASS_SPECIES_VERSION) {
    throw new Error(`grass vat: species_version is ${JSON.stringify(vat.speciesVersion)}, this renderer reads ` +
      `${GRASS_SPECIES_VERSION}; ${REBAKE}`);
  }
  if (vat.frame?.up !== '+Z' || vat.frame?.unit !== 'm') {
    throw new Error(`grass vat: frame is ${JSON.stringify(vat.frame)}, expected {up: '+Z', unit: 'm'}; ${REBAKE}`);
  }
  for (const [name, [domain, timing, components, storage]] of Object.entries(GRASS_BLOCKS)) {
    const block = vat.blocks.get(name);
    if (!block) throw new Error(`grass vat: block '${name}' is missing; ${REBAKE}`);
    const got = [block.domain, block.timing, block.components, block.storage];
    const want = [domain, timing, components, storage];
    if (got.some((value, k) => value !== want[k])) {
      throw new Error(`grass vat: block '${name}' is ${got.join(' ')} (domain timing components storage), ` +
        `expected ${want.join(' ')}; ${REBAKE}`);
    }
  }
}

/**
 * A finite number at `obj[key]`, else an Error naming `where.key`.
 * @param {Record<string, unknown> | undefined} obj
 * @param {string} key
 * @param {string} where
 * @returns {number}
 */
function numberAt(obj, key, where) {
  const value = obj?.[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`grass vat: ${where}.${key} is missing or not a finite number (got ${JSON.stringify(value)}); ` +
      REBAKE);
  }
  return value;
}

/**
 * @typedef {object} GrassParams
 * @property {Record<string, number>} decline the DeclineParams fields by name
 * @property {number} fadeDuration P, FADE_DURATION
 * @property {number} sheathPlateauM m, SHEATH_PLATEAU_M (the wind's stem height)
 * @property {number} collarBend geometry.collar_bend
 * @property {number} tEnd P, the last stage time
 */

/**
 * The parameters the grass shader reads from `species_params`.
 * @param {Vat} vat
 * @returns {GrassParams}
 */
export function grassParams(vat) {
  const species = /** @type {Record<string, any>} */ (vat.speciesParams);
  const params = species?.params;
  const geometry = species?.geometry;
  /** @type {Record<string, number>} */
  const decline = {};
  for (const name of DECLINE_PARAM_NAMES) decline[name] = numberAt(params, name, 'species_params.params');
  const fadeDuration = numberAt(params, 'FADE_DURATION', 'species_params.params');
  const sheathPlateauM = numberAt(params, 'SHEATH_PLATEAU_M', 'species_params.params');
  const collarBend = numberAt(geometry, 'collar_bend', 'species_params.geometry');
  for (const [name, value] of [['FADE_DURATION', fadeDuration], ['SHEATH_PLATEAU_M', sheathPlateauM],
    ['collar_bend', collarBend], ['DECLINE_AGE_REF', decline.DECLINE_AGE_REF],
    ['SHRIVEL_FRACTION', decline.SHRIVEL_FRACTION]]) {
    if (!(value > 0)) throw new Error(`grass vat: species_params ${name} must be > 0, got ${value}; ${REBAKE}`);
  }
  if (!(decline.V_DEAD > 0 && decline.V_DEAD < 1)) {
    throw new Error(`grass vat: species_params.params.V_DEAD must be in (0, 1), got ${decline.V_DEAD}; ${REBAKE}`);
  }
  const tEnd = vat.stageTimes[vat.stageTimes.length - 1];
  if (!Number.isFinite(tEnd)) throw new Error('grass vat: stage_times is empty; ' + REBAKE);
  return { decline, fadeDuration, sheathPlateauM, collarBend, tEnd };
}

/**
 * @typedef {object} StagePair
 * @property {number} i0 stage below (or at) t
 * @property {number} i1 stage above t (i0 at and beyond the ends)
 * @property {number} w blend weight of i1, in [0, 1)
 */

/**
 * The two stages around model time `t`, blended linearly (G52). Clamps to the first and last stage. At an exact
 * stage time `i0` is that stage and `w` is 0. The shader runs the same binary search.
 * @param {ArrayLike<number>} stageTimes strictly increasing
 * @param {number} t
 * @returns {StagePair}
 */
export function stagePair(stageTimes, t) {
  const last = stageTimes.length - 1;
  if (last < 0) throw new Error('grass vat: stagePair needs at least one stage time');
  if (!Number.isFinite(t)) throw new Error(`grass vat: stagePair time must be finite, got ${t}`);
  if (t <= stageTimes[0]) return { i0: 0, i1: 0, w: 0 };
  if (t >= stageTimes[last]) return { i0: last, i1: last, w: 0 };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (stageTimes[mid] <= t) lo = mid;
    else hi = mid;
  }
  return { i0: lo, i1: hi, w: (t - stageTimes[lo]) / (stageTimes[hi] - stageTimes[lo]) };
}

/**
 * Per organ, 1 if it is alive at model time `t` (`appear <= t < vanish`), else 0.
 * @param {Vat} vat
 * @param {number} t
 * @returns {Uint8Array}
 */
export function aliveMask(vat, t) {
  const lifespan = vat.blocks.get('lifespan');
  if (!lifespan) throw new Error(`grass vat: block 'lifespan' is missing; ${REBAKE}`);
  const mask = new Uint8Array(vat.organCount);
  for (let o = 0; o < vat.organCount; o++) {
    mask[o] = lifespan.raw[2 * o] <= t && t < lifespan.raw[2 * o + 1] ? 1 : 0;
  }
  return mask;
}

/**
 * Number of organs alive at model time `t` (`appear <= t < vanish`).
 * @param {Vat} vat
 * @param {number} t
 * @returns {number}
 */
export function aliveOrganCount(vat, t) {
  return aliveMask(vat, t).reduce((sum, alive) => sum + alive, 0);
}

/**
 * @typedef {object} VertexTextureLayout
 * @property {number} width texels per row
 * @property {number} rowsPerStage rows one stage spans (1 when vertexCount fits in a row, as G51 lays it out)
 */

/**
 * Layout of a vertex-stage texture under a texture size limit: vertex i of stage k sits at
 * (i % width, k * rowsPerStage + floor(i / width)). Throws a named Error when no layout fits.
 * @param {number} vertexCount
 * @param {number} stageCount
 * @param {number} maxSize MAX_TEXTURE_SIZE
 * @returns {VertexTextureLayout}
 */
export function vertexTextureLayout(vertexCount, stageCount, maxSize) {
  const rowsPerStage = Math.ceil(vertexCount / maxSize);
  const width = Math.ceil(vertexCount / rowsPerStage);
  if (rowsPerStage * stageCount > maxSize) {
    throw new Error(`grass vat: ${vertexCount} vertices x ${stageCount} stages need ${rowsPerStage * stageCount} ` +
      `texture rows, above MAX_TEXTURE_SIZE ${maxSize}; bake with fewer --samples or a lower resolution`);
  }
  return { width, rowsPerStage };
}

/**
 * A stage-major vertex block (`perVertex` values per vertex) padded to `layout`: each stage's run is followed by
 * `width * rowsPerStage - vertexCount` zero texels. Returns `raw` itself when no padding is needed.
 * @template {Uint16Array | Int16Array | Float32Array} T
 * @param {T} raw
 * @param {number} vertexCount
 * @param {number} stageCount
 * @param {number} perVertex
 * @param {VertexTextureLayout} layout
 * @returns {T}
 */
export function padVertexStages(raw, vertexCount, stageCount, perVertex, layout) {
  const texels = layout.width * layout.rowsPerStage;
  if (texels === vertexCount) return raw;
  const out = /** @type {T} */ (new /** @type {any} */ (raw.constructor)(texels * stageCount * perVertex));
  for (let k = 0; k < stageCount; k++) {
    out.set(raw.subarray(k * vertexCount * perVertex, (k + 1) * vertexCount * perVertex), k * texels * perVertex);
  }
  return out;
}

/** Rows of the organ-static texture (RGBA per row, x the organ). */
export const ORGAN_STATIC_ROWS = Object.freeze([
  'appear, vanish, draw, phase',
  'senescent_time, senescence_duration, death_time, tiller',
  'tiller_base xyz, tiller_az',
  'tiller_axis xyz, 0',
  'side xyz, 0',
]);

/** Layers of the organ-stage texture array (RGBA per layer, x the organ, y the stage). */
export const ORGAN_STAGE_LAYERS = Object.freeze([
  'collar xyz, arch',
  'direction xyz, sheath_length',
  'emergence, 0, 0, 0',
]);

/**
 * Copies `components` values per element from `block` into RGBA texels at channel `channel`.
 * @param {Float32Array} out
 * @param {number} texelBase first texel of the run
 * @param {ArrayLike<number>} raw
 * @param {number} rawBase first value of the run
 * @param {number} count elements
 * @param {number} components
 * @param {number} channel
 */
function scatter(out, texelBase, raw, rawBase, count, components, channel) {
  for (let e = 0; e < count; e++) {
    for (let c = 0; c < components; c++) out[(texelBase + e) * 4 + channel + c] = raw[rawBase + e * components + c];
  }
}

/**
 * The organ-static texture data, RGBA32F, width `organCount`, height `ORGAN_STATIC_ROWS.length` (row-major).
 * `never` stays f32 (1e30).
 * @param {Vat} vat
 * @returns {Float32Array}
 */
export function packOrganStatic(vat) {
  const n = vat.organCount;
  const out = new Float32Array(n * ORGAN_STATIC_ROWS.length * 4);
  const raw = (/** @type {string} */ name) => /** @type {VatBlock} */ (vat.blocks.get(name)).raw;
  scatter(out, 0, raw('lifespan'), 0, n, 2, 0);
  scatter(out, 0, raw('draw'), 0, n, 1, 2);
  scatter(out, 0, raw('phase'), 0, n, 1, 3);
  scatter(out, n, raw('senescence'), 0, n, 3, 0);
  scatter(out, n, raw('tiller'), 0, n, 1, 3);
  scatter(out, 2 * n, raw('tiller_base'), 0, n, 3, 0);
  scatter(out, 2 * n, raw('tiller_az'), 0, n, 1, 3);
  scatter(out, 3 * n, raw('tiller_axis'), 0, n, 3, 0);
  scatter(out, 4 * n, raw('side'), 0, n, 3, 0);
  return out;
}

/**
 * The organ-stage texture array data, RGBA32F, width `organCount`, height `stage_count`,
 * `ORGAN_STAGE_LAYERS.length` layers (layer-major, then stage, then organ).
 * @param {Vat} vat
 * @returns {Float32Array}
 */
export function packOrganStage(vat) {
  const n = vat.organCount;
  const stages = vat.stageTimes.length;
  const layer = n * stages;
  const out = new Float32Array(layer * ORGAN_STAGE_LAYERS.length * 4);
  const raw = (/** @type {string} */ name) => /** @type {VatBlock} */ (vat.blocks.get(name)).raw;
  for (let k = 0; k < stages; k++) {
    scatter(out, k * n, raw('collar'), k * n * 3, n, 3, 0);
    scatter(out, k * n, raw('arch'), k * n, n, 1, 3);
    scatter(out, layer + k * n, raw('direction'), k * n * 3, n, 3, 0);
    scatter(out, layer + k * n, raw('sheath_length'), k * n, n, 1, 3);
    scatter(out, 2 * layer + k * n, raw('emergence'), k * n, n, 1, 0);
  }
  return out;
}

// Glade grass VAT helpers (public/worlds/glade/dev/grass/vat-textures.mjs, an ES module) on a synthetic grass Vat.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const load = () => import('../public/worlds/glade/dev/grass/vat-textures.mjs');

const NEVER = Math.fround(1.0e30);
const STAGE_TIMES = [0, 0.5, 1.5, 3];
const ORGANS = 3;
const VERTICES = 4;

const DECLINE = {
  V_DEAD: 0.15, DECLINE_AGE_SPREAD: 0.4, DECLINE_AGE_REF: 3, DECLINE_JITTER: 0.1, DECLINE_RESIDUAL: 0.7,
  SHRIVEL_FRACTION: 1, ANGLE_SENESCENT_DEG: 12, SHRIVEL_ANGLE_DEG: 25, SHRIVEL_ARCH_RAD: 0.8, DECLINE_SPLAY_DEG: 25,
};

const ARRAYS = { f32: Float32Array, f16: Uint16Array, u32: Uint32Array, oct16: Int16Array };

/** A grass Vat with every profile block; organ-block values encode (block, element, component, stage). */
async function syntheticVat() {
  const { GRASS_BLOCKS } = await load();
  const blocks = new Map();
  for (const [name, [domain, timing, components, storage]] of Object.entries(GRASS_BLOCKS)) {
    const count = domain === 'vertex' ? VERTICES : ORGANS;
    const stages = timing === 'stage' ? STAGE_TIMES.length : 1;
    const perElement = storage === 'oct16' ? 2 : components;
    const raw = new ARRAYS[storage](count * perElement * stages);
    raw.forEach((_, k) => { raw[k] = storage === 'u32' ? k % 2 : k + 1; });
    blocks.set(name, { name, domain, timing, components, storage, raw, meta: null });
  }
  // appear, vanish per organ: alive [0, 1.5), [0.5, never), [3, never)
  blocks.get('lifespan').raw.set([0, 1.5, 0.5, NEVER, 3, NEVER]);
  blocks.get('tiller').raw.set([0, 1, 2]);
  return {
    species: 'grass',
    speciesVersion: 1,
    frame: { up: '+Z', unit: 'm' },
    speciesParams: {
      model: 'grass.lpy', seed: 1,
      params: { ...DECLINE, FADE_DURATION: 0.5, SHEATH_PLATEAU_M: 0.08, ELONGATION_AGE: 2 },
      geometry: { collar_bend: 0.15, blade_segments: 16 },
    },
    vertexCount: VERTICES,
    organCount: ORGANS,
    organIds: ['L0.1', 'L0.2', 'L1.1'],
    stageTimes: STAGE_TIMES,
    previewStage: STAGE_TIMES.length - 1,
    never: NEVER,
    indices: new Uint32Array([0, 1, 2, 1, 2, 3]),
    blocks,
  };
}

test('checkGrassVat accepts a synthetic grass bake', async () => {
  const { checkGrassVat } = await load();
  checkGrassVat(await syntheticVat());
});

test('checkGrassVat rejects wrong species, version and frame, naming the field', async () => {
  const { checkGrassVat } = await load();
  const cases = [
    [(vat) => { vat.species = 'tiny'; }, /species/],
    [(vat) => { vat.speciesVersion = 2; }, /species_version/],
    [(vat) => { vat.frame = { up: '+Y', unit: 'm' }; }, /frame/],
    [(vat) => { vat.frame = { up: '+Z', unit: 'mm' }; }, /frame/],
  ];
  for (const [edit, message] of cases) {
    const vat = await syntheticVat();
    edit(vat);
    assert.throws(() => checkGrassVat(vat), message);
  }
});

test('checkGrassVat rejects each missing or misshapen profile block', async () => {
  const { checkGrassVat, GRASS_BLOCKS } = await load();
  for (const name of Object.keys(GRASS_BLOCKS)) {
    const missing = await syntheticVat();
    missing.blocks.delete(name);
    assert.throws(() => checkGrassVat(missing), new RegExp(`block '${name}' is missing`));
    for (const [field, wrong] of [['domain', (b) => (b.domain === 'vertex' ? 'organ' : 'vertex')],
      ['timing', (b) => (b.timing === 'stage' ? 'static' : 'stage')], ['components', (b) => b.components + 1],
      ['storage', (b) => (b.storage === 'f32' ? 'f16' : 'f32')]]) {
      const vat = await syntheticVat();
      const block = vat.blocks.get(name);
      block[field] = wrong(block);
      assert.throws(() => checkGrassVat(vat), new RegExp(`block '${name}' is .*expected`), `${name}.${field}`);
    }
  }
});

test('grassParams reads the decline, fade, stem and collar parameters', async () => {
  const { grassParams } = await load();
  const params = grassParams(await syntheticVat());
  assert.deepEqual(params, {
    decline: DECLINE, fadeDuration: 0.5, sheathPlateauM: 0.08, collarBend: 0.15, tEnd: 3,
  });
});

test('grassParams throws naming a missing key', async () => {
  const { grassParams, DECLINE_PARAM_NAMES } = await load();
  for (const name of [...DECLINE_PARAM_NAMES, 'FADE_DURATION', 'SHEATH_PLATEAU_M']) {
    const vat = await syntheticVat();
    delete vat.speciesParams.params[name];
    assert.throws(() => grassParams(vat), new RegExp(`species_params\\.params\\.${name} is missing`));
  }
  const vat = await syntheticVat();
  delete vat.speciesParams.geometry.collar_bend;
  assert.throws(() => grassParams(vat), /species_params\.geometry\.collar_bend is missing/);
  const bad = await syntheticVat();
  bad.speciesParams.params.V_DEAD = 1;
  assert.throws(() => grassParams(bad), /V_DEAD must be in \(0, 1\)/);
});

test('stagePair at and between stage times and at both ends', async () => {
  const { stagePair } = await load();
  for (let k = 1; k < STAGE_TIMES.length - 1; k++) {
    assert.deepEqual(stagePair(STAGE_TIMES, STAGE_TIMES[k]), { i0: k, i1: k + 1, w: 0 });
  }
  assert.deepEqual(stagePair(STAGE_TIMES, 0), { i0: 0, i1: 0, w: 0 });
  assert.deepEqual(stagePair(STAGE_TIMES, 0.25), { i0: 0, i1: 1, w: 0.5 });
  assert.deepEqual(stagePair(STAGE_TIMES, 1.25), { i0: 1, i1: 2, w: 0.75 });
  assert.deepEqual(stagePair(STAGE_TIMES, 2.25), { i0: 2, i1: 3, w: 0.5 });
  assert.deepEqual(stagePair(STAGE_TIMES, 3), { i0: 3, i1: 3, w: 0 });
  assert.deepEqual(stagePair(STAGE_TIMES, 7), { i0: 3, i1: 3, w: 0 });
  assert.deepEqual(stagePair(STAGE_TIMES, -1), { i0: 0, i1: 0, w: 0 });
  assert.deepEqual(stagePair([2], 5), { i0: 0, i1: 0, w: 0 });
  assert.throws(() => stagePair(STAGE_TIMES, NaN), /finite/);
});

test('aliveOrganCount counts appear <= t < vanish', async () => {
  const { aliveOrganCount } = await load();
  const vat = await syntheticVat();
  assert.equal(aliveOrganCount(vat, -0.1), 0);
  assert.equal(aliveOrganCount(vat, 0), 1);
  assert.equal(aliveOrganCount(vat, 0.5), 2);
  assert.equal(aliveOrganCount(vat, 1.5), 1);
  assert.equal(aliveOrganCount(vat, 3), 2);
});

test('vertex texture layout keeps one row per stage when it fits and wraps otherwise', async () => {
  const { vertexTextureLayout, padVertexStages } = await load();
  assert.deepEqual(vertexTextureLayout(8280, 49, 16384), { width: 8280, rowsPerStage: 1 });
  assert.deepEqual(vertexTextureLayout(8280, 49, 8192), { width: 4140, rowsPerStage: 2 });
  assert.deepEqual(vertexTextureLayout(5, 2, 4), { width: 3, rowsPerStage: 2 });
  assert.throws(() => vertexTextureLayout(5, 3, 4), /MAX_TEXTURE_SIZE 4/);
  const raw = new Uint16Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]); // 5 vertices x 2 stages, 1 value each
  assert.equal(padVertexStages(raw, 5, 2, 1, { width: 5, rowsPerStage: 1 }), raw);
  assert.deepEqual(Array.from(padVertexStages(raw, 5, 2, 1, { width: 3, rowsPerStage: 2 })),
    [1, 2, 3, 4, 5, 0, 6, 7, 8, 9, 10, 0]);
});

test('organ textures put each block in its documented row, layer and channel', async () => {
  const { packOrganStatic, packOrganStage, ORGAN_STATIC_ROWS, ORGAN_STAGE_LAYERS } = await load();
  const vat = await syntheticVat();
  const raw = (name) => vat.blocks.get(name).raw;
  const st = packOrganStatic(vat);
  assert.equal(st.length, ORGANS * ORGAN_STATIC_ROWS.length * 4);
  const texel = (row, o) => Array.from(st.subarray((row * ORGANS + o) * 4, (row * ORGANS + o) * 4 + 4));
  const o = 2;
  assert.deepEqual(texel(0, o), [raw('lifespan')[4], raw('lifespan')[5], raw('draw')[o], raw('phase')[o]]);
  assert.deepEqual(texel(1, o), [...raw('senescence').slice(6, 9), 2]);
  assert.deepEqual(texel(2, o), [...raw('tiller_base').slice(6, 9), raw('tiller_az')[o]]);
  assert.deepEqual(texel(3, o), [...raw('tiller_axis').slice(6, 9), 0]);
  assert.deepEqual(texel(4, o), [...raw('side').slice(6, 9), 0]);

  const sg = packOrganStage(vat);
  const stages = STAGE_TIMES.length;
  assert.equal(sg.length, ORGANS * stages * ORGAN_STAGE_LAYERS.length * 4);
  const k = 2;
  const at = (layer) => Array.from(sg.subarray(((layer * stages + k) * ORGANS + o) * 4, ((layer * stages + k) * ORGANS + o) * 4 + 4));
  const e = k * ORGANS + o;
  assert.deepEqual(at(0), [...raw('collar').slice(3 * e, 3 * e + 3), raw('arch')[e]]);
  assert.deepEqual(at(1), [...raw('direction').slice(3 * e, 3 * e + 3), raw('sheath_length')[e]]);
  assert.deepEqual(at(2), [raw('emergence')[e], 0, 0, 0]);
});

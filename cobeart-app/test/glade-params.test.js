// Glade parameter contract (public/worlds/glade/params.mjs, an ES module imported from this CommonJS test).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const CONTRACT_DIR = path.join(__dirname, '..', '..', 'contract');
const ARENA = { x: [-3000, 3000], y: [-3000, 3000], z: [0, 2500] };

const loadParams = () => import('../public/worlds/glade/params.mjs');

/** Asserts that `fn` throws a GladeParamError naming `param`, with every one of `parts` in its message. */
const assertParamError = async (fn, param, ...parts) => {
  const { GladeParamError } = await loadParams();
  assert.throws(fn, (err) => {
    assert.ok(err instanceof GladeParamError, `expected GladeParamError, got ${err}`);
    assert.equal(err.param, param);
    for (const part of parts) assert.ok(err.message.includes(part), `"${part}" not in: ${err.message}`);
    return true;
  });
};

/** Defaults with `overrides` applied. */
const withDefaults = async (overrides) => ({ ...(await loadParams()).defaultParams(), ...overrides });

test('foot_role options match the contract role enum', async () => {
  const { PARAMS, ROLES } = await loadParams();
  const schema = JSON.parse(fs.readFileSync(path.join(CONTRACT_DIR, 'frame.schema.json'), 'utf8'));
  assert.deepEqual([...ROLES], schema.$defs.role.enum);
  assert.deepEqual(PARAMS.find((s) => s.key === 'foot_role').options, ROLES);
});

test('PARAMS covers the brief table without plant_max; only h and N_frames are structural', async () => {
  const { PARAMS, PLANT_MAX } = await loadParams();
  assert.deepEqual(PARAMS.map((s) => s.key), [
    'foot_role', 'h', 'R', 'input_power', 'foot_timeout_ms', 'E_cap', 'D', 'D_spill', 'r', 'seed_energy',
    'N_frames', 'delta', 'E_spawn_min', 'lambda_max', 'min_spacing_mm', 'sprite_size_mm', 'size_jitter',
    'brightness_jitter', 'shadow_strength', 'shadow_scale', 'shadow_blur', 'shadow_offset_mm', 'dt',
    'max_steps_per_frame', 'seed',
  ]);
  assert.equal(PLANT_MAX, 1);
  assert.deepEqual(PARAMS.filter((s) => s.kind === 'structural').map((s) => s.key), ['h', 'N_frames']);
  for (const spec of PARAMS) {
    assert.ok(['number', 'int', 'enum'].includes(spec.type), spec.key);
    assert.ok(spec.description.length > 0, spec.key);
    if (spec.type !== 'enum') {
      assert.ok(spec.min <= spec.default && spec.default <= spec.max, `${spec.key} default outside its range`);
      assert.ok(spec.step > 0, spec.key);
    }
  }
});

test('defaults are frozen and validate', async () => {
  const { defaultParams, validateParams } = await loadParams();
  const p = defaultParams();
  assert.ok(Object.isFrozen(p));
  assert.equal(p.h, 50);
  assert.equal(p.foot_role, 'right_hand');
  validateParams(p, ARENA);
});

test('every numeric spec has a finite range and a positive step', async () => {
  const { PARAMS } = await loadParams();
  for (const spec of PARAMS.filter((s) => s.type !== 'enum')) {
    assert.ok(Number.isFinite(spec.min) && Number.isFinite(spec.max) && spec.min < spec.max, spec.key);
    assert.ok(Number.isFinite(spec.step) && spec.step > 0, spec.key);
  }
});

test('each numeric bound validates alone, or fails only on its documented cross rule', async () => {
  const { PARAMS, GladeParamError, validateParams } = await loadParams();
  // Bounds that, with every other parameter at its default, break a cross rule: key:bound -> param named.
  const expected = new Map([
    ['h:min', 'seed_energy'],
    ['E_cap:min', 'E_spawn_min'],
    ['seed_energy:max', 'seed_energy'],
    ['E_spawn_min:min', 'seed_energy'],
    ['E_spawn_min:max', 'E_spawn_min'],
    ['min_spacing_mm:max', 'min_spacing_mm'],
  ]);
  for (const spec of PARAMS.filter((s) => s.type !== 'enum')) {
    for (const bound of ['min', 'max']) {
      const label = `${spec.key}:${bound}`;
      const p = await withDefaults({ [spec.key]: spec[bound] });
      if (!expected.has(label)) {
        validateParams(p, ARENA);
        continue;
      }
      assert.throws(() => validateParams(p, ARENA), (err) => err instanceof GladeParamError &&
        err.param === expected.get(label), label);
    }
  }
});

test('type and range violations name the parameter', async () => {
  const { validateParams } = await loadParams();
  for (const [key, value, part] of [
    ['R', -1, '[50, 1000]'],
    ['N_frames', 30.5, 'integer'],
    ['dt', Number.NaN, 'dt = NaN'],
    ['r', '0.3', 'r = "0.3"'],
    ['foot_role', 'left_knee', 'left_foot'],
    ['max_steps_per_frame', 0, '[1, 16]'],
  ]) {
    const p = await withDefaults({ [key]: value });
    await assertParamError(() => validateParams(p, ARENA), key, part);
  }
});

test('unknown and missing keys are rejected by name', async () => {
  const { defaultParams, validateParams } = await loadParams();
  const extra = { ...defaultParams(), E_capp: 1 };
  await assertParamError(() => validateParams(extra, ARENA), 'E_capp', 'not a Glade parameter');
  const { D, ...missing } = defaultParams();
  assert.equal(D, 2500);
  await assertParamError(() => validateParams(missing, ARENA), 'D', 'missing', '2500');
});

test('every cross-parameter rule throws with the parameter named', async () => {
  const { validateParams } = await loadParams();
  const cases = [
    [{ E_spawn_min: 400 }, 'E_spawn_min', 'E_cap'],
    [{ E_spawn_min: 7 }, 'seed_energy', 'E_spawn_min'],
    [{ h: 10, min_spacing_mm: 15, E_spawn_min: 300 }, 'min_spacing_mm', 'h'],
    [{ delta: 0.5 }, 'delta', '0.49'],
    [{ max_steps_per_frame: 0 }, 'max_steps_per_frame', '[1, 16]'],
    [{ h: 35 }, 'h', '6000 x 6000', 'nearby valid values: 25, 30, 40, 50'],
    [{ h: 15, E_spawn_min: 100 }, 'D_spill', 'h = 15 mm', 'dt = ', 'budget 2000000', 'lower D_spill to at most'],
    [{ h: 15, E_spawn_min: 100, D: 25000, dt: 1 / 15 }, 'D', 'h = 15 mm', 'lower D to at most'],
  ];
  for (const [overrides, param, ...parts] of cases) {
    const p = await withDefaults(overrides);
    await assertParamError(() => validateParams(p, ARENA), param, ...parts);
  }
});

test('h must divide both spans of a non-square arena', async () => {
  const { validateParams } = await loadParams();
  const arena = { x: [-3000, 3000], y: [-2000, 2000], z: [0, 2500] };
  validateParams(await withDefaults({ h: 50 }), arena);
  const p = await withDefaults({ h: 60, min_spacing_mm: 15 });
  await assertParamError(() => validateParams(p, arena), 'h', '6000 x 4000', '40, 50, 80, 100');
});

test('the substep budget suggestion fits and the extremes at h = 50 stay within it', async () => {
  const { GladeParamError, validateParams } = await loadParams();
  const p = await withDefaults({ h: 15, E_spawn_min: 100 });
  let fit = null;
  try {
    validateParams(p, ARENA);
  } catch (err) {
    assert.ok(err instanceof GladeParamError);
    fit = Number(/at most (\d+)/.exec(err.message)[1]);
  }
  assert.ok(fit > 0);
  validateParams({ ...p, D_spill: fit }, ARENA);
  validateParams(await withDefaults({ D: 25000, D_spill: 1e6, dt: 1 / 15 }), ARENA);
});

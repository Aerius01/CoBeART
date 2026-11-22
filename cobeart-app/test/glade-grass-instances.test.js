// Per-instance growth values for the grass demo (public/worlds/glade/dev/grass/instances.mjs, an ES module).
const { test } = require('node:test');
const assert = require('node:assert/strict');

const load = () => import('../public/worlds/glade/dev/grass/instances.mjs');

const BASE = { g: 0.5, g_spread: 0.3, v: 0.6, v_spread: 0.2, spatial: false, noise_scale_mm: 1000 };
const COUNT = 200;

/** Positions on a diagonal line through the arena scale. */
const line = (n, step) => ({
  x: Float32Array.from({ length: n }, (_, i) => 100 + i * step),
  y: Float32Array.from({ length: n }, (_, i) => 200 + i * step),
});

test('the same seed gives the same field and a different seed another', async () => {
  const { createGrowthField } = await load();
  const a = createGrowthField(COUNT, 5);
  const b = createGrowthField(COUNT, 5);
  const c = createGrowthField(COUNT, 6);
  assert.deepEqual(a.r, b.r);
  assert.deepEqual(a.q, b.q);
  assert.notDeepEqual(a.r, c.r);
  for (const value of [...a.r, ...a.q]) assert.ok(value >= -1 && value <= 1);
});

test('values are clamped to [0, 1]', async () => {
  const { createGrowthField, fillGrowth } = await load();
  const field = createGrowthField(COUNT, 1);
  const { x, y } = line(COUNT, 37);
  const out = new Float32Array(2 * COUNT);
  for (const spatial of [false, true]) {
    fillGrowth(field, { ...BASE, g: 0.9, g_spread: 1, v: 0.1, v_spread: 1, spatial }, x, y, out);
    for (const value of out) assert.ok(value >= 0 && value <= 1);
    assert.ok(out.some((value) => value === 0) && out.some((value) => value === 1));
  }
});

test('zero spreads give the globals exactly', async () => {
  const { createGrowthField, fillGrowth } = await load();
  const field = createGrowthField(COUNT, 2);
  const { x, y } = line(COUNT, 37);
  const out = new Float32Array(2 * COUNT);
  for (const spatial of [false, true]) {
    fillGrowth(field, { ...BASE, g_spread: 0, v_spread: 0, spatial }, x, y, out);
    for (let i = 0; i < COUNT; i++) {
      assert.equal(out[2 * i], Math.fround(BASE.g));
      assert.equal(out[2 * i + 1], Math.fround(BASE.v));
    }
  }
});

test('uniform spreads stay within the global plus or minus the spread', async () => {
  const { createGrowthField, fillGrowth } = await load();
  const field = createGrowthField(COUNT, 3);
  const { x, y } = line(COUNT, 37);
  const out = new Float32Array(2 * COUNT);
  fillGrowth(field, BASE, x, y, out);
  const eps = 1e-6;
  for (let i = 0; i < COUNT; i++) {
    assert.ok(Math.abs(out[2 * i] - BASE.g) <= BASE.g_spread + eps);
    assert.ok(Math.abs(out[2 * i + 1] - BASE.v) <= BASE.v_spread + eps);
  }
  assert.ok(new Set(out).size > COUNT / 2, 'the offsets vary between plants');
});

test('spatial mode is continuous and g and v differ', async () => {
  const { createGrowthField, fillGrowth } = await load();
  const field = createGrowthField(COUNT, 4);
  const params = { ...BASE, g: 0.5, g_spread: 0.5, v: 0.5, v_spread: 0.5, spatial: true, noise_scale_mm: 1000 };
  const near = line(COUNT, 5);
  const far = line(COUNT, 700);
  const outNear = new Float32Array(2 * COUNT);
  const outFar = new Float32Array(2 * COUNT);
  fillGrowth(field, params, near.x, near.y, outNear);
  fillGrowth(field, params, far.x, far.y, outFar);
  // 5 mm steps over a 1000 mm patch change the value by far less than 700 mm steps do.
  let stepNear = 0;
  let stepFar = 0;
  let differ = 0;
  for (let i = 1; i < COUNT; i++) {
    for (const k of [0, 1]) {
      stepNear = Math.max(stepNear, Math.abs(outNear[2 * i + k] - outNear[2 * (i - 1) + k]));
      stepFar = Math.max(stepFar, Math.abs(outFar[2 * i + k] - outFar[2 * (i - 1) + k]));
    }
    if (Math.abs(outFar[2 * i] - outFar[2 * i + 1]) > 1e-3) differ++;
  }
  assert.ok(stepNear < 0.05, `neighbouring values jump by ${stepNear}`);
  assert.ok(stepFar > 4 * stepNear, 'distant plants differ more than neighbours');
  assert.ok(differ > COUNT / 2, 'g and v follow different noise fields');
});

test('spatial mode rejects a non-positive noise scale and a short output array', async () => {
  const { createGrowthField, fillGrowth } = await load();
  const field = createGrowthField(4, 1);
  const { x, y } = line(4, 10);
  assert.throws(() => fillGrowth(field, { ...BASE, spatial: true, noise_scale_mm: 0 }, x, y, new Float32Array(8)),
    /noise_scale_mm/);
  assert.throws(() => fillGrowth(field, BASE, x, y, new Float32Array(7)), /needs 8/);
});

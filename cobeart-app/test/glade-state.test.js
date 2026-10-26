// Glade simulation state and RNG (public/worlds/glade/sim/state.mjs and sim/rng.mjs, ES modules).
const { test } = require('node:test');
const assert = require('node:assert/strict');

const ARENA = { x: [-3000, 3000], y: [-3000, 3000], z: [0, 2500] };

const loadState = () => import('../public/worlds/glade/sim/state.mjs');
const loadRng = () => import('../public/worlds/glade/sim/rng.mjs');
const loadParams = () => import('../public/worlds/glade/params.mjs');

const freshState = async (overrides = {}) => {
  const { createState } = await loadState();
  const { defaultParams } = await loadParams();
  return createState({ ...defaultParams(), ...overrides }, ARENA);
};

/** Appends `n` plants with ascending ids and recognisable field values (test helper, writes `state`). */
const addPlants = (state, n) => {
  const p = state.plants;
  for (let k = 0; k < n; k++) {
    const i = state.count++;
    p.x[i] = i; p.y[i] = -i; p.energy[i] = 0.5; p.id[i] = state.nextId++; p.seed[i] = 7 * i;
    p.frame[i] = i % 30; p.direction[i] = i % 2;
  }
};

test('seed 1 gives a stable sequence; the same seed repeats it, another differs', async () => {
  const { seedRng, nextU32 } = await loadRng();
  const draw = (seed, n) => {
    const s = seedRng(seed);
    return Array.from({ length: n }, () => nextU32(s, s));
  };
  assert.deepEqual(draw(1, 5), [1130556604, 2591592147, 3014952990, 960850752, 2734082507]);
  assert.deepEqual(draw(1, 1000), draw(1, 1000));
  assert.notDeepEqual(draw(2, 5), draw(1, 5));
});

test('nextU32 leaves its input state unchanged and writes the advanced state to its output', async () => {
  const { seedRng, nextU32, nextFloat } = await loadRng();
  const a = seedRng(1);
  const before = [...a];
  const b = new Uint32Array(4);
  const v = nextU32(a, b);
  assert.deepEqual([...a], before);
  assert.notDeepEqual([...b], before);
  assert.equal(nextU32(a, new Uint32Array(4)), v, 'the same input state gives the same draw');
  const f = nextFloat(b, b);
  assert.ok(f >= 0 && f < 1);
});

test('seedRng rejects a seed outside the uint32 range', async () => {
  const { seedRng } = await loadRng();
  assert.throws(() => seedRng(-1), /seed -1/);
  assert.throws(() => seedRng(1.5), /seed 1.5/);
  assert.throws(() => seedRng(2 ** 32), /seed 4294967296/);
  assert.equal(seedRng(0xffffffff).length, 4);
});

test('createState builds an empty 120 x 120 grid and pool, seeded from params.seed', async () => {
  const { INITIAL_CAPACITY } = await loadState();
  const { seedRng } = await loadRng();
  const s = await freshState();
  assert.deepEqual([s.grid.nx, s.grid.ny, s.grid.h, s.grid.x0, s.grid.y0], [120, 120, 50, -3000, -3000]);
  assert.equal(s.grid.density.length, 120 * 120);
  assert.ok(s.grid.density.every((d) => d === 0));
  assert.equal(s.capacity, INITIAL_CAPACITY);
  assert.equal(s.count, 0);
  assert.equal(s.nextId, 0);
  for (const arr of Object.values(s.plants)) assert.equal(arr.length, INITIAL_CAPACITY);
  assert.deepEqual([...s.rng], [...seedRng(1)]);
});

test('createState validates its params', async () => {
  const { GladeParamError } = await loadParams();
  await assert.rejects(() => freshState({ h: 35 }), GladeParamError);
});

test('cellOf maps positions to cells, x = x_max into the last cell, outside points clamped', async () => {
  const { cellOf } = await loadState();
  const { grid } = await freshState();
  assert.equal(cellOf(grid, -3000, -3000), 0);
  assert.equal(cellOf(grid, -2950.1, -3000), 0);
  assert.equal(cellOf(grid, -2950, -3000), 1);
  assert.equal(cellOf(grid, 3000, -3000), 119);
  assert.equal(cellOf(grid, -3000, 3000), 119 * 120);
  assert.equal(cellOf(grid, 3000, 3000), 120 * 120 - 1);
  assert.equal(cellOf(grid, 9999, -9999), 119);
});

test('bilinear weights sum to 1 and indices stay inside the grid, corners and edges included', async () => {
  const { bilinear, createBilinear } = await loadState();
  const { grid } = await freshState();
  const out = createBilinear();
  const n = grid.nx * grid.ny;
  const xs = [-3000, -2990, -2975, -1234.5, 0, 2975, 2990, 3000];
  for (const x of xs) {
    for (const y of xs) {
      bilinear(grid, x, y, out);
      const sum = out.weight.reduce((a, b) => a + b, 0);
      assert.ok(Math.abs(sum - 1) < 1e-12, `weights at (${x}, ${y}) sum to ${sum}`);
      for (const i of out.index) assert.ok(i >= 0 && i < n, `index ${i} at (${x}, ${y})`);
      for (const w of out.weight) assert.ok(w >= 0 && w <= 1);
    }
  }
});

/** Total weight `b` puts on `cell`. */
const weightOn = (b, cell) => b.index.reduce((acc, i, k) => acc + (i === cell ? b.weight[k] : 0), 0);

test('bilinear puts full weight on a cell centre and on the corner cell near a corner', async () => {
  const { bilinear, createBilinear } = await loadState();
  const { grid } = await freshState();
  const at = (x, y) => bilinear(grid, x, y, createBilinear());
  const centre = at(-3000 + 25 + 50 * 3, -3000 + 25 + 50 * 7);
  assert.ok(Math.abs(weightOn(centre, 7 * 120 + 3) - 1) < 1e-12);
  assert.ok(Math.abs(weightOn(at(-3000, -3000), 0) - 1) < 1e-12);
  assert.ok(Math.abs(weightOn(at(3000, 3000), 120 * 120 - 1) - 1) < 1e-12);
  const mid = at(-3000 + 50, -3000 + 25);
  assert.ok(Math.abs(weightOn(mid, 0) - 0.5) < 1e-12 && Math.abs(weightOn(mid, 1) - 0.5) < 1e-12);
});

test('cellOf and bilinear on a non-square arena (6000 x 4000 mm, h 50: 120 x 80 cells)', async () => {
  const { createState, cellOf, bilinear, createBilinear } = await loadState();
  const { defaultParams } = await loadParams();
  const { grid } = createState(defaultParams(), { x: [-3000, 3000], y: [-2000, 2000], z: [0, 2500] });
  assert.deepEqual([grid.nx, grid.ny, grid.density.length], [120, 80, 120 * 80]);
  assert.equal(cellOf(grid, 3000, 2000), 80 * 120 - 1);
  assert.equal(cellOf(grid, 3000, -2000), 119);
  assert.equal(cellOf(grid, -3000, 2000), 79 * 120);
  const b = bilinear(grid, -3000 + 25 + 50 * 100, -2000 + 25 + 50 * 10, createBilinear());
  assert.ok(Math.abs(weightOn(b, 10 * 120 + 100) - 1) < 1e-12);
});

test('bilinear on a 1 x N grid keeps every index in the single row', async () => {
  const { bilinear, createBilinear } = await loadState();
  const grid = { nx: 5, ny: 1, h: 10, x0: 0, y0: 0, density: new Float64Array(5) };
  const out = createBilinear();
  for (const [x, y] of [[0, 0], [5, 5], [12, 3], [25, 9], [50, 10]]) {
    bilinear(grid, x, y, out);
    assert.ok(Math.abs(out.weight.reduce((a, w) => a + w, 0) - 1) < 1e-12);
    for (const i of out.index) assert.ok(i >= 0 && i < 5, `index ${i} at (${x}, ${y})`);
  }
  bilinear(grid, 20, 5, out);
  assert.ok(Math.abs(weightOn(out, 1) - 0.5) < 1e-12 && Math.abs(weightOn(out, 2) - 0.5) < 1e-12);
});

test('ensureCapacity doubles, keeps the live prefix in id order and the state object', async () => {
  const { ensureCapacity, INITIAL_CAPACITY } = await loadState();
  const s = await freshState();
  addPlants(s, 10);
  const oldX = s.plants.x;
  assert.equal(ensureCapacity(s, INITIAL_CAPACITY, s.count), s);
  assert.equal(s.plants.x, oldX, 'no reallocation when the capacity suffices');
  assert.equal(ensureCapacity(s, INITIAL_CAPACITY * 2 + 1, s.count), s);
  assert.equal(s.capacity, INITIAL_CAPACITY * 4);
  for (const arr of Object.values(s.plants)) assert.equal(arr.length, INITIAL_CAPACITY * 4);
  assert.deepEqual([...s.plants.id.subarray(0, 10)], [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.deepEqual([...s.plants.seed.subarray(0, 10)], [0, 7, 14, 21, 28, 35, 42, 49, 56, 63]);
  assert.deepEqual([...s.plants.direction.subarray(0, 4)], [0, 1, 0, 1]);
  assert.equal(s.count, 10);
});

test('ensureCapacity keeps the explicit live prefix even when count is stale', async () => {
  const { ensureCapacity, INITIAL_CAPACITY } = await loadState();
  const s = await freshState();
  addPlants(s, 100);
  s.count = 3; // stale, as in a next buffer mid-step
  ensureCapacity(s, INITIAL_CAPACITY + 1, 100);
  assert.deepEqual([...s.plants.id.subarray(0, 100)], Array.from({ length: 100 }, (_, i) => i));
  assert.deepEqual([...s.plants.x.subarray(0, 100)], Array.from({ length: 100 }, (_, i) => i));
});

test('ensureCapacity rejects invalid needed and live', async () => {
  const { ensureCapacity, INITIAL_CAPACITY } = await loadState();
  const s = await freshState();
  assert.throws(() => ensureCapacity(s, -1, 0), RangeError);
  assert.throws(() => ensureCapacity(s, 1.5, 0), RangeError);
  assert.throws(() => ensureCapacity(s, 10, -1), RangeError);
  assert.throws(() => ensureCapacity(s, 10, INITIAL_CAPACITY + 1), /live 4097/);
});

test('cloneInto copies density, the pool prefix, count, nextId and rng, growing dst', async () => {
  const { cloneInto, ensureCapacity, INITIAL_CAPACITY } = await loadState();
  const src = await freshState({ seed: 9 });
  const dst = await freshState();
  ensureCapacity(src, INITIAL_CAPACITY + 1, 0);
  addPlants(src, 5);
  src.grid.density[17] = 3.5;
  assert.equal(cloneInto(src, dst), dst);
  assert.equal(dst.capacity, src.capacity);
  assert.equal(dst.count, 5);
  assert.equal(dst.nextId, 5);
  assert.deepEqual([...dst.rng], [...src.rng]);
  assert.equal(dst.grid.density[17], 3.5);
  for (const key of Object.keys(src.plants)) {
    assert.deepEqual([...dst.plants[key].subarray(0, 5)], [...src.plants[key].subarray(0, 5)], key);
    assert.notEqual(dst.plants[key], src.plants[key], `${key} must be a copy, not shared`);
  }
  assert.notEqual(dst.grid.density, src.grid.density);
  assert.notEqual(dst.rng, src.rng);
});

test('cloneInto throws on a grid shape mismatch, naming both shapes', async () => {
  const { cloneInto } = await loadState();
  const a = await freshState({ h: 50 });
  const b = await freshState({ h: 25 });
  assert.throws(() => cloneInto(a, b), /src 120x120 at h 50, dst 240x240 at h 25/);
});

test('cloneInto throws on a grid origin mismatch, naming both origins', async () => {
  const { createState, cloneInto } = await loadState();
  const { defaultParams } = await loadParams();
  const a = createState(defaultParams(), ARENA);
  const b = createState(defaultParams(), { x: [0, 6000], y: [-3000, 3000], z: [0, 2500] });
  assert.throws(() => cloneInto(a, b), /src \(-3000, -3000\) mm, dst \(0, -3000\) mm/);
});

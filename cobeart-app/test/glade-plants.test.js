// Glade plants (public/worlds/glade/sim/plants.mjs, an ES module): upkeep, growth, decline, frames and spawning.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const ARENA = { x: [-3000, 3000], y: [-3000, 3000], z: [0, 2500] };
/** 10 x 10 cells at the default h = 50 mm. */
const SMALL = { x: [0, 500], y: [0, 500], z: [0, 2500] };

const loadPlants = () => import('../public/worlds/glade/sim/plants.mjs');
const loadState = () => import('../public/worlds/glade/sim/state.mjs');
const loadParams = () => import('../public/worlds/glade/params.mjs');

const setup = async (overrides = {}, arena = SMALL) => {
  const { createState } = await loadState();
  const { defaultParams } = await loadParams();
  const params = { ...defaultParams(), ...overrides };
  const state = createState(params, arena);
  const { createPlantScratch } = await loadPlants();
  return { params, state, scratch: createPlantScratch(state.grid, state.capacity) };
};

/** Appends a plant at (x, y) mm with `energy` (test helper, writes `state`). */
const addPlant = (state, x, y, energy, frame = 0, direction = 0, mature = 0) => {
  const i = state.count++;
  const p = state.plants;
  p.x[i] = x; p.y[i] = y; p.energy[i] = energy; p.id[i] = state.nextId++; p.seed[i] = i;
  p.frame[i] = frame; p.direction[i] = direction; p.mature[i] = mature;
  return i;
};

/**
 * Feeding rates pinned for the feeding tests (G22), all different so a test catches one rate used for another:
 * growth 1/r = 0.5 s, upkeep 0.5 E/s, wither 1/decline_rate = 1 s.
 */
const RATES = { r: 2, r_maintain: 0.5, decline_rate: 1 };

const AREA = 0.05 * 0.05;
const groundEnergy = (state) => state.grid.density.reduce((sum, d) => sum + d, 0) * (state.grid.h ** 2 / 1e6);
const plantEnergy = (state) => {
  let sum = 0;
  for (let i = 0; i < state.count; i++) sum += state.plants.energy[i];
  return sum;
};

test('drawAt takes the weighted shares; a short cell is not made up by the others', async () => {
  const { drawAt } = await loadPlants();
  const { createBilinear } = await loadState();
  const { state } = await setup();
  const g = state.grid;
  g.density.fill(100);
  // (50, 25): halfway between the centres of cells 0 and 1 on row 0, weight 0.5 each.
  assert.ok(Math.abs(drawAt(g, 50, 25, 0.1, createBilinear()) - 0.1) < 1e-12);
  assert.ok(Math.abs(g.density[0] - (100 - 0.05 / AREA)) < 1e-9);
  g.density[1] = 0.01 / AREA;
  const taken = drawAt(g, 50, 25, 0.1, createBilinear());
  assert.ok(Math.abs(taken - 0.06) < 1e-12, `took ${taken}`);
  assert.equal(g.density[1], 0);
});

test('a fed plant reaches 1 in about 1/r, turns mature and then holds, consuming only its upkeep', async () => {
  const { feed } = await loadPlants();
  const { params, state, scratch } = await setup(RATES);
  state.grid.density.fill(1e6);
  addPlant(state, 25, 25, Math.fround(params.seed_energy));
  const growth = params.r * params.dt;
  const upkeep = params.r_maintain * params.dt;
  const expected = Math.ceil((1 - Math.fround(params.seed_energy)) / growth);
  let steps = 0;
  while (state.plants.energy[0] < 1 && steps < 1000) {
    assert.equal(state.plants.mature[0], 0, 'young until full');
    const before = state.plants.energy[0];
    const consumed = feed(state, params, scratch);
    assert.ok(Math.abs(consumed - upkeep) < 1e-7, `consumed ${consumed} while growing ${before} -> upkeep only`);
    steps++;
  }
  assert.ok(Math.abs(steps - expected) <= 1, `full after ${steps} steps, expected ${expected}`);
  assert.equal(state.plants.mature[0], 1);
  for (let k = 0; k < 100; k++) {
    const consumed = feed(state, params, scratch);
    assert.equal(state.plants.energy[0], 1);
    assert.ok(Math.abs(consumed - upkeep) < 1e-12);
  }
});

test('on empty ground a full plant withers to removal in about 1/decline_rate', async () => {
  const { feed } = await loadPlants();
  const { params, state, scratch } = await setup(RATES);
  addPlant(state, 25, 25, 1, 0, 0, 1);
  const expected = Math.round(1 / (params.decline_rate * params.dt));
  let steps = 0;
  let consumed = 0;
  while (state.count > 0 && steps < 1000) {
    consumed += feed(state, params, scratch);
    steps++;
  }
  assert.ok(Math.abs(steps - expected) <= 1, `removed after ${steps} steps, expected ${expected}`);
  assert.ok(Math.abs(consumed - 1) < 1e-6, `consumed ${consumed}`);
});

test('a young plant short of growth declines; a mature plant with upkeep met but no growth holds', async () => {
  const { feed } = await loadPlants();
  const { params, state, scratch } = await setup(RATES);
  const upkeep = params.r_maintain * params.dt;
  const growth = params.r * params.dt;
  const young = addPlant(state, 25, 25, 0.8);
  const mature = addPlant(state, 25, 25, 0.8, 0, 0, 1);
  // Exactly both upkeeps on the four cells around (25, 25): all weight sits on cell 0 at the corner.
  state.grid.density[0] = 2 * upkeep / AREA;
  const consumed = feed(state, params, scratch);
  assert.equal(state.grid.density[0], 0);
  const decline = params.decline_rate * params.dt * growth / (upkeep + growth);
  assert.equal(state.plants.energy[young], Math.fround(0.8 - decline));
  assert.equal(state.plants.energy[mature], Math.fround(0.8));
  assert.equal(state.plants.mature[young], 0);
  const stored = (Math.fround(0.8 - decline) - Math.fround(0.8));
  assert.ok(Math.abs(consumed - (2 * upkeep - stored)) < 1e-9, `consumed ${consumed}`);
});

test('feed regrows its scratch when the pool has outgrown it and serves plants beyond the old capacity', async () => {
  const { feed } = await loadPlants();
  const { ensureCapacity, INITIAL_CAPACITY } = await loadState();
  const { params, state, scratch } = await setup(RATES);
  const upkeep = params.r_maintain * params.dt;
  const growth = params.r * params.dt;
  const declineStep = params.decline_rate * params.dt;
  assert.equal(scratch.missedUpkeep.length, INITIAL_CAPACITY);
  ensureCapacity(state, 5000, 0);
  assert.ok(state.capacity > INITIAL_CAPACITY);

  // 4996 young plants on rich ground at (25, 25), then four at the centre of the last cell (475, 475), whose ground
  // holds exactly two upkeeps: the older two are served upkeep, the younger two are not (oldest first), and no
  // growth is left for any of them.
  state.grid.density.fill(1e6);
  for (let k = 0; k < 4996; k++) addPlant(state, 25, 25, 0.5);
  const lastCell = state.grid.nx * state.grid.ny - 1;
  state.grid.density[lastCell] = 2 * upkeep / AREA;
  const servedYoung = addPlant(state, 475, 475, 0.8);
  const servedMature = addPlant(state, 475, 475, 0.8, 0, 0, 1);
  const starvedMature = addPlant(state, 475, 475, 0.8, 0, 0, 1);
  const starvedYoung = addPlant(state, 475, 475, 0.8);
  assert.equal(state.count, 5000);

  feed(state, params, scratch);

  assert.equal(scratch.missedUpkeep.length, state.capacity, 'the scratch must follow the pool capacity');
  assert.equal(state.count, 5000);
  const e = state.plants.energy;
  // Fed plants, including the ones past slot 4096, grow by exactly one step of r * dt.
  for (const i of [0, 4095, 4096, 4500, 4995]) {
    assert.ok(Math.abs(e[i] - (0.5 + growth)) < 1e-6, `slot ${i}: ${e[i]}, expected ${0.5 + growth}`);
  }
  assert.equal(state.grid.density[lastCell], 0);
  // Upkeep met, growth missed: a young plant declines by the missed share of its demand, a mature plant holds.
  assert.ok(Math.abs(e[servedYoung] - (0.8 - declineStep * growth / (upkeep + growth))) < 1e-6);
  assert.equal(e[servedMature], Math.fround(0.8));
  // Nothing served: a young plant misses all of its demand, a mature plant all of its upkeep; both lose a full step.
  assert.ok(Math.abs(e[starvedYoung] - (0.8 - declineStep)) < 1e-6, `starved young ${e[starvedYoung]}`);
  assert.ok(Math.abs(e[starvedMature] - (0.8 - declineStep)) < 1e-6, `starved mature ${e[starvedMature]}`);
  assert.equal(state.plants.id[servedYoung], servedYoung);
});

test('a declined mature plant regrows on spare ground and stays mature', async () => {
  const { feed } = await loadPlants();
  const { params, state, scratch } = await setup(RATES);
  addPlant(state, 25, 25, 0.5, 0, 0, 1);
  state.grid.density.fill(1e6);
  feed(state, params, scratch);
  assert.equal(state.plants.energy[0], Math.fround(0.5 + params.r * params.dt));
  assert.equal(state.plants.mature[0], 1);
});

test('no demand cliff: a mature plant just below full declines at decline_rate scale like a full one', async () => {
  const { feed } = await loadPlants();
  const { params, state, scratch } = await setup(RATES);
  const upkeep = params.r_maintain * params.dt;
  const full = addPlant(state, 25, 25, 1, 0, 0, 1);
  const nearlyFull = addPlant(state, 475, 475, Math.fround(0.999), 0, 0, 1);
  // Each plant's corner cell holds half its upkeep, so each misses half: decline_rate * dt / 2, no growth left.
  state.grid.density[0] = 0.5 * upkeep / AREA;
  state.grid.density[99] = 0.5 * upkeep / AREA;
  feed(state, params, scratch);
  const drop = params.decline_rate * params.dt * 0.5;
  assert.equal(state.plants.energy[full], Math.fround(1 - drop));
  assert.equal(state.plants.energy[nearlyFull], Math.fround(Math.fround(0.999) - drop));
  assert.ok(drop <= params.decline_rate * params.dt);
});

test('under shortage older plants are served their upkeep before any plant grows; removal keeps pool order', async () => {
  const { feed } = await loadPlants();
  /** Five young plants at 0.5 plus a nearly dead youngest one, all on cell 0, which holds `ground` E. */
  const shortage = async (ground) => {
    const { params, state, scratch } = await setup(RATES);
    for (let k = 0; k < 5; k++) addPlant(state, 25, 25, 0.5);
    addPlant(state, 25, 25, Math.fround(0.1 * params.decline_rate * params.dt));
    state.grid.density[0] = ground(params) / AREA;
    feed(state, params, scratch);
    assert.equal(state.count, 5, 'the youngest dies, the rest survive');
    assert.deepEqual(Array.from(state.plants.id.subarray(0, 5)), [0, 1, 2, 3, 4]);
    assert.equal(state.grid.density[0], 0);
    return { params, e: Array.from(state.plants.energy.subarray(0, 5)) };
  };
  const rates = (params) => ({
    upkeep: params.r_maintain * params.dt, growth: params.r * params.dt, declineStep: params.decline_rate * params.dt,
  });

  // Every upkeep plus one growth share: the growth goes to the oldest plant only.
  const fed = await shortage((p) => 6 * rates(p).upkeep + rates(p).growth);
  const a = rates(fed.params);
  assert.equal(fed.e[0], Math.fround(0.5 + a.growth), 'plant 0: upkeep and growth');
  for (let k = 1; k < 5; k++) {
    assert.equal(fed.e[k], Math.fround(0.5 - a.declineStep * a.growth / (a.upkeep + a.growth)), `plant ${k}: upkeep`);
  }

  // Upkeep for three plants: the three oldest keep it, the others get nothing, nobody grows.
  const short = await shortage((p) => 3 * rates(p).upkeep);
  const b = rates(short.params);
  for (let k = 0; k < 3; k++) {
    assert.equal(short.e[k], Math.fround(0.5 - b.declineStep * b.growth / (b.upkeep + b.growth)), `plant ${k}: upkeep`);
  }
  for (let k = 3; k < 5; k++) assert.equal(short.e[k], Math.fround(0.5 - b.declineStep), `plant ${k}: nothing`);
});

test('compaction carries the mature flag with its plant', async () => {
  const { feed } = await loadPlants();
  const { params, state, scratch } = await setup(RATES);
  addPlant(state, 25, 25, 1e-6); // dies at once on empty ground
  addPlant(state, 25, 25, 0.9, 0, 0, 1);
  addPlant(state, 25, 25, 0.9);
  feed(state, params, scratch);
  assert.equal(state.count, 2);
  assert.deepEqual(Array.from(state.plants.id.subarray(0, 2)), [1, 2]);
  assert.deepEqual(Array.from(state.plants.mature.subarray(0, 2)), [1, 0]);
});

test('frames do not flicker for energy oscillating within delta of a boundary', async () => {
  const { updateFrames } = await loadPlants();
  const { DIRECTION } = await loadState();
  const { params, state } = await setup();
  const n = params.N_frames;
  const i = addPlant(state, 25, 25, 10.5 / n, 10, DIRECTION.UP);
  const low = (10 - params.delta + 0.01) / n;
  const high = (11 + params.delta - 0.01) / n;
  for (let k = 0; k < 50; k++) {
    state.plants.energy[i] = k % 2 === 0 ? low : high;
    updateFrames(state, params);
    assert.equal(state.plants.frame[i], 10);
    assert.equal(state.plants.direction[i], DIRECTION.UP);
  }
  state.plants.energy[i] = (11 + params.delta + 0.001) / n;
  updateFrames(state, params);
  assert.equal(state.plants.frame[i], 11);
  state.plants.energy[i] = (11 - params.delta - 0.001) / n;
  updateFrames(state, params);
  assert.deepEqual([state.plants.frame[i], state.plants.direction[i]], [10, DIRECTION.DOWN]);
});

test('frames can move several bands in one step and stay within [0, N_frames - 1]', async () => {
  const { updateFrames } = await loadPlants();
  const { DIRECTION } = await loadState();
  const { params, state } = await setup();
  const i = addPlant(state, 25, 25, 1);
  updateFrames(state, params);
  assert.deepEqual([state.plants.frame[i], state.plants.direction[i]], [params.N_frames - 1, DIRECTION.UP]);
  state.plants.energy[i] = 0.001;
  updateFrames(state, params);
  assert.deepEqual([state.plants.frame[i], state.plants.direction[i]], [0, DIRECTION.DOWN]);
});

test('a declining plant fed again climbs a frame and switches direction to up', async () => {
  const { feed, updateFrames } = await loadPlants();
  const { DIRECTION } = await loadState();
  const { params, state, scratch } = await setup();
  const n = params.N_frames;
  const i = addPlant(state, 25, 25, 10 / n, 10, DIRECTION.DOWN);
  state.grid.density.fill(1e6);
  let steps = 0;
  while (state.plants.frame[i] === 10 && steps < 100) {
    feed(state, params, scratch);
    updateFrames(state, params);
    steps++;
  }
  assert.deepEqual([state.plants.frame[i], state.plants.direction[i]], [11, DIRECTION.UP]);
});

/** Spawns over `steps` steps with every cell refilled to `rho` and the pool emptied each step. */
const spawnCount = async (rho, steps, overrides) => {
  const { spawn } = await loadPlants();
  const { params, state, scratch } = await setup({ min_spacing_mm: 0, ...overrides }, ARENA);
  let total = 0;
  for (let k = 0; k < steps; k++) {
    state.grid.density.fill(rho);
    state.count = 0;
    total += spawn(state, params, scratch);
  }
  return { total, params, cells: state.grid.density.length };
};

const assertPoisson = (observed, trials, lambda, params) => {
  const p = 1 - Math.exp(-lambda * AREA * params.dt);
  const mean = trials * p;
  const sd = Math.sqrt(trials * p * (1 - p));
  assert.ok(Math.abs(observed - mean) < 4 * sd, `observed ${observed}, expected ${mean.toFixed(1)} ± ${sd.toFixed(1)}`);
};

test('spawn rate matches lambda_max at and above E_cap, and the linear ramp halfway', async () => {
  const atCap = await spawnCount(1e6, 60);
  assertPoisson(atCap.total, 60 * atCap.cells, atCap.params.lambda_max, atCap.params);
  const { params } = await setup();
  const mid = await spawnCount((params.E_spawn_min + params.E_cap) / 2, 120);
  assertPoisson(mid.total, 120 * mid.cells, params.lambda_max / 2, mid.params);
});

test('cells at or below E_spawn_min neither spawn nor draw from the RNG', async () => {
  const { spawn } = await loadPlants();
  const { params, state, scratch } = await setup();
  state.grid.density.fill(params.E_spawn_min);
  const rng = Array.from(state.rng);
  assert.equal(spawn(state, params, scratch), 0);
  assert.deepEqual(Array.from(state.rng), rng);
});

test('a cell that cannot pay seed_energy does not spawn and keeps its energy', async () => {
  const { spawn } = await loadPlants();
  // seed_energy beyond validation on purpose: the cell is above E_spawn_min but holds less than one seed.
  const { params, state, scratch } = await setup({ lambda_max: 5000 });
  params.seed_energy = 1;
  state.grid.density.fill(300); // 0.75 E per cell
  for (let k = 0; k < 30; k++) assert.equal(spawn(state, params, scratch), 0);
  assert.ok(state.grid.density.every((d) => d === 300));
});

const assertSpacing = (state, spacing) => {
  const { x, y } = state.plants;
  for (let i = 0; i < state.count; i++) {
    for (let j = i + 1; j < state.count; j++) {
      const d = Math.hypot(x[i] - x[j], y[i] - y[j]);
      assert.ok(d >= spacing, `plants ${i} and ${j} are ${d} mm apart, below ${spacing}`);
    }
  }
};

test('spacing is never violated, including between plants spawned in the same step', async () => {
  const { spawn } = await loadPlants();
  const { cellOf } = await loadState();
  const same = await setup({ lambda_max: 5000, min_spacing_mm: 50 });
  same.state.grid.density.fill(1e6);
  same.params.lambda_max = 1e6; // past validation: every cell hits in one step, so only same-step spawns can clash
  assert.ok(spawn(same.state, same.params, same.scratch) > 10);
  for (let i = 0; i < same.state.count; i++) assert.equal(same.state.plants.mature[i], 0, 'spawned plants start young');
  assertSpacing(same.state, 50);

  const { params, state, scratch } = await setup({ lambda_max: 5000 });
  state.grid.density.fill(1e6);
  for (let k = 0; k < 300; k++) spawn(state, params, scratch);
  assert.ok(state.count > 300, `only ${state.count} plants`);
  assertSpacing(state, params.min_spacing_mm);
  for (let i = 0; i < state.count; i++) {
    assert.equal(state.plants.id[i], i);
    assert.ok(cellOf(state.grid, state.plants.x[i], state.plants.y[i]) >= 0);
  }
});

/** Runs feed, spawn and frames for `steps` steps on uneven ground and returns the summed consumption. */
const runMixed = async (steps) => {
  const { feed, spawn, updateFrames } = await loadPlants();
  const { params, state, scratch } = await setup({ lambda_max: 2000 });
  state.grid.density.forEach((_, c) => { state.grid.density[c] = 50 + (c * 37) % 600; });
  const initial = groundEnergy(state);
  let consumed = 0;
  let peak = 0;
  for (let k = 0; k < steps; k++) {
    consumed += feed(state, params, scratch);
    spawn(state, params, scratch);
    updateFrames(state, params);
    peak = Math.max(peak, state.count);
  }
  return { state, initial, consumed, peak };
};

test('energy is conserved across feeding and spawning, apart from consumption', async () => {
  const { state, initial, consumed, peak } = await runMixed(240);
  assert.ok(peak > 50 && state.count < peak, `peak ${peak}, final ${state.count}: expected growth then removals`);
  assert.ok(consumed > 0);
  const total = groundEnergy(state) + plantEnergy(state);
  assert.ok(Math.abs(total + consumed - initial) <= 1e-9 * initial,
    `ground + plants ${total} + consumed ${consumed} != initial ${initial}`);
  assert.ok(state.grid.density.every((d) => d >= 0));
});

test('the same seed gives the same result', async () => {
  const a = (await runMixed(300)).state;
  const b = (await runMixed(300)).state;
  assert.equal(a.count, b.count);
  for (const key of Object.keys(a.plants)) {
    assert.deepEqual(a.plants[key].subarray(0, a.count), b.plants[key].subarray(0, b.count), key);
  }
  assert.deepEqual(a.grid.density, b.grid.density);
  assert.deepEqual(a.rng, b.rng);
});

test('pool growth past INITIAL_CAPACITY during spawn keeps every plant and the scratch in sync', async () => {
  const { spawn, feed } = await loadPlants();
  const { INITIAL_CAPACITY, cellOf } = await loadState();
  const { params, state, scratch } = await setup({ lambda_max: 5000, min_spacing_mm: 0 }, ARENA);
  state.grid.density.fill(1e6);
  let spawned = 0;
  for (let k = 0; k < 3; k++) spawned += spawn(state, params, scratch);
  assert.ok(state.count > INITIAL_CAPACITY, `count ${state.count}`);
  assert.equal(state.count, spawned);
  assert.ok(state.capacity >= state.count);
  assert.ok(scratch.cellItems.length >= INITIAL_CAPACITY);
  for (let i = 0; i < state.count; i++) assert.equal(state.plants.id[i], i);
  // The next call regrows the bucket index to the capacity it starts with and must see every plant in its bucket.
  const capacity = state.capacity;
  const seen = new Uint8Array(state.count);
  spawn(state, params, scratch);
  assert.ok(scratch.cellItems.length >= capacity);
  const cells = state.grid.density.length;
  for (let c = 0; c < cells; c++) {
    for (let k = scratch.cellStart[c]; k < scratch.cellStart[c + 1]; k++) {
      const i = scratch.cellItems[k];
      if (i >= seen.length) continue; // spawned by the last call, after the buckets were built
      assert.equal(cellOf(state.grid, state.plants.x[i], state.plants.y[i]), c);
      seen[i]++;
    }
  }
  assert.ok(seen.every((v) => v === 1));
  const before = state.count;
  feed(state, params, scratch);
  assert.equal(state.count, before);
});

test('spawn throws when the scratch was built for another grid', async () => {
  const { spawn, createPlantScratch } = await loadPlants();
  const { params, state } = await setup();
  const other = await setup({}, ARENA);
  assert.throws(() => spawn(state, params, createPlantScratch(other.state.grid, 16)), /createPlantScratch/);
  // Same cell count (100), different shape.
  const wide = await setup({}, { x: [0, 1000], y: [0, 250], z: [0, 2500] });
  assert.equal(wide.state.grid.density.length, state.grid.density.length);
  assert.throws(() => spawn(state, params, createPlantScratch(wide.state.grid, 16)), /20x5 grid but the grid is 10x10/);
});

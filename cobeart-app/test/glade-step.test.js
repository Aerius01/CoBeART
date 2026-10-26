// Glade simulation step (public/worlds/glade/sim/step.mjs, an ES module): wiring, accounting and emergent behaviour.
const { test } = require('node:test');
const assert = require('node:assert/strict');

/** 60 x 60 cells at h = 50 mm: room for a glade of about 1.2 m radius around the centre. */
const ARENA = { x: [-1500, 1500], y: [-1500, 1500], z: [0, 2500] };
/** 10 x 10 cells at h = 50 mm. */
const SMALL = { x: [0, 500], y: [0, 500], z: [0, 2500] };

/**
 * Values for the emergent tests, pinned so tuning the defaults cannot break them (GL grill G22). At these values a
 * planted foot saturates its core and the glade keeps widening; at the provisional defaults it does not.
 */
const PINNED = { input_power: 200, lambda_max: 20, r: 1 / 3, E_cap: 400, E_spawn_min: 40, D: 2500, D_spill: 100000,
  R: 300, seed_energy: 0.02, min_spacing_mm: 15, dt: 1 / 60, h: 50 };
/** Marker height of a flat foot (BRIEF.md section 1). */
const FOOT_Z = 100;

const loadStep = () => import('../public/worlds/glade/sim/step.mjs');
const loadField = () => import('../public/worlds/glade/sim/field.mjs');
const loadParams = () => import('../public/worlds/glade/params.mjs');

const paramsWith = async (overrides) => ({ ...(await loadParams()).defaultParams(), ...overrides });

/**
 * Runs the double-buffered loop for `seconds`, with the foot given per time, calling `each(state, t, stats)` after
 * every step. Returns the latest state.
 */
const run = async (params, arena, seconds, footAt, each = () => {}) => {
  const { resetState, step } = await loadStep();
  let { a, b, scratch } = resetState(params, arena);
  const n = Math.round(seconds / params.dt);
  for (let k = 1; k <= n; k++) {
    const t = k * params.dt;
    const stats = step(a, b, footAt(t), params, scratch);
    [a, b] = [b, a];
    each(a, t, stats);
  }
  return a;
};

/** Appends a plant at (x, y) mm with `energy` (test helper, writes `state`). */
const addPlant = (state, x, y, energy) => {
  const i = state.count++;
  const p = state.plants;
  p.x[i] = x; p.y[i] = y; p.energy[i] = energy; p.id[i] = state.nextId++; p.seed[i] = i;
  p.frame[i] = 0; p.direction[i] = 0;
  return i;
};

/** Every observable field of a state, copied, for equality checks. */
const snapshot = (s) => ({
  density: Array.from(s.grid.density),
  count: s.count,
  nextId: s.nextId,
  rng: Array.from(s.rng),
  plants: Object.fromEntries(Object.entries(s.plants).map(([k, v]) => [k, Array.from(v.subarray(0, s.count))])),
});

/** Distance from (x0, y0) below which 90% of the live plants stand: the glade radius used by these tests. */
const gladeRadius = (s, x0 = 0, y0 = 0) => {
  const d = [];
  for (let i = 0; i < s.count; i++) d.push(Math.hypot(s.plants.x[i] - x0, s.plants.y[i] - y0));
  d.sort((p, q) => p - q);
  return d.length === 0 ? 0 : d[Math.floor(0.9 * (d.length - 1))];
};

test('resolveFoot: no sighting and a sighting older than foot_timeout_ms mean no foot', async () => {
  const { resolveFoot } = await loadStep();
  const params = await paramsWith({ foot_timeout_ms: 200 });
  const sighting = { x: 10, y: -20, z: 100, receivedMs: 1000 };
  assert.equal(resolveFoot(null, 5000, params), null);
  assert.deepEqual(resolveFoot(sighting, 1000, params), { x: 10, y: -20, z: 100 });
  assert.deepEqual(resolveFoot(sighting, 1200, params), { x: 10, y: -20, z: 100 });
  assert.equal(resolveFoot(sighting, 1200.5, params), null);
});

test('resetState gives two distinct empty states and a scratch for their grid', async () => {
  const { resetState } = await loadStep();
  const params = await paramsWith({});
  const { a, b, scratch } = resetState(params, SMALL);
  assert.notEqual(a, b);
  assert.notEqual(a.grid.density, b.grid.density);
  assert.deepEqual(snapshot(a), snapshot(b));
  assert.equal(a.count, 0);
  assert.equal(scratch.field.nx, a.grid.nx);
  assert.equal(scratch.plants.ny, a.grid.ny);
});

test('step leaves prev untouched and refuses prev === next', async () => {
  const { resetState, step } = await loadStep();
  const params = await paramsWith(PINNED);
  const { a, b, scratch } = resetState(params, SMALL);
  a.grid.density.fill(300);
  addPlant(a, 120, 240, 0.5);
  const before = snapshot(a);
  step(a, b, { x: 250, y: 250, z: FOOT_Z }, params, scratch);
  assert.deepEqual(snapshot(a), before);
  assert.notDeepEqual(snapshot(b), before);
  assert.throws(() => step(a, a, null, params, scratch), /same state/);
});

test('energy books close: ground + plants = initial + deposited - consumed over a stand and a die-off', async () => {
  const { totalEnergy } = await loadField();
  const { totalPlantEnergy } = await loadStep();
  const params = await paramsWith(PINNED);
  let deposited = 0;
  let consumed = 0;
  let spawned = 0;
  let worst = 0;
  await run(params, ARENA, 15, (t) => (t <= 8 ? { x: 0, y: 0, z: FOOT_Z } : null), (s, t, stats) => {
    deposited += stats.deposited;
    consumed += stats.consumed;
    spawned += stats.spawned;
    const books = totalEnergy(s.grid) + totalPlantEnergy(s);
    worst = Math.max(worst, Math.abs(books - (deposited - consumed)) / deposited);
  });
  assert.ok(spawned > 100, `the run must exercise spawning (spawned ${spawned})`);
  assert.ok(consumed > 0);
  assert.ok(worst < 1e-9, `worst relative imbalance ${worst}`);
});

test('the same seed and input give identical states; another seed does not', async () => {
  const params = await paramsWith(PINNED);
  const footAt = (t) => ({ x: 400 * Math.cos(t), y: 400 * Math.sin(t), z: FOOT_Z });
  const one = snapshot(await run(params, ARENA, 6, footAt));
  const two = snapshot(await run(params, ARENA, 6, footAt));
  const other = snapshot(await run({ ...params, seed: 2 }, ARENA, 6, footAt));
  assert.ok(one.count > 20, `the run must grow plants (count ${one.count})`);
  assert.deepEqual(two, one);
  assert.notDeepEqual(other.plants.x, one.plants.x);
});

test('the pool grows past its initial capacity across the swap and stays id-ordered', async () => {
  const { resetState, step } = await loadStep();
  const { INITIAL_CAPACITY } = await import('../public/worlds/glade/sim/state.mjs');
  const params = await paramsWith({ ...PINNED, lambda_max: 5000 });
  let { a, b, scratch } = resetState(params, ARENA);
  a.grid.density.fill(params.E_cap);
  // A 64 x 64 lattice at 40 mm pitch fills the pool exactly; the spawns of the first step overflow it.
  for (let iy = 0; iy < 64; iy++) for (let ix = 0; ix < 64; ix++) addPlant(a, -1280 + ix * 40, -1280 + iy * 40, 1);
  assert.equal(a.count, INITIAL_CAPACITY);
  for (let k = 0; k < 3; k++) {
    step(a, b, null, params, scratch);
    [a, b] = [b, a];
  }
  assert.ok(a.count > INITIAL_CAPACITY, `count ${a.count}`);
  assert.ok(a.capacity >= a.count && b.capacity >= a.count);
  for (let i = 1; i < a.count; i++) assert.ok(a.plants.id[i] > a.plants.id[i - 1], `id order breaks at slot ${i}`);
  for (let i = 0; i < INITIAL_CAPACITY; i++) assert.equal(a.plants.id[i], i);
});

test('h = 25 and h = 50 give matching ground energy and plant count (G20)', async () => {
  const { totalEnergy } = await loadField();
  const totals = { 25: { ground: 0, plants: 0 }, 50: { ground: 0, plants: 0 } };
  const seeds = [1, 2, 3];
  for (const h of [25, 50]) {
    for (const seed of seeds) {
      const params = await paramsWith({ ...PINNED, h, seed });
      const s = await run(params, ARENA, 10, () => ({ x: 0, y: 0, z: FOOT_Z }));
      totals[h].ground += totalEnergy(s.grid) / seeds.length;
      totals[h].plants += s.count / seeds.length;
    }
  }
  const groundGap = Math.abs(totals[25].ground - totals[50].ground) / totals[50].ground;
  const plantGap = Math.abs(totals[25].plants - totals[50].plants) / totals[50].plants;
  assert.ok(totals[50].plants > 100, `the run must grow a glade (${totals[50].plants} plants)`);
  assert.ok(groundGap < 0.03, `ground energy differs by ${(groundGap * 100).toFixed(2)}%: ${JSON.stringify(totals)}`);
  assert.ok(plantGap < 0.1, `plant count differs by ${(plantGap * 100).toFixed(2)}%: ${JSON.stringify(totals)}`);
});

test('a foot standing still for 20 s saturates its core and the glade keeps widening after that', async () => {
  const seeds = [1, 2, 3];
  /** Glade radius at 5, 10, 15 and 20 s, averaged over the seeds (one seed's p90 radius is noisy). */
  const meanRadius = [0, 0, 0, 0];
  for (const seed of seeds) {
    const params = await paramsWith({ ...PINNED, seed });
    const { grid } = (await loadStep()).resetState(params, ARENA).a;
    // The cell whose corner is the arena centre, i.e. one of the four cells under the foot.
    const core = (grid.ny / 2) * grid.nx + grid.nx / 2;
    let saturatedAt = null;
    let sample = 0;
    await run(params, ARENA, 20, () => ({ x: 0, y: 0, z: FOOT_Z }), (s, t) => {
      if (saturatedAt === null && s.grid.density[core] > params.E_cap) saturatedAt = t;
      if (Math.round(t / params.dt) % Math.round(5 / params.dt) === 0) {
        assert.ok(s.grid.density[core] > params.E_cap,
          `seed ${seed}: the core must stay saturated (${s.grid.density[core]} E/m² at ${t} s)`);
        meanRadius[sample++] += gladeRadius(s) / seeds.length;
      }
    });
    assert.ok(saturatedAt !== null && saturatedAt < 5, `seed ${seed}: the core must pass E_cap early (${saturatedAt} s)`);
  }
  // Measured over seeds 1 to 9: 3-seed means gain 21 to 33 mm in the last interval and 105 to 126 mm from 10 to 20 s.
  for (let i = 1; i < meanRadius.length; i++) {
    assert.ok(meanRadius[i] > meanRadius[i - 1], `the glade radius must keep growing: ${meanRadius}`);
  }
  assert.ok(meanRadius[3] - meanRadius[1] > 50, `the glade must widen by over 50 mm from 10 to 20 s: ${meanRadius}`);
});

test('a foot walking at 1.4 m/s leaves no cell above E_cap at the values where standing saturates', async () => {
  const params = await paramsWith(PINNED);
  const speed = 1400;
  const start = ARENA.x[0] + 300;
  const length = ARENA.x[1] - ARENA.x[0] - 600;
  let peak = 0;
  let plants = 0;
  await run(params, ARENA, 4, (t) => (t * speed <= length ? { x: start + t * speed, y: 0, z: FOOT_Z } : null), (s) => {
    for (let i = 0; i < s.grid.density.length; i++) peak = Math.max(peak, s.grid.density[i]);
    plants = Math.max(plants, s.count);
  });
  assert.ok(peak < params.E_cap, `walking peaked at ${peak} E/m², cap ${params.E_cap}`);
  assert.ok(peak > params.E_spawn_min && plants > 0, `the trail must still grow plants (peak ${peak}, ${plants} plants)`);
});

test('plants sharing ground die youngest first once it runs dry', async () => {
  const { resetState, step } = await loadStep();
  // Below E_spawn_min, so nothing spawns; all plants stand on one point and draw from the same four cells.
  const params = await paramsWith(PINNED);
  let { a, b, scratch } = resetState(params, SMALL);
  a.grid.density.fill(30);
  for (let k = 0; k < 12; k++) addPlant(a, 250, 250, 1);
  const deathStep = new Map();
  for (let k = 1; deathStep.size < 12 && k < 2000; k++) {
    step(a, b, null, params, scratch);
    [a, b] = [b, a];
    const alive = new Set(a.plants.id.subarray(0, a.count));
    for (let id = 0; id < 12; id++) if (!alive.has(id) && !deathStep.has(id)) deathStep.set(id, k);
  }
  assert.equal(deathStep.size, 12, 'every plant must die');
  // Plants still fully fed when the ground ran out hold equal energy and die on the same step, so the order is
  // "never after an older plant", and strict between the youngest and the oldest.
  for (let id = 1; id < 12; id++) {
    assert.ok(deathStep.get(id) <= deathStep.get(id - 1),
      `plant ${id} died at step ${deathStep.get(id)}, after older plant ${id - 1} (${deathStep.get(id - 1)})`);
  }
  assert.ok(deathStep.get(11) < deathStep.get(0), `deaths by id: ${JSON.stringify([...deathStep])}`);
});

test('after the foot leaves, a grown glade dies mostly youngest first per cell and the pool empties', async () => {
  const params = await paramsWith(PINNED);
  const leaveAt = 20;
  const { cellOf } = await import('../public/worlds/glade/sim/state.mjs');
  /** id -> cell of each plant seen, and id -> step at which it was last alive. */
  const cell = new Map();
  const lastAlive = new Map();
  let emptyAt = null;
  let respawned = false;
  await run(params, ARENA, leaveAt + 30, (t) => (t <= leaveAt ? { x: 0, y: 0, z: FOOT_Z } : null), (s, t) => {
    for (let i = 0; i < s.count; i++) {
      const id = s.plants.id[i];
      if (!cell.has(id)) cell.set(id, cellOf(s.grid, s.plants.x[i], s.plants.y[i]));
      lastAlive.set(id, t);
      if (emptyAt !== null) respawned = true;
    }
    if (t > leaveAt && emptyAt === null && s.count === 0) emptyAt = t;
  });
  assert.ok(emptyAt !== null, 'the pool must empty within 30 s of the foot leaving');
  assert.ok(!respawned, 'nothing may spawn once the pool is empty');

  // "Youngest first": among plants alive when the foot left that share a cell, the younger one (higher id) dies
  // strictly earlier. Not every pair can comply, since bilinear feeding spreads each plant over four cells, so the
  // test asks for a clear majority of the pairs that died on different steps.
  const byCell = new Map();
  for (const [id, t] of lastAlive) {
    if (t <= leaveAt) continue;
    const c = cell.get(id);
    if (!byCell.has(c)) byCell.set(c, []);
    byCell.get(c).push([id, t]);
  }
  let younger = 0;
  let older = 0;
  for (const plants of byCell.values()) {
    for (let i = 0; i < plants.length; i++) {
      for (let j = i + 1; j < plants.length; j++) {
        const [young, old] = plants[i][0] > plants[j][0] ? [plants[i], plants[j]] : [plants[j], plants[i]];
        if (young[1] < old[1]) younger++;
        else if (young[1] > old[1]) older++;
      }
    }
  }
  assert.ok(younger + older > 50, `too few same-cell pairs to judge (${younger + older})`);
  assert.ok(younger >= 3 * older, `younger died first in ${younger} pairs, older first in ${older}`);
});

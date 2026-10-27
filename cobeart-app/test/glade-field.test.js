// Glade ground field (public/worlds/glade/sim/field.mjs, an ES module imported from this CommonJS test).
const { test } = require('node:test');
const assert = require('node:assert/strict');

const ARENA = { x: [-3000, 3000], y: [-3000, 3000], z: [0, 2500] };

const load = async () => ({
  ...(await import('../public/worlds/glade/sim/field.mjs')),
  ...(await import('../public/worlds/glade/sim/state.mjs')),
  ...(await import('../public/worlds/glade/params.mjs')),
});

/** Defaults with overrides, as a plain (unfrozen) object. */
const paramsWith = (defaultParams, overrides = {}) => ({ ...defaultParams(), ...overrides });

/** A free-standing grid (origin 0, 0) for tests that need odd sizes. */
const makeGrid = (nx, ny, h) => ({ nx, ny, h, x0: 0, y0: 0, density: new Float64Array(nx * ny) });

/** Deterministic pseudo-random fill in [0, max). */
const fillNoise = (density, max, seed = 7) => {
  let s = seed;
  for (let i = 0; i < density.length; i++) {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    density[i] = (s / 2 ** 32) * max;
  }
};

const relErr = (a, b) => Math.abs(a - b) / Math.abs(b);

test('substeps keeps k = D·(dt/n)/h² at or below K_MAX = 0.3', async () => {
  const { substeps, K_MAX } = await load();
  assert.equal(K_MAX, 0.3);
  assert.equal(substeps(2500, 1 / 60, 50), 1);
  assert.equal(substeps(100000, 1 / 60, 50), 3);
  assert.equal(substeps(1e6, 1 / 60, 50), 23);
  // Exact boundary: k_total = 0.6 needs exactly 2 substeps of k = 0.3.
  assert.equal(substeps(0.6 * 2500 * 60, 1 / 60, 50), 2);
  for (const [D, dt, h] of [[1e6, 1 / 15, 10], [250, 1 / 240, 200], [25000, 1 / 15, 25], [4500, 1 / 30, 50]]) {
    const n = substeps(D, dt, h);
    assert.ok(D * (dt / n) / (h * h) <= K_MAX, `k too large for D ${D}, dt ${dt}, h ${h}`);
  }
});

test('deposit totals input_power * dt at z = 0, less when lifted, nothing at z >= R or when absent', async () => {
  const { createState, createFieldScratch, deposit, totalEnergy, defaultParams } = await load();
  const p = paramsWith(defaultParams);
  const expected = p.input_power * p.dt;
  for (const [z, factor] of [[0, 1], [100, (1 - 1 / 9) ** 3], [150, 0.75 ** 3], [-100, (1 - 1 / 9) ** 3]]) {
    const { grid } = createState(p, ARENA);
    const scratch = createFieldScratch(grid);
    const got = deposit(grid, { x: 123, y: -456, z }, p, scratch);
    assert.ok(relErr(got, expected * factor) < 1e-12, `z ${z}: returned ${got}`);
    assert.ok(relErr(totalEnergy(grid), expected * factor) < 1e-9, `z ${z}: ground ${totalEnergy(grid)}`);
  }
  for (const foot of [{ x: 0, y: 0, z: p.R }, { x: 0, y: 0, z: p.R + 50 }, null]) {
    const { grid } = createState(p, ARENA);
    assert.equal(deposit(grid, foot, p, createFieldScratch(grid)), 0);
    assert.equal(totalEnergy(grid), 0);
  }
});

test('deposit peak density matches the analytic kernel, and the footprint has the right radius', async () => {
  const { createState, createFieldScratch, deposit, cellOf, defaultParams } = await load();
  const p = paramsWith(defaultParams);
  const { grid } = createState(p, ARENA);
  deposit(grid, { x: 25, y: 25, z: 0 }, p, createFieldScratch(grid));
  // Peak = total / ∫f dA, with ∫f dA = πR²/3 (m²) at z = 0.
  const peak = p.input_power * p.dt / (Math.PI * (p.R / 1000) ** 2 / 3);
  assert.ok(relErr(grid.density[cellOf(grid, 25, 25)], peak) < 0.01);
  assert.equal(grid.density[cellOf(grid, 25 + p.R + 10, 25)], 0);
  assert.ok(grid.density[cellOf(grid, 25 + p.R - 40, 25)] > 0);
});

test('edge renormalisation keeps the total at walls and corners', async () => {
  const { createState, createFieldScratch, deposit, totalEnergy, defaultParams } = await load();
  const p = paramsWith(defaultParams);
  for (const [x, y, z] of [[-2990, 0, 0], [2999, 2999, 0], [-3000, -3000, 120], [0, 2900, 200]]) {
    const { grid } = createState(p, ARENA);
    const got = deposit(grid, { x, y, z }, p, createFieldScratch(grid));
    const expected = p.input_power * p.dt * (1 - (z * z) / (p.R * p.R)) ** 3;
    assert.ok(relErr(got, expected) < 1e-12);
    assert.ok(relErr(totalEnergy(grid), expected) < 1e-9, `(${x}, ${y}, ${z}): ${totalEnergy(grid)} vs ${expected}`);
  }
});

test('a foot exactly at a corner deposits the full total; the boost cap is exported', async () => {
  const { createState, createFieldScratch, deposit, totalEnergy, defaultParams, EDGE_BOOST_MAX } = await load();
  assert.equal(EDGE_BOOST_MAX, 4);
  const p = paramsWith(defaultParams);
  for (const [x, y] of [[-3000, -3000], [3000, -3000], [-3000, 3000], [3000, 3000]]) {
    for (const h of [25, 50]) {
      const q = { ...p, h };
      const { grid } = createState(q, ARENA);
      const got = deposit(grid, { x, y, z: 0 }, q, createFieldScratch(grid));
      const expected = q.input_power * q.dt;
      assert.ok(relErr(got, expected) < 1e-12, `corner (${x}, ${y}) h ${h}: ${got} vs ${expected}`);
      assert.ok(relErr(totalEnergy(grid), got) < 1e-9);
    }
  }
});

test('a foot outside a wall deposits less, fading continuously and monotonically to 0', async () => {
  const { createState, createFieldScratch, deposit, totalEnergy, defaultParams } = await load();
  const p = paramsWith(defaultParams);
  const total = p.input_power * p.dt;
  const amounts = [];
  for (let out = 0; out <= p.R + 20; out += 10) {
    const { grid } = createState(p, ARENA);
    const got = deposit(grid, { x: -3000 - out, y: 137, z: 0 }, p, createFieldScratch(grid));
    assert.ok(relErr(totalEnergy(grid), got) < 1e-9 || got === 0);
    amounts.push(got);
  }
  assert.ok(relErr(amounts[0], total) < 1e-12, 'a foot on the wall injects the full total');
  // Just outside, the in-grid share is still above 1/4 and is boosted to the full total; further out the cap binds.
  assert.ok(relErr(amounts[3], total) < 1e-12, `30 mm outside: ${amounts[3]}`);
  assert.ok(amounts[15] > 0 && amounts[15] < 0.5 * total, `150 mm outside: ${amounts[15]}`);
  for (let i = 1; i < amounts.length; i++) {
    assert.ok(amounts[i] <= amounts[i - 1] * (1 + 1e-9), `not monotonic at ${i * 10} mm: ${amounts}`);
    assert.ok(amounts[i - 1] - amounts[i] < 0.15 * total, `jump at ${i * 10} mm: ${amounts}`);
  }
  assert.equal(amounts.at(-1), 0);
});

test('deposit rejects a non-finite foot, naming it', async () => {
  const { createState, createFieldScratch, deposit, defaultParams } = await load();
  const p = paramsWith(defaultParams);
  const { grid } = createState(p, ARENA);
  for (const foot of [{ x: NaN, y: 0, z: 0 }, { x: 0, y: Infinity, z: 0 }, { x: 0, y: 0, z: -Infinity }]) {
    assert.throws(() => deposit(grid, foot, p, createFieldScratch(grid)),
      (e) => e instanceof RangeError && /foot \(.*\) mm must be finite/.test(e.message));
  }
});

test('diffusion and spill conserve total energy to 1e-6 relative', async () => {
  const { createState, createFieldScratch, diffuse, spill, totalEnergy, defaultParams } = await load();
  for (const overrides of [{}, { D: 25000, dt: 1 / 15 }, { D_spill: 1e6 }, { D_spill: 1e6, dt: 1 / 15 }]) {
    const p = paramsWith(defaultParams, overrides);
    const { grid } = createState(p, ARENA);
    const scratch = createFieldScratch(grid);
    fillNoise(grid.density, 3 * p.E_cap);
    const before = totalEnergy(grid);
    for (let s = 0; s < 10; s++) {
      diffuse(grid, p, scratch);
      spill(grid, p, scratch);
    }
    assert.ok(relErr(totalEnergy(grid), before) < 1e-6, `${JSON.stringify(overrides)}: drift`);
  }
});

test('no flux leaves the grid: energy piled in a corner and along an edge stays inside', async () => {
  const { createFieldScratch, diffuse, spill, totalEnergy, defaultParams } = await load();
  const p = paramsWith(defaultParams, { D: 25000, D_spill: 1e6 });
  const grid = makeGrid(7, 5, 50);
  grid.density[0] = 5000;
  for (let ix = 0; ix < 7; ix++) grid.density[4 * 7 + ix] = 2000;
  const scratch = createFieldScratch(grid);
  const before = totalEnergy(grid);
  for (let s = 0; s < 600; s++) {
    diffuse(grid, p, scratch);
    spill(grid, p, scratch);
  }
  assert.ok(relErr(totalEnergy(grid), before) < 1e-9);
  // Long diffusion in a closed box flattens the field to the mean.
  const mean = grid.density.reduce((a, b) => a + b, 0) / grid.density.length;
  for (const d of grid.density) assert.ok(Math.abs(d - mean) < 1e-6 * mean);
});

test('no negative density at D_spill = 1e6 or at the largest D, dt and smallest h', async () => {
  const { createFieldScratch, diffuse, spill, defaultParams } = await load();
  // D_spill 90,000 at h 50 lands exactly on k = K_MAX, where a lone spike gives away all of its excess.
  for (const overrides of [{ D_spill: 1e6 }, { D_spill: 1e6, dt: 1 / 15 }, { D: 25000, dt: 1 / 15 }, { D_spill: 90000 }]) {
    for (const h of [10, 50]) {
      const p = paramsWith(defaultParams, { ...overrides, h });
      const grid = makeGrid(40, 30, h);
      // Spikes over an empty floor: the harshest case for overshoot.
      for (let i = 0; i < grid.density.length; i += 7) grid.density[i] = 10 * p.E_cap;
      const scratch = createFieldScratch(grid);
      for (let s = 0; s < 5; s++) {
        diffuse(grid, p, scratch);
        spill(grid, p, scratch);
        for (const d of grid.density) assert.ok(d >= 0, `${JSON.stringify(overrides)} h ${h}: ${d}`);
      }
    }
  }
});

test('spill never takes a cell below the cap and leaves sub-cap fields untouched', async () => {
  const { createFieldScratch, spill, defaultParams } = await load();
  const p = paramsWith(defaultParams, { D_spill: 1e6 });
  const grid = makeGrid(20, 20, 50);
  fillNoise(grid.density, 0.99 * p.E_cap);
  const before = grid.density.slice();
  spill(grid, p, createFieldScratch(grid));
  assert.deepEqual(grid.density, before);
  grid.density[210] = 5 * p.E_cap;
  spill(grid, p, createFieldScratch(grid));
  assert.ok(grid.density[210] >= p.E_cap - 1e-9);
});

/** Full-grid reference spill: the operator without the hot-spot box, same weights and visiting order. */
const referenceSpill = (grid, p, substeps) => {
  const { nx, ny, density } = grid;
  const n = substeps(p.D_spill, p.dt, grid.h);
  const k = p.D_spill * (p.dt / n) / (grid.h * grid.h);
  const a = (2 / 3) * k;
  const b = (1 / 6) * k;
  const excess = new Float64Array(nx * ny);
  const delta = new Float64Array(nx * ny);
  for (let s = 0; s < n; s++) {
    for (let i = 0; i < density.length; i++) excess[i] = Math.max(0, density[i] - p.E_cap);
    for (let iy = 0; iy < ny; iy++) {
      for (let ix = 0; ix < nx; ix++) {
        const i = iy * nx + ix;
        let d = 0;
        const edge = (j, w) => {
          const f = w * (excess[j] - excess[i]);
          d += f;
          delta[j] -= f;
        };
        if (ix + 1 < nx) edge(i + 1, a);
        if (iy + 1 < ny) {
          edge(i + nx, a);
          if (ix + 1 < nx) edge(i + nx + 1, b);
          if (ix > 0) edge(i + nx - 1, b);
        }
        delta[i] += d;
      }
    }
    for (let i = 0; i < density.length; i++) {
      density[i] += delta[i];
      delta[i] = 0;
    }
  }
};

test('spill restricted to the hot-spot box matches the full-grid operator', async () => {
  const { createFieldScratch, spill, substeps, defaultParams } = await load();
  for (const [hx, hy] of [[20, 15], [0, 0], [39, 29]]) {
    const p = paramsWith(defaultParams, { D_spill: 1e6 });
    const grid = makeGrid(40, 30, 50);
    fillNoise(grid.density, 0.9 * p.E_cap);
    grid.density[hy * 40 + hx] = 6 * p.E_cap;
    grid.density[Math.min(29, hy + 1) * 40 + hx] = 3 * p.E_cap;
    const ref = { ...grid, density: grid.density.slice() };
    const scratch = createFieldScratch(grid);
    for (let step = 0; step < 3; step++) {
      spill(grid, p, scratch);
      referenceSpill(ref, p, substeps);
    }
    for (let i = 0; i < grid.density.length; i++) {
      assert.ok(Math.abs(grid.density[i] - ref.density[i]) <= 1e-15 * p.E_cap,
        `hot spot (${hx}, ${hy}) cell ${i}: ${grid.density[i]} vs ${ref.density[i]}`);
    }
    assert.ok(scratch.delta.every((d) => d === 0), 'delta is left zero');
  }
});

test('a saturated blob\'s above-cap region expands under spill while a planted foot feeds it', async () => {
  const { createFieldScratch, deposit, spill, defaultParams } = await load();
  const p = paramsWith(defaultParams, { input_power: 200 });
  const grid = makeGrid(61, 61, 50);
  const c = 30;
  for (let iy = 0; iy < 61; iy++) {
    for (let ix = 0; ix < 61; ix++) {
      if ((ix - c) ** 2 + (iy - c) ** 2 <= 9) grid.density[iy * 61 + ix] = 2 * p.E_cap;
    }
  }
  const foot = { x: (c + 0.5) * 50, y: (c + 0.5) * 50, z: 0 };
  const saturated = () => grid.density.filter((d) => d >= p.E_cap * (1 - 1e-9)).length;
  const scratch = createFieldScratch(grid);
  const counts = [saturated()];
  for (let round = 0; round < 4; round++) {
    for (let s = 0; s < 60; s++) {
      deposit(grid, foot, p, scratch);
      spill(grid, p, scratch);
    }
    counts.push(saturated());
  }
  for (let i = 1; i < counts.length; i++) assert.ok(counts[i] > counts[i - 1], `saturated cells ${counts}`);
  // The cap is soft: the core sits above it, but spill keeps it within a small multiple.
  assert.ok(Math.max(...grid.density) < 3 * p.E_cap, `peak ${Math.max(...grid.density)}`);
});

/** Mean density in 50 mm annuli around (x, y) mm, out to `rMax`. */
const radialProfile = (grid, x, y, rMax) => {
  const bins = Math.ceil(rMax / 50);
  const sum = new Float64Array(bins);
  const n = new Float64Array(bins);
  for (let iy = 0; iy < grid.ny; iy++) {
    for (let ix = 0; ix < grid.nx; ix++) {
      const r = Math.hypot(grid.x0 + (ix + 0.5) * grid.h - x, grid.y0 + (iy + 0.5) * grid.h - y);
      const b = Math.floor(r / 50);
      if (b < bins) {
        sum[b] += grid.density[iy * grid.nx + ix];
        n[b] += 1;
      }
    }
  }
  return Array.from(sum, (s, b) => s / n[b]);
};

test('h = 25 and h = 50 give matching totals and radial profiles', async () => {
  const { createState, createFieldScratch, deposit, diffuse, spill, totalEnergy, defaultParams } = await load();
  const runs = [25, 50].map((h) => {
    const p = paramsWith(defaultParams, { h, seed_energy: 0.02 });
    const { grid } = createState(p, ARENA);
    const scratch = createFieldScratch(grid);
    const foot = { x: 410, y: -330, z: 100 };
    for (let s = 0; s < 60; s++) {
      deposit(grid, foot, p, scratch);
      diffuse(grid, p, scratch);
      spill(grid, p, scratch);
    }
    return { total: totalEnergy(grid), profile: radialProfile(grid, foot.x, foot.y, 600) };
  });
  assert.ok(relErr(runs[0].total, runs[1].total) < 1e-9);
  const peak = Math.max(...runs[1].profile);
  // The first annulus averages over very few h = 50 cells, so compare from 50 mm out.
  for (let b = 1; b < runs[0].profile.length; b++) {
    assert.ok(Math.abs(runs[0].profile[b] - runs[1].profile[b]) < 0.05 * peak,
      `annulus ${b}: h25 ${runs[0].profile[b]} vs h50 ${runs[1].profile[b]}`);
  }
});

/** Moments of the density around the grid centre cell, in mm. */
const moments = (grid) => {
  const c = (grid.nx - 1) / 2;
  let m = 0;
  let xx = 0;
  let dd = 0;
  let x4 = 0;
  let d4 = 0;
  for (let iy = 0; iy < grid.ny; iy++) {
    for (let ix = 0; ix < grid.nx; ix++) {
      const w = grid.density[iy * grid.nx + ix];
      const dx = (ix - c) * grid.h;
      const dy = (iy - c) * grid.h;
      const u = (dx + dy) / Math.SQRT2;
      m += w;
      xx += w * dx * dx;
      dd += w * u * u;
      x4 += w * dx ** 4;
      d4 += w * u ** 4;
    }
  }
  return { m, varAxis: xx / m, varDiag: dd / m, m4Axis: x4 / m, m4Diag: d4 / m };
};

test('diffusion of a point source spreads isotropically with variance 2·D·t per axis', async () => {
  const { createFieldScratch, diffuse, defaultParams } = await load();
  const p = paramsWith(defaultParams, { D: 25000 });
  const grid = makeGrid(101, 101, 50);
  grid.density[50 * 101 + 50] = 1e6;
  const scratch = createFieldScratch(grid);
  const steps = 30;
  for (let s = 0; s < steps; s++) diffuse(grid, p, scratch);
  const t = steps * p.dt;
  const mo = moments(grid);
  // Exact for a conservative stencil with the right second moment: 4·D·t in 2D, 2·D·t per axis.
  assert.ok(relErr(mo.varAxis, 2 * p.D * t) < 1e-9, `axis variance ${mo.varAxis} vs ${2 * p.D * t}`);
  assert.ok(relErr(mo.varDiag, mo.varAxis) < 1e-9);
  // Fourth moments separate a round blob from a diamond: the 4 : 1 stencil matches them exactly, where a 2 : 1
  // stencil differs here by +0.83% and a 5-point stencil by -1.67%.
  assert.ok(Math.abs(relErr(mo.m4Diag, mo.m4Axis)) < 1e-6, `fourth moments axis ${mo.m4Axis} vs diagonal ${mo.m4Diag}`);
});

test('operators reject scratch built for another grid', async () => {
  const { createFieldScratch, diffuse, spill, deposit, defaultParams } = await load();
  const p = paramsWith(defaultParams);
  const grid = makeGrid(10, 10, 50);
  const wrong = createFieldScratch(makeGrid(12, 10, 50));
  for (const op of [() => diffuse(grid, p, wrong), () => spill(grid, p, wrong),
    () => deposit(grid, { x: 0, y: 0, z: 0 }, p, wrong)]) {
    assert.throws(op, /createFieldScratch/);
  }
});

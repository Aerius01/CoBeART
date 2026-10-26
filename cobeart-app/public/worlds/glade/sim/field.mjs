// Glade ground field: foot deposition, diffusion and spill on the density grid (BRIEF.md sections 1 and 2).
// Every operator mutates the grid it is handed, which is always the `next` buffer (GL grill G11).
// Pure module: no DOM, window or WebGL, so node --test can import it.

/**
 * Reusable per-grid buffers, not part of the double-buffered state (GL grill G13).
 * @typedef {object} FieldScratch
 * @property {number} nx grid width the buffers were sized for
 * @property {number} ny grid height the buffers were sized for
 * @property {Float64Array} delta per-cell density change accumulated over one substep
 * @property {Float64Array} excess per-cell density above E_cap, frozen at the start of a spill substep
 */

/**
 * Foot position in arena mm; `null` when the foot is absent.
 * @typedef {{ x: number, y: number, z: number } | null} Foot
 */

/**
 * Orthogonal : diagonal edge weights of the isotropic 9-point stencil (4 : 1), times k (GL grill G16, amended):
 * the only ratio whose leading error term is isotropic, so fronts grow as circles.
 */
const W_ORTHO = 2 / 3;
const W_DIAG = 1 / 6;

/** Largest stable per-substep factor: a cell gives away at most (4·W_ORTHO + 4·W_DIAG)·k = (10/3)·k of itself. */
export const K_MAX = 0.3;

/** mm² per m². */
const MM2_PER_M2 = 1e6;

/**
 * @param {import('./state.mjs').Grid} grid
 * @returns {FieldScratch}
 */
export function createFieldScratch(grid) {
  const n = grid.nx * grid.ny;
  return { nx: grid.nx, ny: grid.ny, delta: new Float64Array(n), excess: new Float64Array(n) };
}

/**
 * @param {import('./state.mjs').Grid} grid
 * @param {FieldScratch} scratch
 */
function checkScratch(grid, scratch) {
  if (scratch.nx !== grid.nx || scratch.ny !== grid.ny) {
    throw new Error(`field scratch is sized for ${scratch.nx}x${scratch.ny} but the grid is ${grid.nx}x${grid.ny}; ` +
      'rebuild it with createFieldScratch(grid) after a structural change');
  }
}

/**
 * Substeps an operator with coefficient `D` needs so each substep has k = D·(dt/n)/h² ≤ K_MAX.
 * @param {number} D diffusion coefficient in mm²/s
 * @param {number} dt step in s
 * @param {number} h cell size in mm
 * @returns {number} n ≥ 1
 */
export function substeps(D, dt, h) {
  const n = Math.max(1, Math.ceil(10 * D * dt / (3 * h * h)));
  // Rounding can land the ceil one short at an exact boundary; never return a k above K_MAX.
  return D * (dt / n) / (h * h) > K_MAX ? n + 1 : n;
}

/**
 * Total ground energy in E: density summed over cells, times the cell area.
 * @param {import('./state.mjs').Grid} grid
 * @returns {number}
 */
export function totalEnergy(grid) {
  let sum = 0;
  for (let i = 0; i < grid.density.length; i++) sum += grid.density[i];
  return sum * grid.h * grid.h / MM2_PER_M2;
}

/**
 * Largest factor by which the in-grid share of a footprint is scaled up (GL grill G15, amended). It is the corner
 * case, so on the floor the cap never binds; for a foot outside the arena the deposit fades continuously to 0.
 */
export const EDGE_BOOST_MAX = 4;

/**
 * Adds the foot's energy for one step: `input_power * dt * (1 - z²/R²)³` in total (the kernel's exact integral
 * relative to z = 0), spread over the in-grid cells by f = (1 - rho²)² at their centres and renormalised to the
 * in-grid share, scaled up by at most EDGE_BOOST_MAX (GL grill G15).
 * @param {import('./state.mjs').Grid} grid mutated
 * @param {Foot} foot
 * @param {import('../params.mjs').GladeParams} params
 * @param {FieldScratch} scratch
 * @returns {number} energy deposited in E: the full total on the floor, less beyond the arena edge, 0 when absent,
 *   at z ≥ R, or when no cell centre lies in the footprint
 */
export function deposit(grid, foot, params, scratch) {
  checkScratch(grid, scratch);
  if (foot === null) return 0;
  if (!Number.isFinite(foot.x) || !Number.isFinite(foot.y) || !Number.isFinite(foot.z)) {
    throw new RangeError(`deposit: foot (${foot.x}, ${foot.y}, ${foot.z}) mm must be finite; pass null for an ` +
      'absent foot');
  }
  const { R } = params;
  const R2 = R * R;
  const z2 = foot.z * foot.z;
  if (z2 >= R2) return 0;
  const a = Math.sqrt(R2 - z2);
  const lift = (R2 - z2) / R2;
  const total = params.input_power * params.dt * lift * lift * lift;

  const { nx, ny, h, x0, y0, density } = grid;
  // Lattice cells whose centres can lie in the footprint, unclipped (indices may fall outside the grid).
  const ixa = Math.ceil((foot.x - a - x0) / h - 0.5);
  const ixb = Math.floor((foot.x + a - x0) / h - 0.5);
  const iya = Math.ceil((foot.y - a - y0) / h - 0.5);
  const iyb = Math.floor((foot.y + a - y0) / h - 0.5);

  // First pass: the full lattice sum and the in-grid sum; second pass: the scaled deposit inside.
  let fullSum = 0;
  let insideSum = 0;
  for (let iy = iya; iy <= iyb; iy++) {
    const dy = y0 + (iy + 0.5) * h - foot.y;
    const rowInside = iy >= 0 && iy < ny;
    for (let ix = ixa; ix <= ixb; ix++) {
      const dx = x0 + (ix + 0.5) * h - foot.x;
      const s = 1 - (dx * dx + dy * dy + z2) / R2;
      if (s > 0) {
        fullSum += s * s;
        if (rowInside && ix >= 0 && ix < nx) insideSum += s * s;
      }
    }
  }
  if (insideSum === 0) return 0;
  const area = h * h / MM2_PER_M2;
  const scale = total / (Math.max(insideSum, fullSum / EDGE_BOOST_MAX) * area);
  const iy0 = Math.max(0, iya);
  const iy1 = Math.min(ny - 1, iyb);
  const ix0 = Math.max(0, ixa);
  const ix1 = Math.min(nx - 1, ixb);
  for (let iy = iy0; iy <= iy1; iy++) {
    const dy = y0 + (iy + 0.5) * h - foot.y;
    for (let ix = ix0; ix <= ix1; ix++) {
      const dx = x0 + (ix + 0.5) * h - foot.x;
      const s = 1 - (dx * dx + dy * dy + z2) / R2;
      if (s > 0) density[iy * nx + ix] += scale * s * s;
    }
  }
  return scale * insideSum * area;
}

/**
 * One conservative 9-point substep over the cell box [bx0, bx1] x [by0, by1]: every edge with both ends in the box
 * carries w·k·(source_j - source_i) from j to i, applied antisymmetrically to `density`. Edges are visited once
 * each, so nothing crosses the reflecting boundary. The caller guarantees `source` is zero wherever an edge leaving
 * the box could carry flux. `delta` must be zero on entry and is zero again on return.
 * @param {Float64Array} density mutated
 * @param {Float64Array} source values whose differences drive the flux
 * @param {Float64Array} delta scratch
 * @param {number} nx grid width
 * @param {number} k per-substep factor, ≤ K_MAX
 * @param {number} bx0
 * @param {number} bx1
 * @param {number} by0
 * @param {number} by1
 */
function relax(density, source, delta, nx, k, bx0, bx1, by0, by1) {
  const a = W_ORTHO * k;
  const b = W_DIAG * k;
  for (let iy = by0; iy <= by1; iy++) {
    const row = iy * nx;
    const hasSouth = iy + 1 <= by1;
    for (let ix = bx0; ix <= bx1; ix++) {
      const i = row + ix;
      const si = source[i];
      let d = 0;
      if (ix + 1 <= bx1) {
        const f = a * (source[i + 1] - si);
        d += f;
        delta[i + 1] -= f;
      }
      if (hasSouth) {
        const j = i + nx;
        const f = a * (source[j] - si);
        d += f;
        delta[j] -= f;
        if (ix + 1 <= bx1) {
          const g = b * (source[j + 1] - si);
          d += g;
          delta[j + 1] -= g;
        }
        if (ix - 1 >= bx0) {
          const g = b * (source[j - 1] - si);
          d += g;
          delta[j - 1] -= g;
        }
      }
      delta[i] += d;
    }
  }
  for (let iy = by0; iy <= by1; iy++) {
    const row = iy * nx;
    for (let i = row + bx0; i <= row + bx1; i++) {
      density[i] += delta[i];
      delta[i] = 0;
    }
  }
}

/**
 * Background diffusion with coefficient `params.D` over one step `params.dt`, substepped for stability.
 * @param {import('./state.mjs').Grid} grid mutated
 * @param {import('../params.mjs').GladeParams} params
 * @param {FieldScratch} scratch
 */
export function diffuse(grid, params, scratch) {
  checkScratch(grid, scratch);
  const n = substeps(params.D, params.dt, grid.h);
  const k = params.D * (params.dt / n) / (grid.h * grid.h);
  const { nx, ny } = grid;
  for (let s = 0; s < n; s++) relax(grid.density, grid.density, scratch.delta, nx, k, 0, nx - 1, 0, ny - 1);
}

/**
 * Spill: diffusion of the excess max(0, density - E_cap) with `params.D_spill`, the excess frozen at the start of
 * each substep, so a cell never loses more than its excess. Each substep works only on the box of above-cap cells
 * grown by one cell, and the operator stops early once no cell is above the cap.
 * @param {import('./state.mjs').Grid} grid mutated
 * @param {import('../params.mjs').GladeParams} params
 * @param {FieldScratch} scratch
 */
export function spill(grid, params, scratch) {
  checkScratch(grid, scratch);
  const { density, nx, ny } = grid;
  const { excess } = scratch;
  const n = substeps(params.D_spill, params.dt, grid.h);
  const k = params.D_spill * (params.dt / n) / (grid.h * grid.h);
  for (let s = 0; s < n; s++) {
    // Excess pass, tracking the box of cells above the cap; only edges touching it can carry flux.
    let minX = nx;
    let maxX = -1;
    let minY = ny;
    let maxY = -1;
    for (let iy = 0; iy < ny; iy++) {
      const row = iy * nx;
      for (let ix = 0; ix < nx; ix++) {
        const e = density[row + ix] - params.E_cap;
        if (e > 0) {
          excess[row + ix] = e;
          if (ix < minX) minX = ix;
          if (ix > maxX) maxX = ix;
          if (iy < minY) minY = iy;
          if (iy > maxY) maxY = iy;
        } else {
          excess[row + ix] = 0;
        }
      }
    }
    if (maxX < 0) return;
    relax(density, excess, scratch.delta, nx, k,
      Math.max(0, minX - 1), Math.min(nx - 1, maxX + 1), Math.max(0, minY - 1), Math.min(ny - 1, maxY + 1));
  }
}

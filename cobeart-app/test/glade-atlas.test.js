// Glade placeholder atlas sizing and validation (public/worlds/glade/render/atlas.mjs, imported from CommonJS).
// generateAtlas validates before creating a canvas, so these run in node without one.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const loadAtlas = () => import('../public/worlds/glade/render/atlas.mjs');
const width = (n, cellPx) => n * (cellPx + 2 * Math.ceil(cellPx / 16));

test('maxCellPx matches the formula for known frame counts', async () => {
  const { maxCellPx } = await loadAtlas();
  assert.equal(maxCellPx(30), 120);
  assert.equal(maxCellPx(64), 56);
});

test('maxCellPx is the largest cellPx that fits', async () => {
  const { maxCellPx } = await loadAtlas();
  for (const n of [2, 10, 30, 37, 38, 64]) {
    const fit = maxCellPx(n);
    assert.ok(width(n, fit) <= 4096, `n=${n} fit=${fit}`);
    assert.ok(width(n, fit + 1) > 4096, `n=${n} fit+1=${fit + 1}`);
  }
});

test('maxCellPx throws naming N_frames when nothing fits or input is invalid', async () => {
  const { maxCellPx } = await loadAtlas();
  assert.throws(() => maxCellPx(1000), /N_frames=1000/);
  assert.throws(() => maxCellPx(1), /nFrames/);
});

test('generateAtlas rejects an oversized atlas naming N_frames and cellPx', async () => {
  const { generateAtlas } = await loadAtlas();
  assert.throws(() => generateAtlas({ nFrames: 64, cellPx: 57 }), /N_frames=64.*cellPx=57.*cellPx <= 56/);
});

test('generateAtlas validates nFrames and cellPx', async () => {
  const { generateAtlas } = await loadAtlas();
  assert.throws(() => generateAtlas({ nFrames: 1, cellPx: 96 }), /nFrames.*got 1/);
  assert.throws(() => generateAtlas({ nFrames: 2.5, cellPx: 96 }), /nFrames.*got 2.5/);
  assert.throws(() => generateAtlas({ nFrames: 30, cellPx: 7 }), /cellPx.*got 7/);
  assert.throws(() => generateAtlas({ nFrames: 30, cellPx: 96.5 }), /cellPx.*got 96.5/);
});

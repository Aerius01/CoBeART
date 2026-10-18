// Mouse source maths (public/sources/mouse-math.mjs, an ES module imported from this CommonJS test).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Ajv2020 = require('ajv/dist/2020');

const CONTRACT_DIR = path.join(__dirname, '..', '..', 'contract');
const ARENA = { x: [-3000, 3000], y: [-3000, 3000], z: [0, 2500] };
const RECT = { left: 100, top: 50, width: 1000, height: 500 };
const Z_RANGE = ARENA.z;

const loadMath = () => import('../public/sources/mouse-math.mjs');

const poseFrameValidator = () => {
  const ajv = new Ajv2020({ allErrors: true, allowUnionTypes: true });
  for (const name of ['frame', 'pose_frame']) {
    ajv.addSchema(JSON.parse(fs.readFileSync(path.join(CONTRACT_DIR, `${name}.schema.json`), 'utf8')));
  }
  return ajv.getSchema('https://cobeart.invalid/contract/pose_frame.schema.json');
};

test('top-left maps to (x_max, y_min), bottom-right to (x_min, y_max)', async () => {
  const { screenToArena } = await loadMath();
  assert.deepEqual(screenToArena(100, 50, RECT, ARENA), { x: 3000, y: -3000 });
  assert.deepEqual(screenToArena(1100, 550, RECT, ARENA), { x: -3000, y: 3000 });
  assert.deepEqual(screenToArena(600, 300, RECT, ARENA), { x: 0, y: 0 });
});

test('points outside the layer clamp to the arena; results are integers', async () => {
  const { screenToArena } = await loadMath();
  assert.deepEqual(screenToArena(-500, -500, RECT, ARENA), { x: 3000, y: -3000 });
  assert.deepEqual(screenToArena(9999, 9999, RECT, ARENA), { x: -3000, y: 3000 });
  const { x, y } = screenToArena(333, 77, RECT, ARENA);
  assert.ok(Number.isInteger(x) && Number.isInteger(y));
});

test('mapping inverts the Splat screen mapping (norm_x = (x_max - x) / span, norm_y = (y - y_min) / span)', async () => {
  const { screenToArena } = await loadMath();
  const { x, y } = screenToArena(350, 175, RECT, ARENA);
  assert.ok(Math.abs((ARENA.x[1] - x) / 6000 - 0.25) < 1e-3);
  assert.ok(Math.abs((y - ARENA.y[0]) / 6000 - 0.25) < 1e-3);
});

test('wheel up is higher, wheel down lower, and notches accumulate', async () => {
  const { applyWheel, wheelNotch } = await loadMath();
  assert.equal(wheelNotch(-100), 1);
  assert.equal(wheelNotch(3), -1);
  assert.throws(() => wheelNotch(0), RangeError);
  let z = 1200;
  for (let i = 0; i < 3; i++) z = applyWheel(z, wheelNotch(-120), 50, Z_RANGE);
  assert.equal(z, 1350);
  z = applyWheel(z, wheelNotch(120), 50, Z_RANGE);
  assert.equal(z, 1300);
});

test('z clamps at both arena ends', async () => {
  const { applyWheel } = await loadMath();
  assert.equal(applyWheel(2480, 1, 50, Z_RANGE), 2500);
  assert.equal(applyWheel(2500, 1, 50, Z_RANGE), 2500);
  assert.equal(applyWheel(20, -1, 50, Z_RANGE), 0);
  assert.equal(applyWheel(0, -1, 50, Z_RANGE), 0);
});

test('held and released pose_frames validate against pose_frame.schema.json', async () => {
  const { buildHeldFrame, buildReleaseFrame } = await loadMath();
  const validate = poseFrameValidator();
  const held = buildHeldFrame('right_hand', { x: 450, y: -1200, z: 1350 }, 12.345);
  assert.deepEqual(held.bodies.right_hand, { x: 450, y: -1200, z: 1350, qx: 0, qy: 0, qz: 0, qw: 1 });
  assert.equal(validate(held), true, JSON.stringify(validate.errors));
  const released = buildReleaseFrame(12.4);
  assert.deepEqual(released.bodies, {});
  assert.equal(validate(released), true, JSON.stringify(validate.errors));
});

test('a frame with an unknown role is rejected by the schema (guards the role enum)', async () => {
  const { buildHeldFrame } = await loadMath();
  assert.equal(poseFrameValidator()(buildHeldFrame('mouse', { x: 0, y: 0, z: 0 }, 1)), false);
});

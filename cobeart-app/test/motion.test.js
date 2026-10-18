// Port of the Python MetricsTracker tests (cobeart/input/optitrack/tests/test_metrics.py at the Phase 6 base),
// one test per Python case, plus the frame-level rules of the hub velocity stage.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createMotionTracker, MIN_DT_S } = require('../electron/motion');

const SHIPPED = { max_vel: 13000.0, history_window: 15, max_gap_s: 0.25 };
const ROLE = 'right_hand';
const IDENTITY = { qx: 0, qy: 0, qz: 0, qw: 1 };
const DEG = Math.PI / 180;

/** A tracker with the values config/cobeart.yaml ships, overridable per test (as the Python make_tracker). */
const makeTracker = (overrides = {}, options = {}) => createMotionTracker({ ...SHIPPED, ...overrides }, options);

/** Source clock starting at 100 s, as the Python FakeClock. */
const makeClock = (start = 100.0) => {
  const clock = { now: start, advance: (dt) => { clock.now += dt; } };
  return clock;
};

/** Updates one role at `time` and returns its body. */
const update = (tracker, time, x, y, z, q = IDENTITY, role = ROLE) =>
  tracker.track(time, { [role]: { x, y, z, ...q } })[role];

// Quaternion helpers (scalar last, as scipy): the tests build rotations the way the Python tests do with scipy.
const fromRotvec = ([rx, ry, rz]) => {
  const angle = Math.hypot(rx, ry, rz);
  if (angle === 0) return { ...IDENTITY };
  const s = Math.sin(angle / 2) / angle;
  return { qx: rx * s, qy: ry * s, qz: rz * s, qw: Math.cos(angle / 2) };
};
const AXES = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const fromEuler = (axis, degrees) => fromRotvec(AXES[axis].map((c) => c * degrees * DEG));
/** Hamilton product a * b (apply b, then a), as scipy's `a * b`. */
const multiply = (a, b) => ({
  qx: a.qw * b.qx + a.qx * b.qw + a.qy * b.qz - a.qz * b.qy,
  qy: a.qw * b.qy - a.qx * b.qz + a.qy * b.qw + a.qz * b.qx,
  qz: a.qw * b.qz + a.qx * b.qy - a.qy * b.qx + a.qz * b.qw,
  qw: a.qw * b.qw - a.qx * b.qx - a.qy * b.qy - a.qz * b.qz
});
const negate = (q) => ({ qx: -q.qx, qy: -q.qy, qz: -q.qz, qw: -q.qw });

/** Seeded normal(0, scale) generator (mulberry32 plus Box-Muller), for reproducible random rotations. */
const seededNormal = (seed) => {
  let a = seed >>> 0;
  const uniform = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return (scale = 1) => scale * Math.sqrt(-2 * Math.log(1 - uniform())) * Math.cos(2 * Math.PI * uniform());
};
const randomRotation = (normal) => {
  const v = [normal(), normal(), normal(), normal()];
  const n = Math.hypot(...v);
  return { qx: v[0] / n, qy: v[1] / n, qz: v[2] / n, qw: v[3] / n };
};

const velocity = (b) => [b.vx, b.vy, b.vz];
const angular = (b) => [b.wx, b.wy, b.wz];
const position = (b) => [b.x, b.y, b.z];
const quaternion = (b) => ({ qx: b.qx, qy: b.qy, qz: b.qz, qw: b.qw });
/** numpy.allclose defaults: |a - b| <= 1e-8 + 1e-5 * |b| element-wise. */
const assertAllClose = (actual, expected, message) => {
  actual.forEach((a, i) => assert.ok(Math.abs(a - expected[i]) <= 1e-8 + 1e-5 * Math.abs(expected[i]),
    `${message ?? ''} [${actual}] != [${expected}]`));
};
const approx = (a, b) => Math.abs(a - b) <= 1e-6 * Math.abs(b) + 1e-12;

test('first sighting returns zeros', () => {
  const clock = makeClock();
  const q = fromEuler('x', 30);
  const m = update(makeTracker(), clock.now, 5, 6, 7, q);
  assert.deepEqual(velocity(m), [0, 0, 0]);
  assert.deepEqual(angular(m), [0, 0, 0]);
  assert.equal(m.abs_vel, 0);
  assert.equal(m.norm_abs_vel, 0);
  assert.deepEqual(position(m), [5, 6, 7]);
  assert.deepEqual(quaternion(m), q);
});

test('velocity math with fake clock', () => {
  const clock = makeClock();
  const tracker = makeTracker({ max_vel: 1000.0, max_gap_s: 10.0 });
  update(tracker, clock.now, 0, 0, 0);
  clock.advance(0.5);
  const m = update(tracker, clock.now, 300, 400, 50, fromRotvec([0, 0, 10 * DEG]));
  assert.deepEqual(velocity(m), [600, 800, 100]);
  assertAllClose(angular(m), [0, 0, 20]);
  assert.equal(m.abs_vel, 1000);
  assert.equal(m.norm_abs_vel, 1);
});

test('zero time diff keeps previous velocity and new pose', () => {
  const clock = makeClock();
  const tracker = makeTracker();
  update(tracker, clock.now, 0, 0, 0);
  const m = update(tracker, clock.now, 10, 10, 10, fromEuler('z', 45));
  assert.deepEqual(velocity(m), [0, 0, 0]);
  assert.deepEqual(angular(m), [0, 0, 0]);
  assert.equal(m.abs_vel, 0);
  assert.deepEqual(position(m), [10, 10, 10]);
});

test('dt below floor does not spike and next frame uses full interval', () => {
  const clock = makeClock();
  const tracker = makeTracker({ max_vel: 10000.0 }, { minDtS: 0.0005 });
  const dt = 1 / 240;
  const speed = 1000.0; // mm/s along x
  let x = 0.0;
  update(tracker, clock.now, x, 0, 0);
  clock.advance(dt);
  x += speed * dt;
  assert.ok(approx(update(tracker, clock.now, x, 0, 0).abs_vel, speed));
  // A frame 0.1 ms later carrying 1 mm of noise would read as 10 m/s if divided.
  clock.advance(0.0001);
  const held = update(tracker, clock.now, x + 1.0, 0, 0);
  assert.ok(approx(held.abs_vel, speed));
  assert.equal(held.x, x + 1.0);
  // The next frame differentiates from the last accepted pose over the whole interval.
  clock.advance(dt - 0.0001);
  x += speed * dt;
  assert.ok(approx(update(tracker, clock.now, x, 0, 0).abs_vel, speed));
});

test('clock going backwards resets', () => {
  const clock = makeClock();
  const tracker = makeTracker({ max_vel: 1000.0 });
  update(tracker, clock.now, 0, 0, 0);
  clock.advance(0.1);
  update(tracker, clock.now, 100, 0, 0);
  clock.advance(-5.0);
  const m = update(tracker, clock.now, 900, 0, 0);
  assert.deepEqual(velocity(m), [0, 0, 0]);
  assert.equal(m.norm_abs_vel, 0);
});

for (const flipSign of [false, true]) {
  test(`constant rotation about z across heading wrap (flip_sign=${flipSign})`, () => {
    const clock = makeClock();
    const rate = 90.0; // deg/s
    const dt = 0.25;
    const tracker = makeTracker();
    for (let step = 0; step < 20; step += 1) { // 0 to 427.5 degrees, crossing +-180 and 360
      let q = fromEuler('z', rate * dt * step);
      if (flipSign && step % 2) q = negate(q); // same orientation, opposite sign
      const m = update(tracker, clock.now, 0, 0, 0, q);
      if (step > 0) assertAllClose(angular(m), [0, 0, rate], `step ${step}`);
      clock.advance(dt);
    }
  });
}

test('angular velocity is in world frame', () => {
  // A body leaned 40 degrees about x, then turning about world z, reports pure wz.
  const clock = makeClock();
  const lean = fromEuler('x', 40);
  const tracker = makeTracker();
  update(tracker, clock.now, 0, 0, 0, lean);
  clock.advance(0.1);
  const m = update(tracker, clock.now, 0, 0, 0, multiply(fromEuler('z', -6), lean));
  assertAllClose(angular(m), [0, 0, -60]);
});

test('angular velocity matches the rotation vector (scipy as_rotvec in the Python test)', () => {
  const clock = makeClock();
  const normal = seededNormal(7);
  const tracker = makeTracker();
  let prev = randomRotation(normal);
  update(tracker, clock.now, 0, 0, 0, prev);
  for (let i = 0; i < 50; i += 1) {
    const rotvec = [normal(0.3), normal(0.3), normal(0.3)];
    // Below pi the rotation vector is the shortest one, so it is what as_rotvec would return.
    assert.ok(Math.hypot(...rotvec) < Math.PI);
    const now = multiply(fromRotvec(rotvec), prev);
    clock.advance(0.02);
    const m = update(tracker, clock.now, 0, 0, 0, now);
    assertAllClose(angular(m), rotvec.map((r) => r / DEG / 0.02), `sample ${i}`);
    prev = now;
  }
});

test('norm velocity zeroes only after full window', () => {
  const clock = makeClock();
  const window = 4;
  const tracker = makeTracker({ max_vel: 1000.0, history_window: window, max_gap_s: 10.0 });
  update(tracker, clock.now, 0, 0, 0);
  // A single movement sample is held as the window max, then decays to zero once pushed out.
  clock.advance(1.0);
  assert.equal(update(tracker, clock.now, 500, 0, 0).norm_abs_vel, 0.5);
  const held = [];
  for (let i = 0; i < window; i += 1) {
    clock.advance(1.0);
    held.push(update(tracker, clock.now, 500, 0, 0).norm_abs_vel);
  }
  assert.deepEqual(held, [...new Array(window - 1).fill(0.5), 0]);
  // Still zero after a further full window of zeros.
  clock.advance(1.0);
  assert.equal(update(tracker, clock.now, 500, 0, 0).norm_abs_vel, 0);
});

test('new still body never reports speed', () => {
  const clock = makeClock();
  const tracker = makeTracker({ history_window: 4 });
  for (let i = 0; i < 10; i += 1) {
    assert.equal(update(tracker, clock.now, 0, 0, 0).norm_abs_vel, 0);
    clock.advance(0.01);
  }
});

test('gap longer than max_gap resets to first sighting', () => {
  const clock = makeClock();
  const tracker = makeTracker({ max_vel: 1000.0, max_gap_s: 0.25 });
  update(tracker, clock.now, 0, 0, 0);
  clock.advance(0.1);
  assert.ok(approx(update(tracker, clock.now, 100, 0, 0).abs_vel, 1000));
  clock.advance(0.3);
  const m = update(tracker, clock.now, 5000, 0, 0, fromEuler('z', 90));
  assert.deepEqual(velocity(m), [0, 0, 0]);
  assert.deepEqual(angular(m), [0, 0, 0]);
  assert.equal(m.norm_abs_vel, 0);
});

test('gap below max_gap computes velocity', () => {
  const clock = makeClock();
  const tracker = makeTracker({ max_vel: 1000.0, max_gap_s: 0.25 });
  update(tracker, clock.now, 0, 0, 0);
  clock.advance(0.2);
  const m = update(tracker, clock.now, 100, 0, 0, fromEuler('z', 18));
  assertAllClose(velocity(m), [500, 0, 0]);
  assertAllClose(angular(m), [0, 0, 90]);
});

test('interleaved bodies do not share smoothing', () => {
  const clock = makeClock();
  const window = 3;
  const tracker = makeTracker({ max_vel: 1000.0, history_window: window, max_gap_s: 10.0 });
  update(tracker, clock.now, 0, 0, 0, IDENTITY, 'right_hand');
  update(tracker, clock.now, 0, 0, 0, IDENTITY, 'left_hand');
  let lastFast;
  let lastStill;
  for (let i = 1; i <= 2 * window; i += 1) {
    clock.advance(1.0);
    lastFast = update(tracker, clock.now, 1000 * i, 0, 0, IDENTITY, 'right_hand'); // 1000 mm/s
    lastStill = update(tracker, clock.now, 0, 0, 0, IDENTITY, 'left_hand');
  }
  // Shared history would let the fast role's motion keep the still role above zero.
  assert.equal(lastFast.norm_abs_vel, 1);
  assert.equal(lastStill.norm_abs_vel, 0);
});

// Frame-level rules of the hub stage (not in the Python tests).

test('clock going backwards resets every role, including roles absent from that frame', () => {
  const tracker = makeTracker({ max_vel: 1000.0 });
  const pose = (x) => ({ x, y: 0, z: 0, ...IDENTITY });
  tracker.track(10.0, { right_hand: pose(0), left_hand: pose(0) });
  tracker.track(10.1, { right_hand: pose(100), left_hand: pose(100) });
  tracker.track(5.0, { right_hand: pose(900) });
  // The source restarted (5.0 s) and then jumped forward again; left_hand was last seen at 10.1 s, so without the
  // reset of every role 10.2 s would differentiate against that stale pose and report 1000 mm/s.
  const { left_hand: leftHand } = tracker.track(10.2, { left_hand: pose(200) });
  assert.equal(leftHand.vx, 0);
  assert.equal(leftHand.norm_abs_vel, 0);
});

test('frame bodies keep the pose and carry exactly the frame.schema.json body fields in order', () => {
  const tracker = makeTracker();
  const pose = { x: 1, y: 2, z: 3, qx: 0, qy: 0, qz: 0, qw: 1 };
  const bodies = tracker.track(1.0, { head: pose });
  assert.deepEqual(Object.keys(bodies.head),
    ['x', 'y', 'z', 'qx', 'qy', 'qz', 'qw', 'vx', 'vy', 'vz', 'wx', 'wy', 'wz', 'abs_vel', 'norm_abs_vel']);
  assert.deepEqual(tracker.track(1.1, {}), {});
});

test('min_dt_s is 0.5 ms and invalid metrics are rejected naming the key', () => {
  assert.equal(MIN_DT_S, 0.0005);
  assert.throws(() => makeTracker({ max_vel: 0 }), /metrics\.max_vel/);
  assert.throws(() => makeTracker({ history_window: 1.5 }), /metrics\.history_window/);
  assert.throws(() => makeTracker({ max_gap_s: -1 }), /metrics\.max_gap_s/);
});

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { io: connect } = require('socket.io-client');
const { createHub } = require('../electron/hub');

const MAX_GAP_S = 0.3;
const METRICS = { max_vel: 13000.0, history_window: 3, max_gap_s: MAX_GAP_S };
const SETTLE_MS = 100;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const connectNamespace = (baseUrl, namespace) =>
  new Promise((resolve, reject) => {
    const socket = connect(`${baseUrl}${namespace}`, { transports: ['websocket'], forceNew: true });
    socket.once('connect', () => resolve(socket));
    socket.once('connect_error', reject);
  });

const pose = (x) => ({ x, y: 0, z: 1000, qx: 0, qy: 0, qz: 0, qw: 1 });
const poseFrame = (sourceTimeS, x) => ({ schemaVersion: 2, source_time_s: sourceTimeS, bodies: { right_hand: pose(x) } });

let hub;
let baseUrl;
let viewer;
let frames;
let a;
let b;

const health = async () => (await fetch(`${baseUrl}/health`)).json();

/** Emits on `socket` and returns the viewer frames that arrived within the settle window. */
const framesFrom = async (socket, ...payloads) => {
  const start = frames.length;
  payloads.forEach((payload) => socket.emit('pose_frame', payload));
  await sleep(SETTLE_MS);
  return frames.slice(start);
};

before(async () => {
  hub = await createHub({
    port: 0,
    host: '127.0.0.1',
    publicDir: path.join(__dirname, '..', 'public'),
    metrics: METRICS,
    frontendConfig: {}
  });
  baseUrl = `http://127.0.0.1:${hub.server.address().port}`;
  viewer = await connectNamespace(baseUrl, '/viewer');
  frames = [];
  viewer.on('frame', (frame) => frames.push(frame));
  a = await connectNamespace(baseUrl, '/ingest');
  b = await connectNamespace(baseUrl, '/ingest');
});

after(async () => {
  [viewer, a, b].forEach((socket) => socket.close());
  await hub.close();
});

test('the second source is ignored while the first is active; it takes over after silence with a fresh motion state', async (t) => {
  const errors = t.mock.method(console, 'error', () => {});
  assert.equal((await health()).activeSource, null);

  const fromA = await framesFrom(a, poseFrame(100, 0), poseFrame(100.01, 10));
  assert.equal(fromA.length, 2);
  assert.equal((await health()).activeSource, a.id, 'health names the active socket');

  assert.deepEqual(await framesFrom(b, poseFrame(5, 999), poseFrame(5.01, 999)), [], 'B never reaches /viewer');
  assert.match(errors.mock.calls.map((call) => call.arguments[0]).join('\n'),
    new RegExp(`pose_frame from ${b.id} ignored: ${a.id} is the active source; stop it or wait ${MAX_GAP_S} s`));
  assert.equal((await health()).activeSource, a.id);

  await sleep(MAX_GAP_S * 1000 + 50);
  assert.equal((await health()).activeSource, null, 'a silent source is no longer active');
  // B's clock (5 s) is behind A's (100 s) and its pose is far away: without a reset the motion would be derived across both.
  const [first, second] = await framesFrom(b, poseFrame(5.02, 500), poseFrame(5.03, 510));
  assert.deepEqual(
    [first.bodies.right_hand.vx, first.bodies.right_hand.abs_vel, first.bodies.right_hand.norm_abs_vel],
    [0, 0, 0], 'first forwarded frame has all-zero derived values');
  assert.ok(second.bodies.right_hand.vx > 0);
  assert.equal((await health()).activeSource, b.id);
});

test('a source takes over immediately when the active one disconnects', async () => {
  await sleep(MAX_GAP_S * 1000 + 50); // the previous test left B active
  const c = await connectNamespace(baseUrl, '/ingest');
  const d = await connectNamespace(baseUrl, '/ingest');
  try {
    assert.equal((await framesFrom(c, poseFrame(200, 0))).length, 1);
    assert.deepEqual(await framesFrom(d, poseFrame(1, 0)), [], 'ignored while C is active and connected');

    c.close();
    await sleep(SETTLE_MS);
    assert.equal((await health()).activeSource, null);
    const [first] = await framesFrom(d, poseFrame(1.01, 300));
    assert.ok(first, 'D is forwarded right after C left, well within max_gap_s');
    assert.equal(first.bodies.right_hand.abs_vel, 0);
  } finally {
    c.close();
    d.close();
  }
});

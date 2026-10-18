const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Ajv2020 = require('ajv/dist/2020');
const { io: connect } = require('socket.io-client');
const { createHub } = require('../electron/hub');

/** Connects a client to a namespace and resolves once connected. */
const connectNamespace = (baseUrl, namespace) =>
  new Promise((resolve, reject) => {
    const socket = connect(`${baseUrl}${namespace}`, { transports: ['websocket'], forceNew: true });
    socket.once('connect', () => resolve(socket));
    socket.once('connect_error', reject);
  });

const METRICS = { max_vel: 13000.0, history_window: 3, max_gap_s: 0.25 };
const FRONTEND_CONFIG = { debug: false, arena: { x: [-1, 1], y: [-1, 1], z: [0, 1] } };
const SETTLE_MS = 100;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Validators for the contract messages the hub emits, compiled the way the hub compiles them. */
const contract = (() => {
  const ajv = new Ajv2020({ allowUnionTypes: true });
  const dir = path.join(__dirname, '..', '..', 'contract');
  const names = ['pose_frame', 'frame', 'audio_metrics', 'audio'];
  const schemas = names.map((name) => JSON.parse(fs.readFileSync(path.join(dir, `${name}.schema.json`), 'utf8')));
  schemas.forEach((schema) => ajv.addSchema(schema));
  return Object.fromEntries(names.map((name, i) => [name, ajv.getSchema(schemas[i].$id)]));
})();

const assertValid = (name, message) => {
  const validate = contract[name];
  assert.ok(validate(message), `${name} invalid: ${JSON.stringify(validate.errors)}`);
};

const validAudio = () => ({
  schemaVersion: 2,
  rms: 0.5,
  peak: 0.9,
  zcr: 0.1,
  dominant_frequency: 440,
  rms_db: 0.5,
  rms_envelope: 0.5,
  is_peak: false,
  peak_intensity: 0,
  is_onset: false,
  onset_strength: 0,
  spectrum_2d: [[0, 0.5]],
  spectrum_config: { width: 2, height: 1, freq_min: 20, freq_max: 20000 },
  beat: false,
  tempo_bpm: null,
  beat_timestamp: null
});

const pose = (x = 10, y = 20, z = 30) => ({ x, y, z, qx: 0, qy: 0, qz: 0, qw: 1 });

const poseFrame = (sourceTimeS, bodies = { right_hand: pose() }) =>
  ({ schemaVersion: 2, source_time_s: sourceTimeS, bodies });

// Every test starts its source clock far beyond the previous test's, so no motion state carries over.
let clockBase = 0;
const freshSourceTime = () => (clockBase += 1000);

/** Starts a hub on an ephemeral port. */
const startHub = () => createHub({
  port: 0,
  host: '127.0.0.1',
  publicDir: path.join(__dirname, '..', 'public'),
  metrics: METRICS,
  frontendConfig: FRONTEND_CONFIG
});

let hub;
let baseUrl;
let viewer;
let ingest;
let audio;
const sockets = [];

before(async () => {
  hub = await startHub();
  baseUrl = `http://127.0.0.1:${hub.server.address().port}`;
  viewer = await connectNamespace(baseUrl, '/viewer');
  ingest = await connectNamespace(baseUrl, '/ingest');
  audio = await connectNamespace(baseUrl, '/audio');
  sockets.push(viewer, ingest, audio);
});

after(async () => {
  sockets.forEach((socket) => socket.close());
  await hub.close();
});

/** Runs `emit` and returns what `socket` receives on `event` within the settle window. */
const received = async (socket, event, emit) => {
  const messages = [];
  const collect = (message) => messages.push(message);
  socket.on(event, collect);
  await emit();
  await sleep(SETTLE_MS);
  socket.off(event, collect);
  return messages;
};

/** Emits pose_frames on /ingest and returns the frames the viewer receives; each one must match frame.schema.json. */
const framesAfter = async (...payloads) => {
  const frames = await received(viewer, 'frame', () => payloads.forEach((p) => ingest.emit('pose_frame', p)));
  frames.forEach((frame) => assertValid('frame', frame));
  return frames;
};

test('emits one frame per pose_frame with derived fields and hub timestamp, and serves /health', async () => {
  const before = Date.now();
  const t = freshSourceTime();
  const frames = await framesAfter(poseFrame(t), poseFrame(t + 0.01, { right_hand: pose(), head: pose(0, 0, 1700) }));

  assert.equal(frames.length, 2);
  const [first, second] = frames;
  assert.deepEqual(first.bodies, { right_hand: { ...pose(), vx: 0, vy: 0, vz: 0, wx: 0, wy: 0, wz: 0, abs_vel: 0, norm_abs_vel: 0 } });
  assert.deepEqual(Object.keys(second.bodies).sort(), ['head', 'right_hand']);
  assert.ok(first.timestamp >= before && first.timestamp <= Date.now(), 'timestamp is hub receipt time in epoch ms');
  frames.forEach((frame) => assert.equal('audio' in frame, false));

  const health = await (await fetch(`${baseUrl}/health`)).json();
  assert.equal(health.server, 'ok');
  assert.deepEqual(health.namespaces, { ingest: 1, audio: 1, viewer: 1 });
  assert.equal(typeof health.lastFrameAge, 'number');
  assert.ok(health.lastFrameAge >= 0);
});

test('velocity uses source_time_s, not receipt time (Must pass 2)', async () => {
  const t = freshSourceTime();
  // Sent back to back: receipt times are a few ms apart, source times 0.1 s apart.
  const frames = await framesAfter(
    poseFrame(t, { right_hand: pose(0, 0, 1000) }),
    poseFrame(t + 0.1, { right_hand: pose(100, 0, 1000) })
  );

  assert.equal(frames.length, 2);
  const hand = frames[1].bodies.right_hand;
  assert.ok(Math.abs(hand.vx - 1000) < 1e-6, `vx ${hand.vx}`);
  assert.ok(Math.abs(hand.abs_vel - 1000) < 1e-6, `abs_vel ${hand.abs_vel}`);
});

test('norm_abs_vel reaches 0 after a still history window; a gap above max_gap_s resets velocity (Must pass 3)', async () => {
  const t = freshSourceTime();
  const dt = 0.01;
  const moving = [0, 1, 2].map((i) => poseFrame(t + i * dt, { right_hand: pose(i * 50, 0, 1000) }));
  const still = Array.from({ length: METRICS.history_window }, (_, i) =>
    poseFrame(t + (3 + i) * dt, { right_hand: pose(100, 0, 1000) }));
  const frames = await framesAfter(...moving, ...still);

  const norm = frames.map((frame) => frame.bodies.right_hand.norm_abs_vel);
  assert.ok(norm[2] > 0, 'moving body has a speed');
  assert.ok(norm.slice(3, -1).every((v) => v > 0), 'held while the window still contains motion');
  assert.equal(norm.at(-1), 0);

  const tGap = t + (3 + METRICS.history_window) * dt;
  const [beforeGap, afterGap] = await framesAfter(
    poseFrame(tGap, { right_hand: pose(200, 0, 1000) }),
    poseFrame(tGap + METRICS.max_gap_s + 0.05, { right_hand: pose(900, 0, 1000) })
  );
  assert.ok(beforeGap.bodies.right_hand.vx > 0);
  assert.deepEqual([afterGap.bodies.right_hand.vx, afterGap.bodies.right_hand.abs_vel], [0, 0]);
});

test('rejects v1 payloads, unknown roles, velocity fields and malformed pose_frames (Must pass 4)', async (t) => {
  const errors = t.mock.method(console, 'error', () => {});
  const time = freshSourceTime();
  const v1Frame = {
    schemaVersion: 1,
    type: 'optitrack',
    timestamp: Date.now(),
    rigidbodies: [{ ID: 1, ...pose(), vx: 0, vy: 0, vz: 0, wx: 0, wy: 0, wz: 0, abs_vel: 0, norm_abs_vel: 0 }]
  };
  const rejected = [
    v1Frame,
    { ...poseFrame(time), schemaVersion: 1 },
    poseFrame(time, { left_elbow: pose() }),
    poseFrame(time, { right_hand: { ...pose(), vx: 0 } }),
    poseFrame(time, { right_hand: { ...pose(), ID: 1 } }),
    poseFrame(time, { right_hand: { ...pose(), x: 'left' } }),
    poseFrame(time, { right_hand: { ...pose(), x: 1.5 } }),
    { ...poseFrame(time), audio: { ...validAudio(), timestamp: Date.now() } },
    { ...poseFrame(time), extra: true },
    { ...poseFrame(time), source_time_s: -1 },
    'nope',
    null
  ];

  const frames = await received(viewer, 'frame', () => {
    rejected.forEach((payload) => ingest.emit('pose_frame', payload));
    ingest.emit('frame', v1Frame);
  });

  assert.deepEqual(frames, []);
  const logged = errors.mock.calls.map((call) => call.arguments[0]).join('\n');
  assert.match(logged, /Rejected frame payload: v1 event 'frame' is not accepted on \/ingest; send 'pose_frame'/);
  assert.match(logged, /Rejected pose_frame payload: schemaVersion mismatch: hub speaks 2, received 1/);
});

test('a NaN literal in a pose_frame packet is never forwarded (Must pass 4)', async (t) => {
  t.mock.method(console, 'error', () => {});
  const raw = await connectNamespace(baseUrl, '/ingest');
  const disconnected = new Promise((resolve) => raw.once('disconnect', resolve));
  // What a producer without a finite check would put on the wire; socket.io-client itself would send null instead.
  const packet = `2/ingest,["pose_frame",{"schemaVersion":2,"source_time_s":${freshSourceTime()},` +
    '"bodies":{"right_hand":{"x":NaN,"y":0,"z":0,"qx":0,"qy":0,"qz":0,"qw":1}}}]';

  const frames = await received(viewer, 'frame', () => raw.io.engine.write(packet));
  await disconnected;

  assert.deepEqual(frames, []);
  raw.close();
});

test('a frame with a non-finite derived value is dropped with a log line', async (t) => {
  const errors = t.mock.method(console, 'error', () => {});
  const time = freshSourceTime();
  // Valid integers whose difference overflows to Infinity in the velocity.
  const frames = await framesAfter(
    poseFrame(time, { right_hand: pose(-Number.MAX_VALUE, 0, 0) }),
    poseFrame(time + 0.01, { right_hand: pose(Number.MAX_VALUE, 0, 0) })
  );

  assert.equal(frames.length, 1, 'only the first sighting (all derived values 0) is emitted');
  assert.match(errors.mock.calls.map((call) => call.arguments[0]).join('\n'),
    /Rejected derived frame payload: does not match frame.schema.json/);
});

test('audio reaches /viewer as audio with the hub timestamp and no /ingest client; frames carry no audio (Must pass 5)', async () => {
  const ownHub = await startHub();
  const url = `http://127.0.0.1:${ownHub.server.address().port}`;
  const ownViewer = await connectNamespace(url, '/viewer');
  const ownAudio = await connectNamespace(url, '/audio');
  try {
    const health = await (await fetch(`${url}/health`)).json();
    assert.equal(health.namespaces.ingest, 0);
    assert.equal(health.lastFrameAge, null);

    const metrics = validAudio();
    const before = Date.now();
    const messages = await received(ownViewer, 'audio', () => ownAudio.emit('audio_metrics', metrics));

    assert.equal(messages.length, 1);
    const [message] = messages;
    assertValid('audio', message);
    const { timestamp, ...fields } = message;
    assert.deepEqual(fields, metrics);
    assert.ok(timestamp >= before && timestamp <= Date.now());
    assert.equal(typeof (await (await fetch(`${url}/health`)).json()).lastAudioAge, 'number');
  } finally {
    ownViewer.close();
    ownAudio.close();
    await ownHub.close();
  }

  // On the shared hub, with audio flowing, frames still carry no audio key.
  audio.emit('audio_metrics', validAudio());
  await sleep(SETTLE_MS / 2);
  const [frame] = await framesAfter(poseFrame(freshSourceTime()));
  assert.equal('audio' in frame, false);
});

test('rejects audio_metrics that are malformed, v1 or carry a timestamp', async (t) => {
  t.mock.method(console, 'error', () => {});
  const messages = await received(viewer, 'audio', () => {
    audio.emit('audio_metrics', { ...validAudio(), schemaVersion: 1 });
    audio.emit('audio_metrics', { ...validAudio(), rms: 'loud' });
    audio.emit('audio_metrics', { ...validAudio(), timestamp: Date.now() });
    audio.emit('audio_metrics', { rms: 0.5, peak: 0.9, zcr: 0.1, dominant_frequency: 440 });
  });

  assert.deepEqual(messages, []);
});

test('a viewer connecting after frames and audio receives nothing until the next message (Must pass 6)', async () => {
  const time = freshSourceTime();
  ingest.emit('pose_frame', poseFrame(time));
  audio.emit('audio_metrics', validAudio());
  await sleep(SETTLE_MS);

  const late = await connectNamespace(baseUrl, '/viewer');
  try {
    const replayed = [];
    late.on('frame', (frame) => replayed.push(['frame', frame]));
    late.on('audio', (message) => replayed.push(['audio', message]));
    await sleep(SETTLE_MS);
    assert.deepEqual(replayed, []);

    ingest.emit('pose_frame', poseFrame(time + 0.01));
    audio.emit('audio_metrics', validAudio());
    await sleep(SETTLE_MS);
    assert.deepEqual(replayed.map(([event]) => event).sort(), ['audio', 'frame']);
  } finally {
    late.close();
  }
});

test('serves the frontend config at /config.json and injects it into every HTML page', async () => {
  assert.deepEqual(await (await fetch(`${baseUrl}/config.json`)).json(), FRONTEND_CONFIG);

  const injection = `<script>window.__COBEART_CONFIG__=${JSON.stringify(FRONTEND_CONFIG)};</script>`;
  for (const page of ['/', '/index.html', '/host/', '/worlds/splat/']) {
    const html = await (await fetch(`${baseUrl}${page}`)).text();
    assert.ok(html.includes(injection), `${page} has no injected config`);
    assert.ok(html.indexOf(injection) < html.indexOf('<script', html.indexOf(injection) + 1), `${page}: config is not first`);
  }
});

test('does not serve HTML outside the public directory', async () => {
  const response = await fetch(`${baseUrl}/..%2Fpackage.json`);

  assert.notEqual(response.status, 200);
});

test('createHub names missing options', async () => {
  await assert.rejects(createHub({ port: 0, host: '127.0.0.1', publicDir: '.', frontendConfig: {} }), /metrics/);
});

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { io: connect } = require('socket.io-client');
const { createHub } = require('../electron/hub');

/** Connects a client to a namespace and resolves once connected. */
const connectNamespace = (baseUrl, namespace) =>
  new Promise((resolve, reject) => {
    const socket = connect(`${baseUrl}${namespace}`, { transports: ['websocket'], forceNew: true });
    socket.once('connect', () => resolve(socket));
    socket.once('connect_error', reject);
  });

const AUDIO_MAX_AGE_MS = 200;
const MAX_BODIES = 2;
const FRONTEND_CONFIG = { debug: false, arena: { x: [-1, 1], y: [-1, 1], z: [0, 1] } };
const SETTLE_MS = 100;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const validAudio = () => ({
  schemaVersion: 1,
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

const validBody = () => ({
  ID: 1, x: 10, y: 20, z: 30,
  qx: 0, qy: 0, qz: 0, qw: 1,
  vx: 0, vy: 0, vz: 0, wx: 0, wy: 0, wz: 0,
  abs_vel: 0, norm_abs_vel: 0
});

const validFrame = () => ({
  schemaVersion: 1,
  type: 'optitrack',
  timestamp: Date.now(),
  rigidbodies: [validBody()]
});

let hub;
let baseUrl;
let viewer;
let ingest;
let audio;
const sockets = [];

before(async () => {
  hub = await createHub({
    port: 0,
    host: '127.0.0.1',
    publicDir: path.join(__dirname, '..', 'public'),
    audioMaxAgeMs: AUDIO_MAX_AGE_MS,
    maxBodies: MAX_BODIES,
    frontendConfig: FRONTEND_CONFIG
  });
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

/** Emits on a socket and returns the frames the viewer receives within the settle window. */
const framesAfter = async (emit) => {
  const frames = [];
  const collect = (frame) => frames.push(frame);
  viewer.on('frame', collect);
  emit();
  await sleep(SETTLE_MS);
  viewer.off('frame', collect);
  return frames;
};

test('merges ingest frame with latest audio metrics and serves /health', async () => {
  const metrics = validAudio();
  audio.emit('audio_metrics', metrics);
  // Audio is a separate namespace with no ack; give the hub a moment to store it.
  await sleep(SETTLE_MS / 2);

  const [frame] = await framesAfter(() => ingest.emit('frame', validFrame()));

  assert.deepEqual(frame.rigidbodies, [validBody()]);
  assert.equal(typeof frame.timestamp, 'number');
  assert.deepEqual({ ...frame.audio, timestamp: undefined }, { ...metrics, timestamp: undefined });
  assert.equal(typeof frame.audio.timestamp, 'number');

  const health = await (await fetch(`${baseUrl}/health`)).json();
  assert.equal(health.server, 'ok');
  assert.deepEqual(health.namespaces, { ingest: 1, audio: 1, viewer: 1 });
  assert.equal(typeof health.lastAudioAge, 'number');
  assert.equal(typeof health.lastFrameAge, 'number');
});

test('omits audio older than audioMaxAgeMs', async () => {
  audio.emit('audio_metrics', validAudio());
  await sleep(AUDIO_MAX_AGE_MS + SETTLE_MS);

  const [frame] = await framesAfter(() => ingest.emit('frame', validFrame()));

  assert.equal(frame.audio, undefined);
});

test('does not forward a malformed frame', async () => {
  const malformed = { ...validFrame(), rigidbodies: [{ ...validBody(), x: 'left' }] };
  const unknownField = { ...validFrame(), extra: true };
  const withAudio = { ...validFrame(), audio: { ...validAudio(), timestamp: Date.now() } };
  const duplicateIds = { ...validFrame(), rigidbodies: [validBody(), validBody()] };

  const frames = await framesAfter(() => {
    [malformed, unknownField, withAudio, duplicateIds, 'nope', null].forEach((payload) => ingest.emit('frame', payload));
  });

  assert.deepEqual(frames, []);
});

test('rejects a frame with more rigid bodies than maxBodies', async () => {
  const bodies = [0, 1, 2].map((ID) => ({ ...validBody(), ID }));

  const frames = await framesAfter(() => ingest.emit('frame', { ...validFrame(), rigidbodies: bodies }));

  assert.deepEqual(frames, []);
});

test('serves the frontend config at /config.json and injects it into every HTML page', async () => {
  assert.deepEqual(await (await fetch(`${baseUrl}/config.json`)).json(), FRONTEND_CONFIG);

  const injection = `<script>window.__COBEART_CONFIG__=${JSON.stringify(FRONTEND_CONFIG)};</script>`;
  for (const page of ['/', '/index.html', '/fluid/', '/molten/', '/ink/', '/composite/']) {
    const html = await (await fetch(`${baseUrl}${page}`)).text();
    assert.ok(html.includes(injection), `${page} has no injected config`);
    assert.ok(html.indexOf(injection) < html.indexOf('<script', html.indexOf(injection) + 1), `${page}: config is not first`);
  }
});

test('does not serve HTML outside the public directory', async () => {
  const response = await fetch(`${baseUrl}/..%2Fpackage.json`);

  assert.notEqual(response.status, 200);
});

test('rejects a frame with the wrong schemaVersion', async () => {
  const frames = await framesAfter(() => ingest.emit('frame', { ...validFrame(), schemaVersion: 2 }));

  assert.deepEqual(frames, []);
});

test('rejects audio metrics that are malformed or have the wrong schemaVersion', async () => {
  const stale = validAudio();
  await sleep(AUDIO_MAX_AGE_MS + SETTLE_MS);
  audio.emit('audio_metrics', { ...stale, schemaVersion: 2 });
  audio.emit('audio_metrics', { ...stale, rms: 'loud' });
  audio.emit('audio_metrics', { rms: 0.5, peak: 0.9, zcr: 0.1, dominant_frequency: 440 });
  await sleep(SETTLE_MS / 2);

  const [frame] = await framesAfter(() => ingest.emit('frame', validFrame()));

  assert.equal(frame.audio, undefined);
});

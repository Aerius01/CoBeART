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

let hub;
let baseUrl;
const sockets = [];

before(async () => {
  hub = await createHub({ port: 0, host: '127.0.0.1', publicDir: path.join(__dirname, '..', 'public') });
  baseUrl = `http://127.0.0.1:${hub.server.address().port}`;
});

after(async () => {
  sockets.forEach((socket) => socket.close());
  await hub.close();
});

test('merges ingest frame with latest audio metrics and serves /health', async () => {
  const viewer = await connectNamespace(baseUrl, '/viewer');
  const ingest = await connectNamespace(baseUrl, '/ingest');
  const audio = await connectNamespace(baseUrl, '/audio');
  sockets.push(viewer, ingest, audio);

  const metrics = { rms: 0.5, peak: 0.9, zcr: 0.1, dominant_frequency: 440 };
  audio.emit('audio_metrics', metrics);
  // Audio is a separate namespace with no ack; give the hub a moment to store it.
  await new Promise((resolve) => setTimeout(resolve, 100));

  const received = new Promise((resolve) => viewer.once('frame', resolve));
  ingest.emit('frame', { rigidbodies: [{ id: 1, x: 10, y: 20, z: 30 }] });
  const frame = await received;

  assert.deepEqual(frame.rigidbodies, [{ id: 1, x: 10, y: 20, z: 30 }]);
  assert.equal(typeof frame.timestamp, 'number');
  assert.deepEqual({ ...frame.audio, timestamp: undefined }, { ...metrics, timestamp: undefined });
  assert.equal(typeof frame.audio.timestamp, 'number');

  const health = await (await fetch(`${baseUrl}/health`)).json();
  assert.equal(health.server, 'ok');
  assert.deepEqual(health.namespaces, { ingest: 1, audio: 1, viewer: 1 });
  assert.equal(typeof health.lastAudioAge, 'number');
  assert.equal(typeof health.lastFrameAge, 'number');
});

// Socket.IO hub shared by the Electron main process and the standalone web server.
// Turns pose_frames (/ingest) into frames with derived motion values and forwards audio metrics (/audio) as audio;
// both go to /viewer as separate streams.

const http = require('http');
const express = require('express');
const fs = require('fs');
const path = require('path');
const { Server } = require('socket.io');
const Ajv2020 = require('ajv/dist/2020');
const { createMotionTracker } = require('./motion');

const CONTRACT_DIR = path.join(__dirname, '..', '..', 'contract');
const SCHEMA_NAMES = ['pose_frame', 'frame', 'audio_metrics', 'audio'];
const SCHEMA_VERSION = 2;
const REJECT_LOG_INTERVAL_MS = 5000;
const MAX_REASON_CHARS = 300;
// A real audio_metrics message is about 48 KB (simulator, default spectrum); allow about 5x.
const MAX_PAYLOAD_BYTES = 256 * 1024;
const V1_FRAME_REASON = "v1 event 'frame' is not accepted on /ingest; send 'pose_frame' with schemaVersion 2";

/**
 * Compiles the contract schemas; they reference each other, so all four are registered first.
 * @returns {Record<string, import('ajv').ValidateFunction>} validators keyed by schema name
 */
function loadValidators() {
  const ajv = new Ajv2020({ allErrors: false, allowUnionTypes: true });
  const schemas = SCHEMA_NAMES.map((name) =>
    JSON.parse(fs.readFileSync(path.join(CONTRACT_DIR, `${name}.schema.json`), 'utf8')));
  schemas.forEach((schema) => ajv.addSchema(schema));
  return Object.fromEntries(SCHEMA_NAMES.map((name, i) => [name, ajv.getSchema(schemas[i].$id)]));
}

/** Returns a rate-limited reject logger: first rejection per kind logs, later ones are counted until the interval passes. */
function createRejectLogger(now) {
  const state = new Map();
  return (kind, reason) => {
    const entry = state.get(kind) ?? { lastLogged: -Infinity, suppressed: 0 };
    if (now() - entry.lastLogged < REJECT_LOG_INTERVAL_MS) {
      entry.suppressed += 1;
      state.set(kind, entry);
      return;
    }
    const extra = entry.suppressed > 0 ? ` (${entry.suppressed} similar rejections suppressed)` : '';
    console.error(`[electron] Rejected ${kind} payload: ${reason.slice(0, MAX_REASON_CHARS)}${extra}`);
    state.set(kind, { lastLogged: now(), suppressed: 0 });
  };
}

/** Joins ajv errors into one reason string. */
const describeErrors = (errors) => errors.map((e) => `${e.instancePath || '/'} ${e.message}`).join('; ');

/** Returns the absolute path of the HTML page a request URL path maps to inside `publicDir`, or null. */
function htmlPageFor(publicDir, urlPath) {
  const relative = decodeURIComponent(urlPath);
  const root = path.resolve(publicDir);
  const file = path.join(root, relative.endsWith('/') ? `${relative}index.html` : relative);
  return file.endsWith('.html') && file.startsWith(root + path.sep) ? file : null;
}

/** Inserts the frontend config as the first script of an HTML page, so every page and iframe reads window.__COBEART_CONFIG__. */
function injectConfig(html, config) {
  const script = `<script>window.__COBEART_CONFIG__=${JSON.stringify(config)};</script>`;
  if (!/<head[^>]*>/i.test(html)) throw new Error('Cannot inject the frontend config: page has no <head>');
  return html.replace(/<head[^>]*>/i, (head) => `${head}${script}`);
}

/**
 * Lists the worlds under `<publicDir>/worlds/`: the subfolders that contain an index.html, sorted by name.
 * @param {string} publicDir
 * @returns {Promise<{ name: string }[]>}
 */
async function listWorlds(publicDir) {
  const worldsDir = path.join(publicDir, 'worlds');
  const entries = await fs.promises.readdir(worldsDir, { withFileTypes: true });
  const hasIndex = async (name) => fs.promises.access(path.join(worldsDir, name, 'index.html')).then(() => true, () => false);
  const names = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  const withIndex = await Promise.all(names.map(async (name) => ((await hasIndex(name)) ? name : null)));
  return withIndex.filter((name) => name !== null).map((name) => ({ name }));
}

/**
 * Starts the hub and resolves once it is listening.
 * `metrics`: the velocity stage settings (`hubConfig(config).metrics`).
 * `frontendConfig`: JSON-serializable config injected into every HTML page and served at /config.json.
 * `now`: injectable clock (epoch ms) for the frame and audio `timestamp`s.
 * @param {{ port: number, host: string, publicDir: string,
 *   metrics: { max_vel: number, history_window: number, max_gap_s: number },
 *   frontendConfig: object, now?: () => number }} options
 * @returns {Promise<{ server: import('http').Server, io: import('socket.io').Server, close: () => Promise<void> }>}
 */
async function createHub({ port, host, publicDir, metrics, frontendConfig, now = Date.now }) {
  const missing = Object.entries({ port, host, publicDir, metrics, frontendConfig })
    .filter(([, value]) => value === undefined).map(([name]) => name);
  if (missing.length > 0) throw new Error(`createHub is missing required options: ${missing.join(', ')}`);

  let motion = createMotionTracker(metrics);
  const app = express();
  const server = http.createServer(app);
  const io = new Server(server, { maxHttpBufferSize: MAX_PAYLOAD_BYTES });

  app.get('/config.json', (req, res) => res.json(frontendConfig));

  // Computed per request, so a new world folder needs no restart. Registered before the static and HTML routes.
  app.get('/worlds.json', async (req, res, next) => {
    try {
      res.json({ worlds: await listWorlds(publicDir) });
    } catch (err) {
      next(err);
    }
  });

  // HTML pages get the frontend config injected; everything else is served as is.
  app.get(/(\.html|\/)$/, async (req, res, next) => {
    const page = htmlPageFor(publicDir, req.path);
    if (!page) return next();
    try {
      res.type('html').send(injectConfig(await fs.promises.readFile(page, 'utf8'), frontendConfig));
    } catch (err) {
      if (err.code === 'ENOENT') return next();
      next(err);
    }
  });

  // Serve the front-end so the BrowserWindow (or a browser) can load index.html from the hub.
  app.use(express.static(publicDir));

  const validators = loadValidators();
  const logReject = createRejectLogger(now);

  /** Returns null when an incoming payload is valid for `kind` (pose_frame or audio_metrics), otherwise a reason. */
  const rejectionReason = (kind, payload) => {
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      return 'payload is not an object';
    }
    if (payload.schemaVersion !== SCHEMA_VERSION) {
      return `schemaVersion mismatch: hub speaks ${SCHEMA_VERSION}, received ${JSON.stringify(payload.schemaVersion)}`;
    }
    const validate = validators[kind];
    return validate(payload) ? null : describeErrors(validate.errors);
  };

  // Socket.IO raises 'error' and closes the connection for a packet that is not valid JSON (e.g. a NaN literal),
  // before any event handler runs.
  const reportPacketError = (kind, err) => {
    logReject(kind, `undecodable packet (${err.message}); connection dropped by Socket.IO`);
  };

  let lastFrameTime = null;
  let lastAudioTime = null;

  // D20: one /ingest connection drives the hub at a time (its pose_frame was accepted last). Others are dropped
  // while it is connected and has sent within max_gap_s of hub receipt time.
  const activeWindowMs = metrics.max_gap_s * 1000;
  let activeSourceId = null;
  let activeSourceTime = -Infinity;

  /** The active source's socket id, or null when none is connected and recent. */
  const currentActiveSource = () =>
    activeSourceId !== null && io.of('/ingest').sockets.has(activeSourceId) && now() - activeSourceTime <= activeWindowMs
      ? activeSourceId
      : null;

  app.get('/health', (req, res) => {
    res.json({
      server: 'ok',
      namespaces: {
        ingest: io.of('/ingest').sockets.size,
        audio: io.of('/audio').sockets.size,
        viewer: io.of('/viewer').sockets.size
      },
      activeSource: currentActiveSource(),
      lastAudioAge: lastAudioTime === null ? null : now() - lastAudioTime,
      lastFrameAge: lastFrameTime === null ? null : now() - lastFrameTime
    });
  });

  // Viewers get frame and audio as they arrive; nothing is replayed on connection.
  const viewer = io.of('/viewer');

  // Each valid audio_metrics becomes one viewer audio message, stamped with the hub receipt time.
  const audio = io.of('/audio');
  audio.on('connection', (socket) => {
    console.log('[electron] Client connected to /audio');

    socket.on('audio_metrics', (payload) => {
      const reason = rejectionReason('audio_metrics', payload);
      if (reason) {
        logReject('audio_metrics', reason);
        return;
      }
      lastAudioTime = now();
      viewer.emit('audio', { ...payload, timestamp: lastAudioTime });
    });

    socket.on('error', (err) => reportPacketError('audio_metrics', err));

    socket.on('disconnect', () => {
      console.log('[electron] Client disconnected from /audio');
    });
  });

  // Each valid pose_frame becomes exactly one viewer frame with the derived motion fields.
  const ingest = io.of('/ingest');
  ingest.on('connection', (socket) => {
    console.log('[electron] Client connected to /ingest');

    socket.on('pose_frame', (payload) => {
      const reason = rejectionReason('pose_frame', payload);
      if (reason) {
        logReject('pose_frame', reason);
        return;
      }

      const active = currentActiveSource();
      if (active !== null && active !== socket.id) {
        logReject('inactive-source pose_frame',
          `pose_frame from ${socket.id} ignored: ${active} is the active source; stop it or wait ${metrics.max_gap_s} s`);
        return;
      }
      if (activeSourceId !== socket.id) {
        // Two source clocks must never mix, so a new source starts from a fresh motion state.
        activeSourceId = socket.id;
        motion = createMotionTracker(metrics);
      }
      activeSourceTime = now();

      const frame = {
        schemaVersion: SCHEMA_VERSION,
        timestamp: now(),
        bodies: motion.track(payload.source_time_s, payload.bodies)
      };
      // ajv rejects non-finite numbers, so this also catches a NaN or infinite derived value.
      if (!validators.frame(frame)) {
        logReject('derived frame', `does not match frame.schema.json: ${describeErrors(validators.frame.errors)}`);
        return;
      }

      lastFrameTime = frame.timestamp;
      viewer.emit('frame', frame);
    });

    socket.on('frame', () => logReject('frame', V1_FRAME_REASON));

    socket.on('error', (err) => reportPacketError('pose_frame', err));

    socket.on('disconnect', () => {
      if (activeSourceId === socket.id) activeSourceId = null;
      console.log('[electron] Client disconnected from /ingest');
    });
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.off('error', reject);
      resolve();
    });
  });
  console.log(`[electron] HTTP server on http://${host}:${server.address().port}`);

  /** Disconnects all clients and stops listening. */
  const close = () => new Promise((resolve) => {
    io.close(() => resolve());
  });

  return { server, io, close };
}

module.exports = { createHub, listWorlds };

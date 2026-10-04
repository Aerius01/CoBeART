// Socket.IO hub shared by the Electron main process and the standalone web server.
// Merges OptiTrack frames (/ingest) with the latest audio metrics (/audio) and broadcasts to /viewer.

const http = require('http');
const express = require('express');
const fs = require('fs');
const path = require('path');
const { Server } = require('socket.io');
const Ajv2020 = require('ajv/dist/2020');

const CONTRACT_DIR = path.join(__dirname, '..', '..', 'contract');
const SCHEMA_FILES = ['audio_metrics.schema.json', 'frame.schema.json'];
const SCHEMA_VERSION = 1;
const REJECT_LOG_INTERVAL_MS = 5000;
const MAX_REASON_CHARS = 300;
// A real audio_metrics message is about 48 KB (simulator, default spectrum); allow about 5x.
const MAX_PAYLOAD_BYTES = 256 * 1024;

/** Compiles the contract schemas; `frame` references `audio_metrics`, so both are registered first. */
function loadValidators() {
  const ajv = new Ajv2020({ allErrors: false, allowUnionTypes: true });
  const schemas = SCHEMA_FILES.map((file) => JSON.parse(fs.readFileSync(path.join(CONTRACT_DIR, file), 'utf8')));
  schemas.forEach((schema) => ajv.addSchema(schema));
  const [audioSchema, frameSchema] = schemas;
  return {
    audio_metrics: ajv.getSchema(audioSchema.$id),
    frame: ajv.getSchema(frameSchema.$id)
  };
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

/**
 * Starts the hub and resolves once it is listening.
 * `audioMaxAgeMs`: audio older than this is omitted from merged frames. `now`: injectable clock (epoch ms).
 * @param {{ port: number, host?: string, publicDir: string, audioMaxAgeMs?: number, now?: () => number }} options
 * @returns {Promise<{ server: import('http').Server, io: import('socket.io').Server, close: () => Promise<void> }>}
 */
async function createHub({ port, host = '127.0.0.1', publicDir, audioMaxAgeMs = 500, now = Date.now }) {
  const app = express();
  const server = http.createServer(app);
  const io = new Server(server, { maxHttpBufferSize: MAX_PAYLOAD_BYTES });

  // Serve the front-end so the BrowserWindow (or a browser) can load index.html from the hub.
  app.use(express.static(publicDir));

  const validators = loadValidators();
  const logReject = createRejectLogger(now);

  /** Returns null when the payload is valid for `kind`, otherwise a reason string. */
  const rejectionReason = (kind, payload) => {
    if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
      return 'payload is not an object';
    }
    if (payload.schemaVersion !== SCHEMA_VERSION) {
      return `schemaVersion mismatch: hub speaks ${SCHEMA_VERSION}, received ${JSON.stringify(payload.schemaVersion)}`;
    }
    const validate = validators[kind];
    if (kind === 'frame' && 'audio' in payload) return 'producers must not send audio';
    if (!validate(payload)) {
      return validate.errors.map((e) => `${e.instancePath || '/'} ${e.message}`).join('; ');
    }
    if (kind === 'frame') {
      const ids = payload.rigidbodies.map((body) => body.ID);
      if (new Set(ids).size !== ids.length) return 'duplicate rigid-body ID';
    }
    return null;
  };

  // Socket.IO raises 'error' and closes the connection for a packet that is not valid JSON (e.g. a NaN literal),
  // before any event handler runs.
  const reportPacketError = (kind, err) => {
    logReject(kind, `undecodable packet (${err.message}); connection dropped by Socket.IO`);
  };

  let lastFrame = null;
  let lastAudioData = null;

  app.get('/health', (req, res) => {
    const audioAge = lastAudioData ? now() - lastAudioData.timestamp : null;
    const frameAge = lastFrame ? now() - lastFrame.timestamp : null;
    res.json({
      server: 'ok',
      namespaces: {
        ingest: io.of('/ingest').sockets.size,
        audio: io.of('/audio').sockets.size,
        viewer: io.of('/viewer').sockets.size
      },
      lastAudioAge: audioAge,
      lastFrameAge: frameAge
    });
  });

  // Dedicated channel for audio metrics; updates background state, does not drive emissions.
  const audio = io.of('/audio');
  audio.on('connection', (socket) => {
    console.log('[electron] Client connected to /audio');

    socket.on('audio_metrics', (audioData) => {
      const reason = rejectionReason('audio_metrics', audioData);
      if (reason) {
        logReject('audio_metrics', reason);
        return;
      }
      lastAudioData = { ...audioData, timestamp: now() };
    });

    socket.on('error', (err) => reportPacketError('audio_metrics', err));

    socket.on('disconnect', () => {
      console.log('[electron] Client disconnected from /audio');
    });
  });

  // Viewers get the last known frame immediately on connection.
  const viewer = io.of('/viewer');
  viewer.on('connection', (socket) => {
    if (lastFrame) socket.emit('frame', lastFrame);
  });

  // OptiTrack frames drive the emission rate; audio is additive.
  const ingest = io.of('/ingest');
  ingest.on('connection', (socket) => {
    console.log('[electron] Client connected to /ingest');

    socket.on('frame', (payload) => {
      const reason = rejectionReason('frame', payload);
      if (reason) {
        logReject('frame', reason);
        return;
      }

      const combinedFrame = { ...payload, timestamp: now() };
      if (lastAudioData && now() - lastAudioData.timestamp <= audioMaxAgeMs) {
        combinedFrame.audio = lastAudioData;
      }

      lastFrame = combinedFrame;
      viewer.emit('frame', combinedFrame);
    });

    socket.on('error', (err) => reportPacketError('frame', err));

    socket.on('disconnect', () => {
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

module.exports = { createHub };

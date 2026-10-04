// Socket.IO hub shared by the Electron main process and the standalone web server.
// Merges OptiTrack frames (/ingest) with the latest audio metrics (/audio) and broadcasts to /viewer.

const http = require('http');
const express = require('express');
const { Server } = require('socket.io');

const REQUIRED_AUDIO_FIELDS = ['rms', 'peak', 'zcr', 'dominant_frequency'];

/**
 * Starts the hub and resolves once it is listening.
 * @param {{ port: number, host?: string, publicDir: string }} options
 * @returns {Promise<{ server: import('http').Server, io: import('socket.io').Server, close: () => Promise<void> }>}
 */
async function createHub({ port, host = '127.0.0.1', publicDir }) {
  const app = express();
  const server = http.createServer(app);
  const io = new Server(server, { cors: { origin: '*' } });

  // Serve the front-end so the BrowserWindow (or a browser) can load index.html from the hub.
  app.use(express.static(publicDir));

  let lastFrame = null;
  let lastAudioData = null;

  app.get('/health', (req, res) => {
    const audioAge = lastAudioData ? Date.now() - lastAudioData.timestamp : null;
    const frameAge = lastFrame ? Date.now() - lastFrame.timestamp : null;
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
      if (!audioData || typeof audioData !== 'object') return;

      const hasAllFields = REQUIRED_AUDIO_FIELDS.every((field) =>
        typeof audioData[field] === 'number' && !isNaN(audioData[field])
      );

      if (!hasAllFields) {
        console.warn('[electron] Invalid audio_metrics payload:', audioData);
        return;
      }

      try {
        lastAudioData = { ...audioData, timestamp: Date.now() };
      } catch (err) {
        console.error('[electron] Error processing audio_metrics:', err);
      }
    });

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
      if (!payload || typeof payload !== 'object') return;

      const combinedFrame = { ...payload, timestamp: Date.now() };
      if (lastAudioData) {
        combinedFrame.audio = lastAudioData;
      }

      lastFrame = combinedFrame;
      viewer.emit('frame', combinedFrame);
    });

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

// Standalone web hub (no Electron window). Same pipeline as electron/main.js.
const path = require('path');
const { createHub } = require('./electron/hub');
const { loadConfig, resolvePort, frontendConfig } = require('./electron/config');

const config = loadConfig();

createHub({
  port: resolvePort(config),
  host: config.network.socketio.host,
  publicDir: path.join(__dirname, 'public'),
  audioMaxAgeMs: config.hub.audio_max_age_ms,
  maxBodies: config.tracking.max_num_objects,
  frontendConfig: frontendConfig(config)
}).catch((err) => {
  console.error('Startup failed:', err);
  process.exit(1);
});

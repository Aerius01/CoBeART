// Standalone web hub (no Electron window). Same pipeline as electron/main.js.
const path = require('path');
const { createHub } = require('./electron/hub');
const { loadConfig, resolvePort, hubConfig, frontendConfig } = require('./electron/config');

const config = loadConfig();

createHub({
  port: resolvePort(config),
  host: config.network.socketio.host,
  publicDir: path.join(__dirname, 'public'),
  metrics: hubConfig(config).metrics,
  frontendConfig: frontendConfig(config)
}).catch((err) => {
  console.error('Startup failed:', err);
  process.exit(1);
});

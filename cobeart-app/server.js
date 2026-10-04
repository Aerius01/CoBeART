// Standalone web hub (no Electron window). Same pipeline as electron/main.js.
const path = require('path');
const { createHub } = require('./electron/hub');

const port = Number(process.env.PORT ?? 3000);

createHub({ port, host: '127.0.0.1', publicDir: path.join(__dirname, 'public') });

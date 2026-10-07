const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { loadConfig, resolvePort, frontendConfig } = require('../electron/config');

const REAL_CONFIG_PATH = path.join(__dirname, '..', '..', 'config', 'cobeart.yaml');
const REAL_CONFIG = loadConfig({ env: {} });

/** Writes `text` to a temp YAML file and returns its path. */
const writeYaml = (text) => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'cobeart-config-')), 'cobeart.yaml');
  fs.writeFileSync(file, text);
  return file;
};

test('loads and validates the real config/cobeart.yaml', () => {
  assert.deepEqual(REAL_CONFIG.arena.x, [-3000, 3000]);
  assert.equal(REAL_CONFIG.network.socketio.host, '127.0.0.1');
});

test('COBEART_CONFIG selects the config file and the arena reaches the frontend subset', () => {
  const original = fs.readFileSync(REAL_CONFIG_PATH, 'utf8');
  const file = writeYaml(original.replace('x: [-3000, 3000]', 'x: [-1000, 2000]'));

  const config = loadConfig({ env: { COBEART_CONFIG: file } });

  assert.deepEqual(config.arena.x, [-1000, 2000]);
  assert.deepEqual(frontendConfig(config).arena.x, [-1000, 2000]);
});

test('an invalid config raises an error naming the file and the problem', () => {
  const file = writeYaml('debug: false\n');

  assert.throws(
    () => loadConfig({ env: { COBEART_CONFIG: file } }),
    (err) => err.message.includes(file) && err.message.includes('required')
  );
});

test('PORT overrides the configured port and must be a valid port', () => {
  assert.equal(resolvePort(REAL_CONFIG, {}), REAL_CONFIG.network.socketio.port);
  assert.equal(resolvePort(REAL_CONFIG, { PORT: '3302' }), 3302);
  assert.throws(() => resolvePort(REAL_CONFIG, { PORT: 'abc' }), /PORT must be an integer/);
});

test('the frontend config carries exactly what pages read', () => {
  assert.deepEqual(
    Object.keys(frontendConfig(REAL_CONFIG)).sort(),
    ['arena', 'bridge_framerate', 'composite', 'debug', 'splat_color']
  );
});

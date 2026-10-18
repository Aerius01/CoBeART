const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const yaml = require('js-yaml');
const { loadConfig, resolvePort, trackingRoles, hubConfig, frontendConfig } = require('../electron/config');

const REAL_CONFIG_PATH = path.join(__dirname, '..', '..', 'config', 'cobeart.yaml');
const REAL_CONFIG = loadConfig({ env: {} });

/** Writes `text` to a temp YAML file and returns its path. */
const writeYaml = (text) => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'cobeart-config-')), 'cobeart.yaml');
  fs.writeFileSync(file, text);
  return file;
};

/** Loads a copy of the real config after `mutate` changed its parsed YAML. */
const loadMutated = (mutate) => {
  const raw = yaml.load(fs.readFileSync(REAL_CONFIG_PATH, 'utf8'));
  mutate(raw);
  return loadConfig({ env: { COBEART_CONFIG: writeYaml(yaml.dump(raw)) } });
};

/** Asserts that loading the mutated config fails with a message containing every one of `parts`. */
const assertRejected = (mutate, ...parts) => assert.throws(
  () => loadMutated(mutate),
  (err) => parts.every((part) => err.message.includes(part)) || assert.fail(`unexpected message: ${err.message}`)
);

test('loads and validates the real config/cobeart.yaml', () => {
  assert.deepEqual(REAL_CONFIG.arena.x, [-3000, 3000]);
  assert.equal(REAL_CONFIG.network.socketio.host, '127.0.0.1');
  assert.equal(REAL_CONFIG.metrics.max_gap_s, 0.25);
  assert.equal(REAL_CONFIG.frontend.audio_max_age_ms, 500);
  assert.deepEqual(REAL_CONFIG.sources.mouse, { role: 'right_hand', z_initial_mm: 1200, z_step_mm: 50, rate_hz: 60 });
});

test('trackingRoles maps numeric Motive IDs to roles (js-yaml gives the keys as strings)', () => {
  assert.deepEqual(trackingRoles(REAL_CONFIG), new Map([
    [0, 'left_hand'], [1, 'right_hand'], [2, 'head'], [3, 'chest_front'],
    [4, 'chest_back'], [5, 'left_foot'], [6, 'right_foot'], [7, 'stick']
  ]));
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
  const frontend = frontendConfig(REAL_CONFIG);
  assert.deepEqual(Object.keys(frontend).sort(), ['arena', 'audio_max_age_ms', 'bridge_framerate', 'debug', 'sources']);
  assert.equal(frontend.audio_max_age_ms, 500);
  assert.equal(frontend.sources.mouse.role, 'right_hand');
});

test('the hub config carries the velocity-stage metrics', () => {
  assert.deepEqual(hubConfig(REAL_CONFIG), { metrics: { max_vel: 13000, history_window: 15, max_gap_s: 0.25 } });
});

test('an unknown role in tracking.roles is rejected, naming the key', () => {
  assertRejected((raw) => { raw.tracking.roles[1] = 'right_elbow'; }, '/tracking/roles/1', 'right_hand');
});

test('an unknown sources.mouse.role is rejected, naming the key', () => {
  assertRejected((raw) => { raw.sources.mouse.role = 'mouse'; }, '/sources/mouse/role', 'allowed values');
});

test('a tracking.roles key that is not a non-negative integer is rejected', () => {
  assertRejected((raw) => { raw.tracking.roles['-1'] = 'stick'; delete raw.tracking.roles[7]; }, '/tracking/roles', "'-1'");
});

test('a role mapped from two IDs is rejected', () => {
  assertRejected((raw) => { raw.tracking.roles[8] = 'stick'; }, '/tracking/roles/8', "'stick'", 'ID 7');
});

test('a mouse z start outside arena.z is rejected', () => {
  assertRejected((raw) => { raw.sources.mouse.z_initial_mm = 3000; }, '/sources/mouse/z_initial_mm', 'arena.z');
});

test('removed keys are rejected as unknown, naming the key', () => {
  assertRejected((raw) => { raw.tracking.max_num_objects = 10; }, '/tracking', "'max_num_objects'");
  assertRejected((raw) => { raw.frontend.splat_color = [1, 0.6, 0.2]; }, '/frontend', "'splat_color'");
  assertRejected((raw) => { raw.hub = { audio_max_age_ms: 500 }; }, "'hub'");
});

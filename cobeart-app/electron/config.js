// Loads and validates the shared config/cobeart.yaml and derives the subsets the hub and the frontend receive.

const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const Ajv2020 = require('ajv/dist/2020');

const REPO_ROOT = path.join(__dirname, '..', '..');
const DEFAULT_CONFIG_PATH = path.join(REPO_ROOT, 'config', 'cobeart.yaml');
const SCHEMA_PATH = path.join(REPO_ROOT, 'config', 'cobeart.schema.json');
// The config schema takes its role enum from the contract ($defs/role), so the contract schema is registered first.
const FRAME_SCHEMA_PATH = path.join(REPO_ROOT, 'contract', 'frame.schema.json');

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

/** One ajv error as "<instance path> <message>", plus the offending key or the allowed values when ajv names them. */
function describeError({ instancePath, message, params }) {
  const detail = params.additionalProperty !== undefined ? ` '${params.additionalProperty}'`
    : params.propertyName !== undefined ? ` '${params.propertyName}'`
      : params.allowedValues !== undefined ? ` (${params.allowedValues.join(', ')})`
        : '';
  return `${instancePath || '/'} ${message}${detail}`;
}

/** Problems the schema cannot express: each role at most once in tracking.roles, mouse z start inside arena.z. */
function crossFieldProblems(config) {
  const problems = [];
  const seen = new Map();
  for (const [id, role] of Object.entries(config.tracking.roles)) {
    if (seen.has(role)) problems.push(`/tracking/roles/${id} role '${role}' is already mapped from ID ${seen.get(role)}`);
    else seen.set(role, id);
  }
  const [zMin, zMax] = config.arena.z;
  const zInitial = config.sources.mouse.z_initial_mm;
  if (zInitial < zMin || zInitial > zMax) {
    problems.push(`/sources/mouse/z_initial_mm ${zInitial} must lie inside arena.z [${zMin}, ${zMax}]`);
  }
  return problems;
}

/**
 * Reads the YAML at `configPath` (default: COBEART_CONFIG, else config/cobeart.yaml) and validates it against the
 * schema and the cross-field rules. Returns the parsed YAML unchanged; note that js-yaml gives the integer keys of
 * `tracking.roles` as strings, so read that mapping through `trackingRoles(config)`.
 * @param {{ configPath?: string, env?: NodeJS.ProcessEnv }} options
 */
function loadConfig({ env = process.env, configPath = env.COBEART_CONFIG ?? DEFAULT_CONFIG_PATH } = {}) {
  const config = yaml.load(fs.readFileSync(configPath, 'utf8'));
  const ajv = new Ajv2020({ allErrors: true, allowUnionTypes: true });
  ajv.addSchema(readJson(FRAME_SCHEMA_PATH));
  const validate = ajv.compile(readJson(SCHEMA_PATH));
  const problems = validate(config) ? crossFieldProblems(config) : validate.errors.map(describeError);
  if (problems.length > 0) throw new Error(`Invalid config ${configPath}: ${problems.join('; ')}`);
  return config;
}

/** Hub TCP port: the PORT environment variable (so parallel hubs never collide) overrides the config. */
function resolvePort(config, env = process.env) {
  if (env.PORT === undefined) return config.network.socketio.port;
  const port = Number(env.PORT);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error(`PORT must be an integer in 0..65535, got ${JSON.stringify(env.PORT)}`);
  }
  return port;
}

/**
 * `tracking.roles` as a Map from Motive streaming ID (number) to contract role (string).
 * @returns {Map<number, string>}
 */
function trackingRoles(config) {
  return new Map(Object.entries(config.tracking.roles).map(([id, role]) => [Number(id), role]));
}

/**
 * The hub-side values (the velocity stage).
 * @returns {{ metrics: { max_vel: number, history_window: number, max_gap_s: number } }}
 *   `max_vel`: mm/s mapped to norm_abs_vel = 1; `history_window`: samples in the norm_abs_vel hold window;
 *   `max_gap_s`: seconds of source time after which a role's motion state restarts.
 */
function hubConfig(config) {
  const { max_vel, history_window, max_gap_s } = config.metrics;
  return { metrics: { max_vel, history_window, max_gap_s } };
}

/**
 * The part of the config exposed to pages as window.__COBEART_CONFIG__ (and /config.json).
 * @returns {{ debug: boolean, arena: { x: [number, number], y: [number, number], z: [number, number] },
 *   bridge_framerate: number, audio_max_age_ms: number,
 *   sources: { mouse: { role: string, z_initial_mm: number, z_step_mm: number, rate_hz: number } } }}
 */
function frontendConfig(config) {
  return {
    debug: config.debug,
    arena: config.arena,
    bridge_framerate: config.frontend.bridge_framerate,
    audio_max_age_ms: config.frontend.audio_max_age_ms,
    sources: config.sources
  };
}

module.exports = { loadConfig, resolvePort, trackingRoles, hubConfig, frontendConfig };

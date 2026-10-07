// Loads and validates the shared config/cobeart.yaml and derives the subset the frontend receives.

const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const Ajv2020 = require('ajv/dist/2020');

const REPO_ROOT = path.join(__dirname, '..', '..');
const DEFAULT_CONFIG_PATH = path.join(REPO_ROOT, 'config', 'cobeart.yaml');
const SCHEMA_PATH = path.join(REPO_ROOT, 'config', 'cobeart.schema.json');

/**
 * Reads the YAML at `configPath` (default: COBEART_CONFIG, else config/cobeart.yaml) and validates it against the schema.
 * @param {{ configPath?: string, env?: NodeJS.ProcessEnv }} options
 */
function loadConfig({ env = process.env, configPath = env.COBEART_CONFIG ?? DEFAULT_CONFIG_PATH } = {}) {
  const config = yaml.load(fs.readFileSync(configPath, 'utf8'));
  const validate = new Ajv2020({ allErrors: true, allowUnionTypes: true })
    .compile(JSON.parse(fs.readFileSync(SCHEMA_PATH, 'utf8')));
  if (!validate(config)) {
    const problems = validate.errors.map((e) => `${e.instancePath || '/'} ${e.message}`).join('; ');
    throw new Error(`Invalid config ${configPath}: ${problems}`);
  }
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

/** The part of the config exposed to pages as window.__COBEART_CONFIG__. */
function frontendConfig(config) {
  return {
    debug: config.debug,
    arena: config.arena,
    bridge_framerate: config.frontend.bridge_framerate,
    splat_color: config.frontend.splat_color,
    composite: config.frontend.composite
  };
}

module.exports = { loadConfig, resolvePort, frontendConfig };

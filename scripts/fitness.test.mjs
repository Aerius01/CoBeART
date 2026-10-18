import assert from 'node:assert/strict';
import test from 'node:test';
import { hubOnlyServer, moduleBoundaries, noArenaPortLiterals, noPrintHotPath, RULES, worldsReceiveOnly } from './fitness.mjs';

const ids = (violations) => violations.map((v) => v.rule);

test('hub-only-server flags new Server( outside hub.js', () => {
  const v = hubOnlyServer('cobeart-app/electron/main.js', 'a\nconst io = new Server(http);\n');
  assert.deepEqual(ids(v), ['hub-only-server']);
  assert.equal(v[0].line, 2);
});

test('hub-only-server allows hub.js and unrelated code', () => {
  assert.deepEqual(hubOnlyServer('cobeart-app/electron/hub.js', 'new Server(server, {})'), []);
  assert.deepEqual(hubOnlyServer('cobeart-app/server.js', 'const hub = createHub();'), []);
});

test('no-arena-port-literals flags a bare 3000', () => {
  const v = noArenaPortLiterals('cobeart-app/public/fluid/script.js', 'x\nconst arena = 3000;\n');
  assert.deepEqual(ids(v), ['no-arena-port-literals']);
  assert.equal(v[0].line, 2);
  assert.equal(noArenaPortLiterals('cobeart/foo.py', 'PORT = 3000').length, 1);
});

test('no-arena-port-literals allows the headDepth default', () => {
  const line = '            const headDepth = headBefore.length === 3 ? headBefore[2] : 3000;';
  assert.deepEqual(noArenaPortLiterals('cobeart-app/public/fluid/script.js', line), []);
});

test('no-arena-port-literals allows the docstring URLs', () => {
  const cfg = '    """Return `value` if it is an http(s) URL with a host, e.g. http://127.0.0.1:3000, else raise ConfigError."""';
  const cli = '    """argparse type: an http(s) URL with a host, e.g. http://127.0.0.1:3000."""';
  assert.deepEqual(noArenaPortLiterals('cobeart/settings/config.py', cfg), []);
  assert.deepEqual(noArenaPortLiterals('cobeart/input/optitrack/start_client.py', cli), []);
});

test('no-arena-port-literals still flags other 3000s in the allowlisted files', () => {
  assert.equal(noArenaPortLiterals('cobeart-app/public/fluid/script.js', 'const halfX = 3000;').length, 1);
  assert.equal(noArenaPortLiterals('cobeart/settings/config.py', 'DEFAULT_PORT = 3000').length, 1);
  assert.equal(noArenaPortLiterals('cobeart/input/optitrack/start_client.py', 'url = "http://127.0.0.1:3000"').length, 1);
});

test('no-arena-port-literals is clean without 3000', () => {
  assert.deepEqual(noArenaPortLiterals('cobeart/foo.py', 'x = 2700\ny = 4000'), []);
});

test('no-print-hot-path flags print( in sender, metrics and transform', () => {
  const v = noPrintHotPath('cobeart/input/optitrack/sender.py', 'x = 1\n    print("x")\n');
  assert.deepEqual(ids(v), ['no-print-hot-path']);
  assert.equal(v[0].line, 2);
  assert.equal(noPrintHotPath('cobeart/input/optitrack/transform.py', 'print(1)').length, 1);
});

test('no-print-hot-path is clean for logging, other files and pprint', () => {
  assert.deepEqual(noPrintHotPath('cobeart/input/optitrack/sender.py', 'logger.info("x")\npprint(x)'), []);
  assert.deepEqual(noPrintHotPath('cobeart/audio/capture.py', 'print("meter")'), []);
});

test('RULES scope: tests directories are excluded from port and print rules', () => {
  const port = RULES.find((r) => r.check === noArenaPortLiterals);
  const print = RULES.find((r) => r.check === noPrintHotPath);
  assert.equal(port.applies('cobeart-app/test/config.test.js'), false);
  assert.equal(port.applies('cobeart/audio/tests/test_x.py'), false);
  assert.equal(port.applies('cobeart-app/electron/hub.js'), true);
  assert.equal(print.applies('cobeart/input/optitrack/tests/test_sender.py'), false);
  assert.equal(print.applies('cobeart/input/optitrack/sender.py'), true);
});

test('no-print-hot-path flags metrics.py and skips other optitrack files', () => {
  assert.equal(noPrintHotPath('cobeart/input/optitrack/metrics.py', 'print(1)').length, 1);
  assert.deepEqual(noPrintHotPath('cobeart/input/optitrack/start_client.py', 'print(1)'), []);
});

test('module-boundaries flags audio importing input and input importing audio', () => {
  const a = moduleBoundaries('cobeart/audio/capture.py', 'import os\nfrom cobeart.input.optitrack.sender import X\n');
  assert.deepEqual(ids(a), ['module-boundaries']);
  assert.equal(a[0].line, 2);
  assert.equal(moduleBoundaries('cobeart/audio/x.py', 'import cobeart.input.optitrack').length, 1);
  assert.equal(moduleBoundaries('cobeart/input/optitrack/x.py', '    from cobeart.audio import capture').length, 1);
  assert.equal(moduleBoundaries('cobeart/input/optitrack/x.py', 'import cobeart.audio.capture as c').length, 1);
});

test('module-boundaries flags the simulator importing either producer', () => {
  assert.equal(moduleBoundaries('cobeart/simulator/x.py', 'from cobeart.input.optitrack import sender').length, 1);
  assert.equal(moduleBoundaries('cobeart/simulator/x.py', 'import cobeart.audio.capture').length, 1);
});

test('module-boundaries allows common, settings, own-module imports and comments', () => {
  assert.deepEqual(moduleBoundaries('cobeart/audio/emitter.py', 'from cobeart.common.strict_json import StrictJson'), []);
  assert.deepEqual(moduleBoundaries('cobeart/audio/capture.py', 'from cobeart.audio.utils import x\nfrom cobeart.settings.config import y'), []);
  assert.deepEqual(moduleBoundaries('cobeart/input/optitrack/x.py', 'from cobeart.input.optitrack.sender import X'), []);
  assert.deepEqual(moduleBoundaries('cobeart/simulator/x.py', 'from cobeart.settings.config import y'), []);
  assert.deepEqual(moduleBoundaries('cobeart/audio/x.py', '# from cobeart.input import y\nx = "import cobeart.input"'), []);
  assert.deepEqual(moduleBoundaries('cobeart/settings/config.py', 'from cobeart.input import y'), []);
});

test('worlds-receive-only flags sockets, io( and the hub namespaces in a world', () => {
  const file = 'cobeart-app/public/worlds/splat/script.js';
  const a = worldsReceiveOnly(file, 'x\nconst s = io("/viewer");\n');
  assert.deepEqual(ids(a), ['worlds-receive-only']);
  assert.equal(a[0].line, 2);
  assert.equal(worldsReceiveOnly(file, 'import "socket.io-client";').length, 1);
  assert.equal(worldsReceiveOnly(file, 'fetch("/ingest")').length, 1);
  assert.equal(worldsReceiveOnly('cobeart-app/public/worlds/other/index.html', '<script src="/socket.io/socket.io.js">').length, 1);
});

test('worlds-receive-only allows postMessage listeners and files outside worlds', () => {
  const file = 'cobeart-app/public/worlds/splat/script.js';
  assert.deepEqual(worldsReceiveOnly(file, "window.addEventListener('message', (e) => handle(e.data));\nconst r = radio(1) + gl.drawio(2);"), []);
  assert.deepEqual(worldsReceiveOnly('cobeart-app/public/host/bridge.js', 'const s = io("/viewer");'), []);
  const rule = RULES.find((r) => r.check === worldsReceiveOnly);
  assert.equal(rule.applies('cobeart-app/public/worlds/splat/script.js'), true);
  assert.equal(rule.applies('cobeart-app/public/host/bridge.js'), false);
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { hubOnlyServer, noArenaPortLiterals, noPrintHotPath, RULES } from './fitness.mjs';

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
  assert.deepEqual(noArenaPortLiterals('cobeart/optitrackclient/start_client.py', cli), []);
});

test('no-arena-port-literals still flags other 3000s in the allowlisted files', () => {
  assert.equal(noArenaPortLiterals('cobeart-app/public/fluid/script.js', 'const halfX = 3000;').length, 1);
  assert.equal(noArenaPortLiterals('cobeart/settings/config.py', 'DEFAULT_PORT = 3000').length, 1);
  assert.equal(noArenaPortLiterals('cobeart/optitrackclient/start_client.py', 'url = "http://127.0.0.1:3000"').length, 1);
});

test('no-arena-port-literals is clean without 3000', () => {
  assert.deepEqual(noArenaPortLiterals('cobeart/foo.py', 'x = 2700\ny = 4000'), []);
});

test('no-print-hot-path flags print( in packagesender and transform.py', () => {
  const v = noPrintHotPath('cobeart/packagesender/sender.py', 'x = 1\n    print("x")\n');
  assert.deepEqual(ids(v), ['no-print-hot-path']);
  assert.equal(v[0].line, 2);
  assert.equal(noPrintHotPath('cobeart/optitrackclient/transform.py', 'print(1)').length, 1);
});

test('no-print-hot-path is clean for logging, other files and pprint', () => {
  assert.deepEqual(noPrintHotPath('cobeart/packagesender/sender.py', 'logger.info("x")\npprint(x)'), []);
  assert.deepEqual(noPrintHotPath('cobeart/audiocapture/capture.py', 'print("meter")'), []);
});

test('RULES scope: tests directories are excluded from port and print rules', () => {
  const port = RULES.find((r) => r.check === noArenaPortLiterals);
  const print = RULES.find((r) => r.check === noPrintHotPath);
  assert.equal(port.applies('cobeart-app/test/config.test.js'), false);
  assert.equal(port.applies('cobeart/audiocapture/tests/test_x.py'), false);
  assert.equal(port.applies('cobeart-app/electron/hub.js'), true);
  assert.equal(print.applies('cobeart/packagesender/tests/test_sender.py'), false);
  assert.equal(print.applies('cobeart/packagesender/sender.py'), true);
});

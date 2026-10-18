const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHub } = require('../electron/hub');

const METRICS = { max_vel: 13000.0, history_window: 3, max_gap_s: 0.25 };
const INDEX_HTML = '<html><head></head></html>';

/** Creates a temp public dir with the given world folders ({ name: hasIndex }). */
const makePublicDir = (worlds) => {
  const publicDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cobeart-worlds-'));
  for (const [name, hasIndex] of Object.entries(worlds)) {
    fs.mkdirSync(path.join(publicDir, 'worlds', name), { recursive: true });
    if (hasIndex) fs.writeFileSync(path.join(publicDir, 'worlds', name, 'index.html'), INDEX_HTML);
  }
  return publicDir;
};

const startHub = (publicDir) => createHub({
  port: 0, host: '127.0.0.1', publicDir, metrics: METRICS, frontendConfig: { debug: false }
});

test('/worlds.json lists only world folders with an index.html, sorted, computed per request', async () => {
  const publicDir = makePublicDir({ zeta: true, alpha: true, empty: false });
  const hub = await startHub(publicDir);
  try {
    const url = `http://127.0.0.1:${hub.server.address().port}/worlds.json`;
    assert.deepEqual(await (await fetch(url)).json(), { worlds: [{ name: 'alpha' }, { name: 'zeta' }] });

    fs.writeFileSync(path.join(publicDir, 'worlds', 'empty', 'index.html'), INDEX_HTML);
    const second = await (await fetch(url)).json();
    assert.deepEqual(second.worlds.map((world) => world.name), ['alpha', 'empty', 'zeta']);
  } finally {
    await hub.close();
    fs.rmSync(publicDir, { recursive: true, force: true });
  }
});

test('/worlds.json is not shadowed by a static file of the same name', async () => {
  const publicDir = makePublicDir({ only: true });
  fs.writeFileSync(path.join(publicDir, 'worlds.json'), '{"shadow":true}');
  const hub = await startHub(publicDir);
  try {
    const body = await (await fetch(`http://127.0.0.1:${hub.server.address().port}/worlds.json`)).json();
    assert.deepEqual(body, { worlds: [{ name: 'only' }] });
  } finally {
    await hub.close();
    fs.rmSync(publicDir, { recursive: true, force: true });
  }
});

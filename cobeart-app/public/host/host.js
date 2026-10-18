// Host page setup: picks the world from ?world=<name> (validated against /worlds.json) and the input source
// from ?source=<live|mouse>. Both are required: no silent default. Errors are shown on the page and no world loads.
(async function () {
  'use strict';

  const SOURCES = ['live', 'mouse'];
  const params = new URLSearchParams(location.search);
  const frameEl = document.getElementById('worldFrame');
  const errorEl = document.getElementById('hostError');

  function fail(message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
    console.error(`[host] ${message}`);
  }

  const response = await fetch('/worlds.json');
  if (!response.ok) return fail(`Cannot load /worlds.json: HTTP ${response.status}`);
  const worlds = (await response.json()).worlds.map((w) => w.name);
  const validWorlds = `Valid worlds: ${worlds.join(', ') || '(none)'}.`;

  const world = params.get('world');
  if (world === null) return fail(`Missing "world" in the URL. ${validWorlds}`);
  if (!worlds.includes(world)) return fail(`Unknown world "${world}". ${validWorlds}`);

  const source = params.get('source');
  const validSources = `Valid sources: ${SOURCES.join(', ')}.`;
  if (source === null) return fail(`Missing "source" in the URL. ${validSources}`);
  if (!SOURCES.includes(source)) return fail(`Unknown source "${source}". ${validSources}`);

  const debugSuffix = params.has('debug') ? '?debug' : '';
  frameEl.src = `/worlds/${encodeURIComponent(world)}/index.html${debugSuffix}`;

  if (source === 'mouse') {
    const script = document.createElement('script');
    script.type = 'module';
    script.src = '/sources/mouse.js';
    document.body.appendChild(script);
  }
})().catch((err) => {
  const errorEl = document.getElementById('hostError');
  errorEl.textContent = `Host setup failed: ${err.message}`;
  errorEl.hidden = false;
  console.error('[host] setup failed', err);
});

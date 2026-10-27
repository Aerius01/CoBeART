// Glade world wiring (BRIEF.md "Runtime"): frame messages, the fixed-step loop, rendering, the panel and keys.
// Simulation and rendering live in sim/ and render/; this file only connects them.
import { defaultParams } from './params.mjs';
import { resetState, resolveFoot, step } from './sim/step.mjs';
import { generateAtlas, maxCellPx } from './render/atlas.mjs';
import { createRenderer } from './render/renderer.mjs';
import { createPanel } from './panel.mjs';

/** Desired atlas cell size in px, lowered for frame counts whose atlas would not fit (maxCellPx). */
const DESIRED_CELL_PX = 128;
/** Shortest interval between two dropped-time warnings, in ms. */
const DROP_WARN_INTERVAL_MS = 5000;

/** @param {unknown} err */
function showError(err) {
  console.error(err);
  const box = /** @type {HTMLElement} */ (document.getElementById('error'));
  box.hidden = false;
  box.textContent = String(/** @type {Error} */ (err)?.stack ?? err);
}

/** @param {number} nFrames */
const atlasFor = (nFrames) => generateAtlas({ nFrames, cellPx: Math.min(DESIRED_CELL_PX, maxCellPx(nFrames)) });

/** @param {EventTarget | null} target */
const isFormField = (target) => target instanceof Element && target.closest('input, select, textarea') !== null;

function start() {
  const arena = window.__COBEART_CONFIG__?.arena;
  if (!arena) throw new Error('window.__COBEART_CONFIG__.arena is missing; open Glade through the hub (npm run web)');

  /** @type {Readonly<import('./params.mjs').GladeParams>} */
  let params = defaultParams();
  let sim = resetState(params, arena);
  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById('view'));
  const renderer = createRenderer(canvas, arena, atlasFor(params.N_frames));
  new ResizeObserver(() => renderer.resize()).observe(canvas);
  console.info(`glade: seed ${params.seed}`);

  /** @type {import('./sim/step.mjs').Sighting} */
  let sighting = null;
  let heatmap = false;
  let loggedCapacity = sim.a.capacity;
  let lastMs = /** @type {number | null} */ (null);
  let owedS = 0;
  let droppedMs = 0;
  let lastWarnMs = -Infinity;

  /**
   * Commits `nextParams` with a fresh glade; owed time and the capacity baseline restart with it.
   * @param {Readonly<import('./params.mjs').GladeParams>} nextParams
   * @param {import('./sim/step.mjs').SimBuffers} nextSim built from nextParams before anything is committed
   */
  const commitReset = (nextParams, nextSim = resetState(nextParams, arena)) => {
    params = nextParams;
    sim = nextSim;
    owedS = 0;
    loggedCapacity = sim.a.capacity;
    console.info(`glade: reset, seed ${params.seed}`);
  };

  window.addEventListener('message', (e) => {
    if (e.data?.type !== 'frame') return;
    // A frame without the role means the foot is not tracked now (BRIEF.md "Fit with the system").
    const body = e.data.frame?.bodies?.[params.foot_role];
    sighting = body ? { x: body.x, y: body.y, z: body.z, receivedMs: performance.now() } : null;
  });

  const panel = createPanel({
    arena,
    getParams: () => params,
    apply(next, key) {
      if (key === 'N_frames') {
        // One synchronous handler: everything that can throw runs before anything is committed.
        const nextSim = resetState(next, arena);
        renderer.setAtlas(atlasFor(next.N_frames));
        commitReset(next, nextSim);
        return;
      }
      if (key === 'h') {
        commitReset(next);
        return;
      }
      if (key === 'foot_role') sighting = null;
      params = next;
    },
  });

  /** @param {string} json */
  const copyToClipboard = (json) => {
    if (!navigator.clipboard) {
      console.error('glade: D could not copy to the clipboard: navigator.clipboard is unavailable on this page');
      return;
    }
    navigator.clipboard.writeText(json).catch((err) => {
      console.error(`glade: D could not copy to the clipboard (${err?.name ?? 'Error'}: ${err?.message ?? err}); ` +
        'copy the JSON from the line above');
    });
  };

  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || isFormField(e.target)) return;
    switch (e.key.toLowerCase()) {
      case 'e': heatmap = !heatmap; break;
      case 't': panel.show(!panel.visible()); break;
      case 'r': commitReset(params); break;
      case 'd': {
        const json = JSON.stringify(params, null, 2);
        console.log(`glade: parameters\n${json}`);
        copyToClipboard(json);
        break;
      }
      default: break;
    }
  });

  /** @param {import('./sim/field.mjs').Foot} foot */
  const stepOnce = (foot) => {
    step(sim.a, sim.b, foot, params, sim.scratch);
    const t = sim.a;
    sim.a = sim.b;
    sim.b = t;
    if (sim.a.capacity > loggedCapacity) {
      loggedCapacity = sim.a.capacity;
      console.info(`glade: plant capacity grew to ${loggedCapacity}`);
    }
  };

  /** @param {number} nowMs */
  const frame = (nowMs) => {
    try {
      if (lastMs !== null) owedS += (nowMs - lastMs) / 1000;
      lastMs = nowMs;
      const foot = resolveFoot(sighting, nowMs, params);
      for (let n = 0; n < params.max_steps_per_frame && owedS >= params.dt; n++) {
        stepOnce(foot);
        owedS -= params.dt;
      }
      if (owedS >= params.dt) {
        // Owed time beyond the catch-up limit is dropped, so the glade resumes as if no time had passed.
        const whole = Math.floor(owedS / params.dt) * params.dt;
        owedS -= whole;
        droppedMs += whole * 1000;
      }
      if (droppedMs > 0 && nowMs - lastWarnMs >= DROP_WARN_INTERVAL_MS) {
        console.warn(`glade: dropped ${droppedMs.toFixed(0)} ms of simulation time (more than ` +
          `max_steps_per_frame = ${params.max_steps_per_frame} steps owed); find the stall if this repeats`);
        droppedMs = 0;
        lastWarnMs = nowMs;
      }
      renderer.render(sim.a, params, { heatmap });
    } catch (err) {
      showError(err);
      return;
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);

  window.__glade = {
    get params() { return params; },
    get state() { return sim.a; },
    get heatmap() { return heatmap; },
  };
}

try {
  start();
} catch (err) {
  showError(err);
}

// Glade tuning panel: lil-gui built from the PARAMS metadata (BRIEF.md "Runtime", GL grill G33).
// The controls are bound to a candidate copy, since lil-gui writes into its object on every drag tick; a value
// reaches the live params only after validateParams accepts it. Live keys commit on change while valid and snap
// back on finish when invalid; structural keys (they reset the glade) commit on finish only.
import GUI from './vendor/lil-gui.esm.min.js';
import { PARAMS, validateParams } from './params.mjs';

/** Number ranges wider than this get a plain number box instead of a slider (e.g. `seed`). */
const MAX_SLIDER_SPAN = 1e6;

/**
 * @typedef {object} PanelDeps
 * @property {() => Readonly<import('./params.mjs').GladeParams>} getParams the committed params
 * @property {(next: Readonly<import('./params.mjs').GladeParams>, key: string) => void} apply commits `next`
 *   (already validated); throws to reject it, e.g. when a structural rebuild fails
 * @property {import('./params.mjs').Arena} arena
 */

/**
 * @typedef {object} Panel
 * @property {(visible: boolean) => void} show
 * @property {() => boolean} visible
 */

/**
 * Builds the hidden panel; `T` in main.mjs shows it.
 * @param {PanelDeps} deps
 * @returns {Panel}
 */
export function createPanel({ getParams, apply, arena }) {
  const gui = new GUI({ title: 'Glade' });
  const errorBox = document.createElement('div');
  errorBox.style.cssText = 'color:#f88;padding:4px 8px;white-space:pre-wrap;font:11px monospace;';
  errorBox.hidden = true;
  gui.domElement.insertBefore(errorBox, gui.domElement.children[1] ?? null);

  /** @type {Record<string, unknown>} */
  const candidate = { ...getParams() };

  /** @param {unknown} err */
  const showError = (err) => {
    errorBox.textContent = err instanceof Error ? err.message : String(err);
    errorBox.hidden = false;
  };
  const clearError = () => { errorBox.hidden = true; };

  /**
   * Validates the committed params with `key` set to the candidate's value and commits them; null on success.
   * @param {string} key
   * @returns {unknown} the error, or null
   */
  const tryCommit = (key) => {
    try {
      const next = Object.freeze({ ...getParams(), [key]: candidate[key] });
      validateParams(next, arena);
      apply(next, key);
      clearError();
      return null;
    } catch (err) {
      return err;
    }
  };

  /**
   * @param {string} key
   * @param {{ updateDisplay(): unknown }} controller lil-gui controller
   * @param {unknown} err
   */
  const reject = (key, controller, err) => {
    console.error(`glade panel: rejected ${key}: ${err instanceof Error ? err.message : String(err)}`);
    showError(err);
    candidate[key] = getParams()[key];
    controller.updateDisplay();
  };

  for (const spec of PARAMS) {
    const structural = spec.kind === 'structural';
    let controller;
    if (spec.type === 'enum') {
      controller = gui.add(candidate, spec.key, /** @type {string[]} */ ([...(spec.options ?? [])]));
    } else if (/** @type {number} */ (spec.max) - /** @type {number} */ (spec.min) > MAX_SLIDER_SPAN) {
      controller = gui.add(candidate, spec.key).step(/** @type {number} */ (spec.step));
    } else {
      controller = gui.add(candidate, spec.key, spec.min, spec.max, spec.step);
    }
    const unit = spec.unit ? ` (${spec.unit})` : '';
    controller.name(structural ? `${spec.key} ⟳` : spec.key);
    controller.domElement.title = `${spec.description}${unit}${structural ? '; resets the glade' : ''}`;

    const ctl = controller;
    if (!structural) {
      // While dragging or typing, valid values apply at once; invalid ones wait for the finish to be rejected.
      ctl.onChange(() => { tryCommit(spec.key); });
    }
    ctl.onFinishChange(() => {
      if (candidate[spec.key] === getParams()[spec.key]) {
        clearError();
        return;
      }
      const err = tryCommit(spec.key);
      if (err !== null) reject(spec.key, ctl, err);
    });
  }

  let shown = false;
  gui.hide();
  return {
    show(visible) {
      shown = visible;
      gui.show(visible);
    },
    visible() { return shown; },
  };
}

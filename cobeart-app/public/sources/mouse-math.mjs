// Pure maths of the mouse source (D17, D18). No DOM access, so the node tests import it directly.

/**
 * @typedef {{ x: [number, number], y: [number, number], z: [number, number] }} Arena arena bounds in mm, [min, max]
 * @typedef {{ left: number, top: number, width: number, height: number }} LayerRect
 * @typedef {{ x: number, y: number, z: number }} ArenaPoint integer mm
 * @typedef {{ schemaVersion: 2, source_time_s: number, bodies: Record<string, object> }} PoseFrame
 */

export const SCHEMA_VERSION = 2;

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

/**
 * Screen point to arena x, y per D18 (canonical top-down view): the layer's left edge is x_max, its top edge is
 * y_min. Exact inverse of the mapping in worlds/splat/script.js. Points outside the layer clamp to the arena.
 * @param {number} clientX
 * @param {number} clientY
 * @param {LayerRect} rect
 * @param {Arena} arena
 * @returns {{ x: number, y: number }} integer mm
 */
export function screenToArena(clientX, clientY, rect, arena) {
  const [xMin, xMax] = arena.x;
  const [yMin, yMax] = arena.y;
  const normX = clamp((clientX - rect.left) / rect.width, 0, 1);
  const normY = clamp((clientY - rect.top) / rect.height, 0, 1);
  return {
    x: Math.round(xMax - normX * (xMax - xMin)),
    y: Math.round(yMin + normY * (yMax - yMin)),
  };
}

/**
 * Wheel event to notch: one event is one notch, wheel up (negative deltaY) is +1.
 * @param {number} deltaY non-zero
 * @returns {1 | -1}
 */
export function wheelNotch(deltaY) {
  if (!Number.isFinite(deltaY) || deltaY === 0) throw new RangeError(`wheel deltaY must be non-zero, got ${deltaY}`);
  return deltaY < 0 ? 1 : -1;
}

/**
 * New height after `notches` wheel notches, clamped to the arena's z range.
 * @param {number} z current height, mm
 * @param {number} notches signed notch count (up is positive)
 * @param {number} stepMm height change per notch
 * @param {[number, number]} zRange arena.z
 * @returns {number} integer mm
 */
export function applyWheel(z, notches, stepMm, zRange) {
  return Math.round(clamp(z + notches * stepMm, zRange[0], zRange[1]));
}

/**
 * pose_frame for a held button: the configured role with identity orientation.
 * @param {string} role
 * @param {ArenaPoint} point
 * @param {number} sourceTimeS performance.now() / 1000
 * @returns {PoseFrame}
 */
export function buildHeldFrame(role, { x, y, z }, sourceTimeS) {
  return {
    schemaVersion: SCHEMA_VERSION,
    source_time_s: sourceTimeS,
    bodies: { [role]: { x, y, z, qx: 0, qy: 0, qz: 0, qw: 1 } },
  };
}

/**
 * pose_frame for a released button: nothing is tracked.
 * @param {number} sourceTimeS
 * @returns {PoseFrame}
 */
export function buildReleaseFrame(sourceTimeS) {
  return { schemaVersion: SCHEMA_VERSION, source_time_s: sourceTimeS, bodies: {} };
}

/**
 * Placeholder plant atlas for Glade: a top-down rosette that grows with the frame index.
 *
 * Geometry (the GL 3.2 renderer computes UVs from this):
 *  - cols = nFrames, rows = 2. Row 0 is the growing set, row 1 the declining set; frames 0..nFrames-1 run left to right.
 *  - gutterPx = 2 * ceil(cellPx / 16), so the gutter is at least cellPx / 8.
 *  - Slot pitch P = cellPx + gutterPx. The canvas is (cols * P) by (rows * P) pixels.
 *  - The art of (frame, row) lies inside the square of side cellPx whose top-left corner is
 *    (frame * P + gutterPx / 2, row * P + gutterPx / 2). The gutter is fully transparent.
 *  - Row 0 is at the top of the canvas (canvas y grows downwards).
 *  - Pixels use straight (non-premultiplied) alpha.
 */

const MAX_ATLAS_PX = 4096;
const MIN_FRAMES = 2;
const MIN_CELL_PX = 8;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const DECLINE_LENGTH_FACTOR = 0.87;

/** @typedef {{r: number, g: number, b: number}} Rgb */
/** @type {Rgb} */ const GREEN_YOUNG = { r: 120, g: 190, b: 70 };
/** @type {Rgb} */ const GREEN_MATURE = { r: 40, g: 120, b: 45 };
/** @type {Rgb} */ const STRAW = { r: 190, g: 150, b: 80 };
/** @type {Rgb} */ const BROWN = { r: 130, g: 90, b: 45 };

/**
 * @param {Rgb} a
 * @param {Rgb} b
 * @param {number} t
 * @returns {Rgb}
 */
function lerpRgb(a, b, t) {
  return { r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t };
}

/**
 * @param {Rgb} c
 * @param {number} lightness multiplier on each channel
 * @param {number} alpha
 * @returns {string}
 */
function css(c, lightness, alpha) {
  const ch = (/** @type {number} */ v) => Math.max(0, Math.min(255, Math.round(v * lightness)));
  return `rgba(${ch(c.r)},${ch(c.g)},${ch(c.b)},${alpha})`;
}

/**
 * Draws one rosette centred at the origin of ctx.
 * @param {CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D} ctx
 * @param {number} t growth in [0, 1]
 * @param {boolean} declining
 * @param {number} cellPx
 */
function drawRosette(ctx, t, declining, cellPx) {
  const maxR = cellPx / 2 - 1;
  const base = declining
    ? lerpRgb(lerpRgb(GREEN_YOUNG, GREEN_MATURE, t), lerpRgb(STRAW, BROWN, t), 0.72 + 0.25 * (1 - t))
    : lerpRgb(GREEN_YOUNG, GREEN_MATURE, t);

  if (t === 0) {
    ctx.fillStyle = css(base, 1, 0.25);
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(0.75, cellPx / 48), 0, Math.PI * 2);
    ctx.fill();
    return;
  }

  const blades = Math.round(4 + 14 * t);
  const length = maxR * (0.06 + 0.94 * t ** 0.85) * (declining ? DECLINE_LENGTH_FACTOR : 1);
  const halfWidth = Math.max(0.6, length * (0.24 + 0.06 * (1 - t)));
  // Long blades first so the shorter inner ones stay visible on top.
  for (let layer = 0; layer < 2; layer++) {
    const layerBlades = layer === 0 ? Math.ceil(blades / 2) : Math.floor(blades / 2);
    const layerScale = layer === 0 ? 1 : 0.68;
    const lightness = layer === 0 ? 0.85 : 1.1;
    for (let i = 0; i < layerBlades; i++) {
      const angle = (i / layerBlades) * Math.PI * 2 + layer * GOLDEN_ANGLE;
      const len = length * layerScale;
      const w = halfWidth * layerScale;
      ctx.save();
      ctx.rotate(angle);
      const grad = ctx.createLinearGradient(0, 0, len, 0);
      grad.addColorStop(0, css(base, lightness * 0.7, 1));
      grad.addColorStop(1, css(base, lightness, 1));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(len * 0.45, -w * 1.6, len, 0);
      ctx.quadraticCurveTo(len * 0.45, w * 1.6, 0, 0);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }
}

/**
 * @param {number} nFrames
 */
function assertFrames(nFrames) {
  if (!Number.isInteger(nFrames) || nFrames < MIN_FRAMES) {
    throw new Error(`generateAtlas: nFrames must be an integer >= ${MIN_FRAMES}, got ${nFrames}. Pass a whole frame count.`);
  }
}

/**
 * @param {number} cellPx
 * @returns {number}
 */
function gutterFor(cellPx) {
  return 2 * Math.ceil(cellPx / 16);
}

/**
 * Largest integer cellPx whose atlas, nFrames * (cellPx + gutter) wide, fits in 4096 px.
 * @param {number} nFrames integer >= 2
 * @returns {number}
 */
export function maxCellPx(nFrames) {
  assertFrames(nFrames);
  let cellPx = Math.floor(MAX_ATLAS_PX / nFrames);
  while (cellPx >= MIN_CELL_PX && nFrames * (cellPx + gutterFor(cellPx)) > MAX_ATLAS_PX) cellPx--;
  if (cellPx < MIN_CELL_PX) {
    throw new Error(
      `maxCellPx: N_frames=${nFrames} leaves no valid cellPx (>= ${MIN_CELL_PX}) within ${MAX_ATLAS_PX}px. Reduce N_frames.`);
  }
  return cellPx;
}

/**
 * @param {number} width
 * @param {number} height
 * @returns {OffscreenCanvas | HTMLCanvasElement}
 */
function createCanvas(width, height) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

/**
 * Generates the placeholder atlas. See the file header for the cell geometry.
 * @param {{nFrames: number, cellPx: number}} params
 * @returns {{canvas: OffscreenCanvas | HTMLCanvasElement, cols: number, rows: number, cellPx: number, gutterPx: number}}
 */
export function generateAtlas({ nFrames, cellPx }) {
  assertFrames(nFrames);
  if (!Number.isInteger(cellPx) || cellPx < MIN_CELL_PX) {
    throw new Error(`generateAtlas: cellPx must be an integer >= ${MIN_CELL_PX}, got ${cellPx}. Pass a whole pixel size.`);
  }
  const cols = nFrames;
  const rows = 2;
  const gutterPx = gutterFor(cellPx);
  const pitch = cellPx + gutterPx;
  const fit = maxCellPx(nFrames);
  if (cellPx > fit) {
    throw new Error(
      `generateAtlas: atlas width ${cols * pitch}px exceeds ${MAX_ATLAS_PX}px for N_frames=${nFrames}, cellPx=${cellPx}. ` +
      `Use cellPx <= ${fit} or fewer frames.`);
  }

  const canvas = createCanvas(cols * pitch, rows * pitch);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('generateAtlas: could not get a 2D context from the atlas canvas.');

  for (let row = 0; row < rows; row++) {
    for (let frame = 0; frame < nFrames; frame++) {
      const t = frame / (nFrames - 1);
      ctx.save();
      ctx.translate(frame * pitch + gutterPx / 2 + cellPx / 2, row * pitch + gutterPx / 2 + cellPx / 2);
      drawRosette(ctx, t, row === 1, cellPx);
      ctx.restore();
    }
  }
  return { canvas, cols, rows, cellPx, gutterPx };
}

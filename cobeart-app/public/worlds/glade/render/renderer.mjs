// Glade WebGL2 renderer (BRIEF.md "Rendering", GL grill G23 to G27).
// One instanced draw per frame for all plants (halo quad then sprite quad per instance), an optional
// blocky energy heatmap under them, black background, premultiplied alpha throughout.
import {
  HEATMAP_FRAGMENT_SHADER, HEATMAP_VERTEX_SHADER, PLANT_ATTRIB, PLANT_FRAGMENT_SHADER, PLANT_VERTEX_SHADER,
  VERTICES_PER_INSTANCE,
} from './shaders.mjs';

/** Bytes per packed instance: x, y (f32), seed (u32), frame | direction << 16 (u32). */
const INSTANCE_BYTES = 16;
const INSTANCE_WORDS = INSTANCE_BYTES / 4;
const INITIAL_INSTANCES = 1024;
const SPRITE_SETS = 2;
const VERTICES_PER_QUAD = 6;

/** Raised when the browser cannot create a WebGL2 context. */
export class GladeWebGL2Error extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = 'GladeWebGL2Error';
  }
}

/**
 * Atlas as returned by generateAtlas (render/atlas.mjs); straight alpha, cols = frames, rows = sprite sets.
 * @typedef {object} Atlas
 * @property {OffscreenCanvas | HTMLCanvasElement} canvas
 * @property {number} cols frames per sprite set
 * @property {number} rows sprite sets (2: growing, declining)
 * @property {number} cellPx side of one art square in px
 * @property {number} gutterPx transparent gutter around each square in px
 */

/**
 * @typedef {object} RenderOptions
 * @property {boolean} [heatmap] draw the energy grid under the plants
 */

/**
 * @typedef {object} GladeRenderer
 * @property {(state: import('../sim/state.mjs').GladeState, params: import('../params.mjs').GladeParams,
 *   options?: RenderOptions) => void} render draws one frame
 * @property {(atlas: Atlas) => void} setAtlas rebuilds the atlas texture (N_frames may change)
 * @property {() => void} resize matches the drawing buffer to client size x devicePixelRatio
 * @property {() => void} dispose frees GL resources and listeners
 */

/**
 * @param {import('../params.mjs').Arena} arena
 */
function assertArena(arena) {
  for (const axis of /** @type {const} */ (['x', 'y'])) {
    const range = arena?.[axis];
    if (!Array.isArray(range) || range.length !== 2 || !range.every(Number.isFinite) || !(range[1] > range[0])) {
      throw new Error(`createRenderer: arena.${axis} = ${JSON.stringify(range)} must be [min, max] in mm with ` +
        'max > min; pass window.__COBEART_CONFIG__.arena');
    }
  }
}

/**
 * @param {Atlas} atlas
 */
function assertAtlas(atlas) {
  if (!atlas || !atlas.canvas || !Number.isInteger(atlas.cols) || atlas.cols < 2 || atlas.rows !== SPRITE_SETS ||
      !Number.isInteger(atlas.cellPx) || !Number.isInteger(atlas.gutterPx)) {
    throw new Error(`renderer atlas: expected { canvas, cols >= 2, rows = ${SPRITE_SETS}, cellPx, gutterPx } from ` +
      `generateAtlas, got cols ${atlas?.cols}, rows ${atlas?.rows}, cellPx ${atlas?.cellPx}`);
  }
  if (atlas.gutterPx < 0 || atlas.gutterPx % 2 !== 0) {
    throw new Error(`renderer atlas: gutterPx ${atlas.gutterPx} must be even and >= 0 (half on each side of a ` +
      'cell); build the atlas with the render/atlas.mjs geometry');
  }
  const pitch = atlas.cellPx + atlas.gutterPx;
  const width = atlas.cols * pitch;
  const height = atlas.rows * pitch;
  if (atlas.canvas.width !== width || atlas.canvas.height !== height) {
    throw new Error(`renderer atlas: canvas is ${atlas.canvas.width}x${atlas.canvas.height} px but cols ${atlas.cols}, ` +
      `rows ${atlas.rows}, cellPx ${atlas.cellPx} and gutterPx ${atlas.gutterPx} need ${width}x${height} px; ` +
      'fix the atlas metadata or the image so they agree');
  }
}

/**
 * @param {WebGL2RenderingContext} gl
 * @param {number} type
 * @param {string} source
 * @param {string} label
 * @returns {WebGLShader}
 */
function compileShader(gl, type, source, label) {
  const shader = /** @type {WebGLShader} */ (gl.createShader(type));
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`Glade renderer: ${label} shader failed to compile (fix render/shaders.mjs):\n${log}`);
  }
  return shader;
}

/**
 * @param {WebGL2RenderingContext} gl
 * @param {string} vertexSource
 * @param {string} fragmentSource
 * @param {string} label
 * @returns {WebGLProgram}
 */
function linkProgram(gl, vertexSource, fragmentSource, label) {
  const vs = compileShader(gl, gl.VERTEX_SHADER, vertexSource, `${label} vertex`);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource, `${label} fragment`);
  const program = /** @type {WebGLProgram} */ (gl.createProgram());
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(program);
    gl.deleteProgram(program);
    throw new Error(`Glade renderer: ${label} program failed to link (fix render/shaders.mjs):\n${log}`);
  }
  return program;
}

/**
 * Uniform locations of `program` by name; throws when a name is not an active uniform.
 * @template {string} K
 * @param {WebGL2RenderingContext} gl
 * @param {WebGLProgram} program
 * @param {readonly K[]} names
 * @returns {Record<K, WebGLUniformLocation>}
 */
function uniformLocations(gl, program, names) {
  const out = /** @type {Record<K, WebGLUniformLocation>} */ ({});
  for (const name of names) {
    const loc = gl.getUniformLocation(program, name);
    if (loc === null) throw new Error(`Glade renderer: uniform ${name} is not active; check render/shaders.mjs`);
    out[name] = loc;
  }
  return out;
}

/**
 * Cuts each art square (no gutter) out of the atlas canvas into one RGBA8 layer, premultiplying on the CPU.
 * Layer = direction * cols + frame, matching the atlas rows (0 growing, 1 declining).
 * @param {Atlas} atlas
 * @returns {Uint8Array} cellPx * cellPx * 4 bytes per layer, cols * rows layers
 */
function atlasLayers(atlas) {
  const { canvas, cols, rows, cellPx, gutterPx } = atlas;
  const ctx = /** @type {CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null} */ (
    canvas.getContext('2d'));
  if (!ctx) throw new Error('renderer atlas: could not read the atlas canvas through a 2D context');
  const src = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  const pitch = cellPx + gutterPx;
  const rowBytes = cellPx * 4;
  const layerBytes = cellPx * rowBytes;
  const out = new Uint8Array(layerBytes * cols * rows);
  for (let direction = 0; direction < rows; direction++) {
    for (let frame = 0; frame < cols; frame++) {
      const layerBase = (direction * cols + frame) * layerBytes;
      const left = frame * pitch + gutterPx / 2;
      const top = direction * pitch + gutterPx / 2;
      for (let py = 0; py < cellPx; py++) {
        let s = ((top + py) * canvas.width + left) * 4;
        let d = layerBase + py * rowBytes;
        for (let px = 0; px < cellPx; px++, s += 4, d += 4) {
          const a = src[s + 3];
          out[d] = Math.round(src[s] * a / 255);
          out[d + 1] = Math.round(src[s + 1] * a / 255);
          out[d + 2] = Math.round(src[s + 2] * a / 255);
          out[d + 3] = a;
        }
      }
    }
  }
  return out;
}

/**
 * Creates the Glade renderer on `canvas`. Throws GladeWebGL2Error when WebGL2 is unavailable.
 * @param {HTMLCanvasElement} canvas
 * @param {import('../params.mjs').Arena} arena bounds in mm, from window.__COBEART_CONFIG__.arena
 * @param {Atlas} atlas from generateAtlas
 * @returns {GladeRenderer}
 */
export function createRenderer(canvas, arena, atlas) {
  assertArena(arena);
  assertAtlas(atlas);
  const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, premultipliedAlpha: true });
  if (!gl) {
    throw new GladeWebGL2Error('Glade renderer: this browser or GPU gives no WebGL2 context; ' +
      'run the world in Electron or a WebGL2-capable browser with hardware acceleration enabled');
  }

  const onContextLost = () => {
    console.error('Glade renderer: the WebGL context was lost; reload the page to restore rendering');
  };
  canvas.addEventListener('webglcontextlost', onContextLost);

  const arenaMinX = arena.x[0];
  const arenaMinY = arena.y[0];
  const arenaSpanX = arena.x[1] - arena.x[0];
  const arenaSpanY = arena.y[1] - arena.y[0];

  /**
   * Throws unless `grid` covers exactly this renderer's arena, so the heatmap cannot misalign with the plants.
   * @param {import('../sim/state.mjs').Grid} grid
   */
  function assertGrid(grid) {
    const { x0, y0, nx, ny, h } = grid;
    if (x0 !== arenaMinX || y0 !== arenaMinY || nx * h !== arenaSpanX || ny * h !== arenaSpanY) {
      throw new Error(`renderer: state.grid (origin (${x0}, ${y0}) mm, ${nx}x${ny} cells of ${h} mm) does not ` +
        `cover the renderer arena (x [${arena.x[0]}, ${arena.x[1]}], y [${arena.y[0]}, ${arena.y[1]}] mm); ` +
        'build the state and the renderer from the same arena');
    }
  }

  // Plant program and its instance VAO.
  const plantProgram = linkProgram(gl, PLANT_VERTEX_SHADER, PLANT_FRAGMENT_SHADER, 'plant');
  const plantU = uniformLocations(gl, plantProgram, /** @type {const} */ ([
    'u_arenaMin', 'u_arenaSpan', 'u_spriteSize', 'u_sizeJitter', 'u_brightnessJitter', 'u_shadowScale',
    'u_shadowOffset', 'u_nFrames', 'u_atlas', 'u_shadowStrength', 'u_shadowBlur',
  ]));
  const instanceBuffer = /** @type {WebGLBuffer} */ (gl.createBuffer());
  const plantVao = /** @type {WebGLVertexArrayObject} */ (gl.createVertexArray());
  gl.bindVertexArray(plantVao);
  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  gl.enableVertexAttribArray(PLANT_ATTRIB.position);
  gl.vertexAttribPointer(PLANT_ATTRIB.position, 2, gl.FLOAT, false, INSTANCE_BYTES, 0);
  gl.vertexAttribDivisor(PLANT_ATTRIB.position, 1);
  gl.enableVertexAttribArray(PLANT_ATTRIB.seed);
  gl.vertexAttribIPointer(PLANT_ATTRIB.seed, 1, gl.UNSIGNED_INT, INSTANCE_BYTES, 8);
  gl.vertexAttribDivisor(PLANT_ATTRIB.seed, 1);
  gl.enableVertexAttribArray(PLANT_ATTRIB.frameDirection);
  gl.vertexAttribIPointer(PLANT_ATTRIB.frameDirection, 1, gl.UNSIGNED_INT, INSTANCE_BYTES, 12);
  gl.vertexAttribDivisor(PLANT_ATTRIB.frameDirection, 1);
  gl.bindVertexArray(null);

  // Heatmap program; the full-screen triangle comes from gl_VertexID, so its VAO has no attributes.
  const heatProgram = linkProgram(gl, HEATMAP_VERTEX_SHADER, HEATMAP_FRAGMENT_SHADER, 'heatmap');
  const heatU = uniformLocations(gl, heatProgram, /** @type {const} */ (['u_density', 'u_eCap']));
  const heatVao = /** @type {WebGLVertexArrayObject} */ (gl.createVertexArray());

  /** Instance staging: persistent, grown by doubling; two views share one buffer. */
  let instanceCapacity = 0;
  /** @type {Float32Array} instance words read as f32 (x, y) */
  let instanceF32 = new Float32Array(0);
  /** @type {Uint32Array} instance words read as u32 (seed, frame | direction << 16) */
  let instanceU32 = new Uint32Array(0);

  /** @param {number} needed */
  function ensureInstanceCapacity(needed) {
    if (needed <= instanceCapacity) return;
    let capacity = Math.max(instanceCapacity, INITIAL_INSTANCES);
    while (capacity < needed) capacity *= 2;
    const bytes = new ArrayBuffer(capacity * INSTANCE_BYTES);
    instanceF32 = new Float32Array(bytes);
    instanceU32 = new Uint32Array(bytes);
    instanceCapacity = capacity;
    gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, bytes.byteLength, gl.DYNAMIC_DRAW);
  }

  let disposed = false;

  function assertLive() {
    if (disposed) throw new Error('Glade renderer: renderer disposed; create a new one with createRenderer');
  }

  /** @type {WebGLTexture | null} */
  let atlasTexture = null;
  let atlasFrames = 0;

  /** @param {Atlas} next */
  function setAtlas(next) {
    assertLive();
    assertAtlas(next);
    const layers = atlasLayers(next);
    const layerCount = next.cols * next.rows;
    const levels = Math.floor(Math.log2(next.cellPx)) + 1;
    const texture = /** @type {WebGLTexture} */ (gl.createTexture());
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, texture);
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, levels, gl.RGBA8, next.cellPx, next.cellPx, layerCount);
    // Data is premultiplied on the CPU above; the browser must not touch it.
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, 0, next.cellPx, next.cellPx, layerCount,
      gl.RGBA, gl.UNSIGNED_BYTE, layers);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.generateMipmap(gl.TEXTURE_2D_ARRAY);
    if (atlasTexture) gl.deleteTexture(atlasTexture);
    atlasTexture = texture;
    atlasFrames = next.cols;
  }

  // Heatmap texture: R32F, NEAREST, sized to the grid; staging reallocated only when nx * ny changes.
  /** @type {WebGLTexture | null} */
  let densityTexture = null;
  let densityNx = 0;
  let densityNy = 0;
  /** @type {Float32Array} Float32 copy of grid.density for upload */
  let densityStaging = new Float32Array(0);

  /** @param {import('../sim/state.mjs').Grid} grid */
  function uploadDensity(grid) {
    const { nx, ny, density } = grid;
    if (nx * ny !== densityStaging.length) densityStaging = new Float32Array(nx * ny);
    densityStaging.set(density);
    gl.activeTexture(gl.TEXTURE1);
    if (nx !== densityNx || ny !== densityNy || !densityTexture) {
      if (densityTexture) gl.deleteTexture(densityTexture);
      densityTexture = /** @type {WebGLTexture} */ (gl.createTexture());
      gl.bindTexture(gl.TEXTURE_2D, densityTexture);
      gl.texStorage2D(gl.TEXTURE_2D, 1, gl.R32F, nx, ny);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      densityNx = nx;
      densityNy = ny;
    } else {
      gl.bindTexture(gl.TEXTURE_2D, densityTexture);
    }
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    // Row iy of the data is texture row t = iy, so t follows arena y like the plant mapping.
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, nx, ny, gl.RED, gl.FLOAT, densityStaging);
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
  }

  /**
   * Packs live plants newest first (oldest drawn last, on top) into the staging buffer and uploads them.
   * @param {import('../sim/state.mjs').GladeState} state
   * @returns {number} instances uploaded
   */
  function uploadInstances(state) {
    const count = state.count;
    if (!Number.isInteger(count) || count < 0 || count > state.capacity) {
      throw new Error(`renderer: state.count ${count} must be an integer in [0, capacity ${state.capacity}]`);
    }
    if (count === 0) return 0;
    ensureInstanceCapacity(count);
    // Read the pool arrays fresh each frame: ensureCapacity replaces them when the pool grows.
    const { x, y, seed, frame, direction } = state.plants;
    const f32 = instanceF32;
    const u32 = instanceU32;
    for (let i = count - 1, w = 0; i >= 0; i--, w += INSTANCE_WORDS) {
      f32[w] = x[i];
      f32[w + 1] = y[i];
      u32[w + 2] = seed[i];
      u32[w + 3] = frame[i] | (direction[i] << 16);
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, u32, 0, count * INSTANCE_WORDS);
    return count;
  }

  /**
   * @param {import('../sim/state.mjs').GladeState} state
   * @param {import('../params.mjs').GladeParams} params
   * @param {RenderOptions} [options]
   */
  function render(state, params, { heatmap = false } = {}) {
    assertLive();
    assertGrid(state.grid);
    if (params.N_frames !== atlasFrames) {
      throw new Error(`renderer: params.N_frames ${params.N_frames} differs from the atlas (${atlasFrames} frames); ` +
        'regenerate the atlas with generateAtlas and pass it to setAtlas');
    }
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);

    if (heatmap) {
      uploadDensity(state.grid);
      gl.disable(gl.BLEND);
      gl.useProgram(heatProgram);
      gl.uniform1i(heatU.u_density, 1);
      gl.uniform1f(heatU.u_eCap, params.E_cap);
      gl.bindVertexArray(heatVao);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    const instances = uploadInstances(state);
    if (instances > 0) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(plantProgram);
      gl.uniform2f(plantU.u_arenaMin, arenaMinX, arenaMinY);
      gl.uniform2f(plantU.u_arenaSpan, arenaSpanX, arenaSpanY);
      gl.uniform1f(plantU.u_spriteSize, params.sprite_size_mm);
      gl.uniform1f(plantU.u_sizeJitter, params.size_jitter);
      gl.uniform1f(plantU.u_brightnessJitter, params.brightness_jitter);
      gl.uniform1f(plantU.u_shadowScale, params.shadow_scale);
      gl.uniform1f(plantU.u_shadowStrength, params.shadow_strength);
      gl.uniform1f(plantU.u_shadowBlur, params.shadow_blur);
      // G26: arena (-1, +1) / sqrt(2) * offset, screen bottom right under D18.
      const o = params.shadow_offset_mm * Math.SQRT1_2;
      gl.uniform2f(plantU.u_shadowOffset, -o, o);
      gl.uniform1i(plantU.u_nFrames, atlasFrames);
      gl.uniform1i(plantU.u_atlas, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D_ARRAY, atlasTexture);
      gl.bindVertexArray(plantVao);
      // With no halo, start at vertex 6 so gl_VertexID / 6 is 1 (sprite) for every drawn vertex.
      const first = params.shadow_strength === 0 ? VERTICES_PER_QUAD : 0;
      gl.drawArraysInstanced(gl.TRIANGLES, first, VERTICES_PER_INSTANCE - first, instances);
    }
    gl.bindVertexArray(null);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    canvas.removeEventListener('webglcontextlost', onContextLost);
    gl.deleteProgram(plantProgram);
    gl.deleteProgram(heatProgram);
    gl.deleteBuffer(instanceBuffer);
    gl.deleteVertexArray(plantVao);
    gl.deleteVertexArray(heatVao);
    if (atlasTexture) gl.deleteTexture(atlasTexture);
    if (densityTexture) gl.deleteTexture(densityTexture);
    atlasTexture = null;
    densityTexture = null;
  }

  setAtlas(atlas);
  resize();
  return { render, setAtlas, resize, dispose };
}

// Grass VAT renderer (GP roadmap, Phase 5, G50 to G54 and G60): one instanced draw of a grass bake. Each instance
// carries its growth g, vitality v and the stage pair around t = g * t_end; the vertex shader blends those stages,
// applies the decline rule and the wind, and places the plant in the arena. `capture` reads the declined model-space
// values of one instance back by transform feedback for the check against decline.py. The page owns the context,
// the canvas size, the viewport and the clear.

import {
  CAPTURE_FLOATS, CAPTURE_VARYINGS, GRASS_FRAGMENT_SHADER, LOCATIONS, TEXTURE_UNITS, grassVertexShader,
} from './shader.mjs';
import {
  DECLINE_PARAM_NAMES, ORGAN_STAGE_LAYERS, ORGAN_STATIC_ROWS, checkGrassVat, grassParams, packOrganStage,
  packOrganStatic, padVertexStages, stagePair, vertexTextureLayout,
} from './vat-textures.mjs';

/** @typedef {import('../vat/vat.mjs').Vat} Vat */
/** @typedef {import('../vat/vat.mjs').VatBlock} VatBlock */

/**
 * Floats per instance, three vec4s:
 * - place: x mm, y mm, rotation rad, mm per model metre;
 * - motion: lean x, lean y (arena axes, rad), wind x, wind y (arena axes, m/s);
 * - growth: g, v, i0, w, where `{i0, w} = stagePair(vat.stageTimes, g * tEnd)` (vat-textures.mjs), filled by the
 *   caller; the shader blends stage i0 with stage min(i0 + 1, last) by w.
 */
export const GRASS_INSTANCE_FLOATS = 12;

/**
 * @typedef {object} GrassDrawUniforms
 * @property {Float32Array} viewProj column-major
 * @property {number} time s
 * @property {number} speedRef m/s
 * @property {number} leafBend rad
 * @property {number} leafSwayHz
 * @property {number} flutter rad
 * @property {number} flutterHz
 * @property {[number, number]} flip m/s
 * @property {number} dryWindResponse wind drive factor of a dead leaf
 */

/**
 * Declined model-space values per vertex, after decline and before wind and placement.
 * @typedef {object} Capture
 * @property {Float32Array} positions 3 per vertex, m
 * @property {Float32Array} pivots 3 per vertex, m
 * @property {Float32Array} axes 3 per vertex, unit
 * @property {Float32Array} browning 1 per vertex
 * @property {Float32Array} opacity 1 per vertex
 */

/**
 * @typedef {object} GpuTimer
 * @property {() => void} begin starts timing the next GL commands (no-op while a query is open)
 * @property {() => void} end
 * @property {() => number | null} poll the oldest finished measurement in ms, or null when none is ready
 */

/**
 * @typedef {object} GrassRenderer
 * @property {(instances: Float32Array, count: number, uniforms: GrassDrawUniforms) => void} draw binds its program,
 *   textures and vertex array, enables the depth test and disables culling and blending; draws into the bound
 *   framebuffer with the caller's viewport (no clear)
 * @property {(t: number, v: number) => Capture} capture
 * @property {GpuTimer | null} gpuTimer
 * @property {() => void} dispose
 */

/**
 * @param {WebGL2RenderingContext} gl
 * @param {number} type
 * @param {string} source
 * @param {string} label
 */
function compile(gl, type, source, label) {
  const shader = /** @type {WebGLShader} */ (gl.createShader(type));
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`grass renderer: ${label} shader does not compile: ${log}`);
  }
  return shader;
}

/**
 * @param {WebGL2RenderingContext} gl
 * @param {string} vertexSource
 * @param {readonly string[] | null} varyings interleaved transform feedback outputs
 * @param {string} label
 */
function link(gl, vertexSource, varyings, label) {
  const program = /** @type {WebGLProgram} */ (gl.createProgram());
  const vs = compile(gl, gl.VERTEX_SHADER, vertexSource, `${label} vertex`);
  const fs = compile(gl, gl.FRAGMENT_SHADER, GRASS_FRAGMENT_SHADER, `${label} fragment`);
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  if (varyings) gl.transformFeedbackVaryings(program, [...varyings], gl.INTERLEAVED_ATTRIBS);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`grass renderer: ${label} program does not link: ${gl.getProgramInfoLog(program)}`);
  }
  return program;
}

/**
 * @param {WebGL2RenderingContext} gl
 * @returns {GpuTimer | null}
 */
function createGpuTimer(gl) {
  const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2');
  if (!ext) return null;
  /** @type {WebGLQuery[]} */
  const pending = [];
  /** @type {WebGLQuery | null} */
  let open = null;
  return {
    begin() {
      if (open) return;
      open = gl.createQuery();
      gl.beginQuery(ext.TIME_ELAPSED_EXT, open);
    },
    end() {
      if (!open) return;
      gl.endQuery(ext.TIME_ELAPSED_EXT);
      pending.push(open);
      open = null;
    },
    poll() {
      const disjoint = gl.getParameter(ext.GPU_DISJOINT_EXT);
      while (pending.length) {
        const query = pending[0];
        if (!gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) return null;
        pending.shift();
        const ns = gl.getQueryParameter(query, gl.QUERY_RESULT);
        gl.deleteQuery(query);
        if (!disjoint) return ns / 1e6;
      }
      return null;
    },
  };
}

/**
 * @param {WebGL2RenderingContext} gl the page's context
 * @param {Vat} vat a grass bake (checked with checkGrassVat)
 * @returns {GrassRenderer}
 */
export function createGrassRenderer(gl, vat) {
  checkGrassVat(vat);
  const params = grassParams(vat);
  if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) {
    throw new Error('grass renderer: createGrassRenderer needs a WebGL2 context, e.g. canvas.getContext(\'webgl2\')');
  }

  const stageCount = vat.stageTimes.length;
  const { vertexCount, organCount } = vat;
  const maxSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
  for (const [name, size] of [['organCount', organCount], ['stage count', stageCount]]) {
    if (size > maxSize) {
      throw new Error(`grass renderer: ${name} ${size} exceeds MAX_TEXTURE_SIZE ${maxSize}; bake with fewer ` +
        'organs or fewer --samples');
    }
  }
  // G51 puts a stage in one row of width vertexCount; above MAX_TEXTURE_SIZE a stage wraps over several rows.
  const layout = vertexTextureLayout(vertexCount, stageCount, maxSize);
  const vertexRows = layout.rowsPerStage * stageCount;
  if (gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS) < ORGAN_STAGE_LAYERS.length) {
    throw new Error('grass renderer: MAX_ARRAY_TEXTURE_LAYERS is below the organ-stage layer count');
  }

  const block = (/** @type {string} */ name) => /** @type {VatBlock} */ (vat.blocks.get(name));
  const unpackAlignment = gl.getParameter(gl.UNPACK_ALIGNMENT);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  /** @type {WebGLTexture[]} */
  const textures = [];
  const texture = (/** @type {number} */ unit, /** @type {number} */ target) => {
    const tex = /** @type {WebGLTexture} */ (gl.createTexture());
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(target, tex);
    gl.texParameteri(target, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(target, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(target, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(target, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    textures.push(tex);
    return tex;
  };
  /** @type {[number, number, WebGLTexture][]} unit, target, texture */
  const bindings = [];
  const tex2d = (/** @type {number} */ unit, /** @type {number} */ internal, /** @type {number} */ width,
    /** @type {number} */ height, /** @type {number} */ format, /** @type {number} */ type,
    /** @type {ArrayBufferView} */ data) => {
    const tex = texture(unit, gl.TEXTURE_2D);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, width, height, 0, format, type, data);
    bindings.push([unit, gl.TEXTURE_2D, tex]);
  };
  tex2d(TEXTURE_UNITS.position, gl.RGB16F, layout.width, vertexRows, gl.RGB, gl.HALF_FLOAT,
    padVertexStages(block('position').raw, vertexCount, stageCount, 3, layout));
  tex2d(TEXTURE_UNITS.normal, gl.RG16I, layout.width, vertexRows, gl.RG_INTEGER, gl.SHORT,
    padVertexStages(block('normal').raw, vertexCount, stageCount, 2, layout));
  tex2d(TEXTURE_UNITS.organStatic, gl.RGBA32F, organCount, ORGAN_STATIC_ROWS.length, gl.RGBA, gl.FLOAT,
    packOrganStatic(vat));
  const organStage = texture(TEXTURE_UNITS.organStage, gl.TEXTURE_2D_ARRAY);
  gl.texImage3D(gl.TEXTURE_2D_ARRAY, 0, gl.RGBA32F, organCount, stageCount, ORGAN_STAGE_LAYERS.length, 0, gl.RGBA,
    gl.FLOAT, packOrganStage(vat));
  bindings.push([TEXTURE_UNITS.organStage, gl.TEXTURE_2D_ARRAY, organStage]);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, unpackAlignment);
  const bindTextures = () => {
    for (const [unit, target, tex] of bindings) {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(target, tex);
    }
  };

  const drawProgram = link(gl, grassVertexShader(), null, 'draw');
  const captureProgram = link(gl, grassVertexShader({ capture: true }), CAPTURE_VARYINGS, 'capture');

  /**
   * Uniform locations of a program by name (without the u_ prefix).
   * @param {WebGLProgram} program
   */
  const locations = (program) => {
    /** @type {Record<string, WebGLUniformLocation | null>} */
    const u = {};
    for (const name of ['position', 'normal', 'organStage', 'organStatic', 'stageCount',
      'vertexRowWidth', 'vertexRowsPerStage', 'tEnd',
      'fadeDuration', 'collarBend', 'viewProj', 'time', 'stemHeight', 'speedRef', 'leafBend', 'leafSwayHz', 'flutter',
      'flutterHz', 'flip', 'dryWindResponse', 'lightDir', 'captureT']) {
      u[name] = gl.getUniformLocation(program, `u_${name}`);
    }
    for (const name of DECLINE_PARAM_NAMES) u[name] = gl.getUniformLocation(program, `u_decline.${name}`);
    return u;
  };
  const light = (() => {
    const l = [0.3, -0.4, 1.0];
    const n = Math.hypot(...l);
    return l.map((c) => c / n);
  })();
  /**
   * Sets the uniforms that depend only on the bake.
   * @param {WebGLProgram} program
   * @param {Record<string, WebGLUniformLocation | null>} u
   */
  const setBakeUniforms = (program, u) => {
    gl.useProgram(program);
    for (const [name, unit] of Object.entries(TEXTURE_UNITS)) gl.uniform1i(u[name], unit);
    gl.uniform1i(u.stageCount, stageCount);
    gl.uniform1i(u.vertexRowWidth, layout.width);
    gl.uniform1i(u.vertexRowsPerStage, layout.rowsPerStage);
    gl.uniform1f(u.tEnd, params.tEnd);
    gl.uniform1f(u.fadeDuration, params.fadeDuration);
    gl.uniform1f(u.collarBend, params.collarBend);
    gl.uniform1f(u.stemHeight, params.sheathPlateauM);
    gl.uniform3f(u.lightDir, light[0], light[1], light[2]);
    for (const name of DECLINE_PARAM_NAMES) {
      if (!u[name]) throw new Error(`grass renderer: uniform u_decline.${name} is missing from the shader`);
      gl.uniform1f(u[name], params.decline[name]);
    }
  };
  const drawU = locations(drawProgram);
  const captureU = locations(captureProgram);
  setBakeUniforms(drawProgram, drawU);
  setBakeUniforms(captureProgram, captureU);
  gl.useProgram(null);

  /** @type {WebGLBuffer[]} */
  const buffers = [];
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  /**
   * @param {ArrayBufferView} data
   * @param {number} location
   * @param {'uint' | 'float'} kind
   */
  const vertexAttribute = (data, location, kind) => {
    const buffer = /** @type {WebGLBuffer} */ (gl.createBuffer());
    buffers.push(buffer);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(location);
    if (kind === 'uint') gl.vertexAttribIPointer(location, 1, gl.UNSIGNED_INT, 0, 0);
    else gl.vertexAttribPointer(location, 1, gl.FLOAT, false, 0, 0);
  };
  vertexAttribute(block('organ').raw, LOCATIONS.organ, 'uint');
  vertexAttribute(block('kind').raw, LOCATIONS.kind, 'uint');
  vertexAttribute(block('s').raw, LOCATIONS.s, 'float');
  const indexBuffer = /** @type {WebGLBuffer} */ (gl.createBuffer());
  buffers.push(indexBuffer);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, vat.indices, gl.STATIC_DRAW);
  const instanceBuffer = /** @type {WebGLBuffer} */ (gl.createBuffer());
  buffers.push(instanceBuffer);
  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  for (const [k, location] of [LOCATIONS.place, LOCATIONS.motion, LOCATIONS.growth].entries()) {
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 4, gl.FLOAT, false, GRASS_INSTANCE_FLOATS * 4, k * 16);
    gl.vertexAttribDivisor(location, 1);
  }
  gl.bindVertexArray(null);
  gl.bindBuffer(gl.ARRAY_BUFFER, null);

  const feedbackBuffer = /** @type {WebGLBuffer} */ (gl.createBuffer());
  buffers.push(feedbackBuffer);
  gl.bindBuffer(gl.COPY_READ_BUFFER, feedbackBuffer);
  gl.bufferData(gl.COPY_READ_BUFFER, vertexCount * CAPTURE_FLOATS * 4, gl.STREAM_READ);
  gl.bindBuffer(gl.COPY_READ_BUFFER, null);
  const feedback = gl.createTransformFeedback();

  const gpuTimer = createGpuTimer(gl);

  return {
    draw(instances, count, d) {
      gl.enable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.disable(gl.BLEND);
      gl.useProgram(drawProgram);
      bindTextures();
      gl.uniformMatrix4fv(drawU.viewProj, false, d.viewProj);
      gl.uniform1f(drawU.time, d.time);
      gl.uniform1f(drawU.speedRef, d.speedRef);
      gl.uniform1f(drawU.leafBend, d.leafBend);
      gl.uniform1f(drawU.leafSwayHz, d.leafSwayHz);
      gl.uniform1f(drawU.flutter, d.flutter);
      gl.uniform1f(drawU.flutterHz, d.flutterHz);
      gl.uniform2f(drawU.flip, d.flip[0], d.flip[1]);
      gl.uniform1f(drawU.dryWindResponse, d.dryWindResponse);
      gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, instances.subarray(0, count * GRASS_INSTANCE_FLOATS), gl.DYNAMIC_DRAW);
      gl.bindVertexArray(vao);
      gl.drawElementsInstanced(gl.TRIANGLES, vat.indices.length, gl.UNSIGNED_INT, 0, count);
      gl.bindVertexArray(null);
    },

    capture(t, v) {
      if (!Number.isFinite(t) || !Number.isFinite(v)) {
        throw new Error(`grass renderer: capture needs finite t and v, got t ${t}, v ${v}`);
      }
      const instance = new Float32Array(GRASS_INSTANCE_FLOATS);
      const { i0, w } = stagePair(vat.stageTimes, t);
      instance[3] = 1;
      instance[8] = t / params.tEnd;
      instance[9] = v;
      instance[10] = i0;
      instance[11] = w;
      gl.useProgram(captureProgram);
      bindTextures();
      gl.uniform1f(captureU.captureT, t);
      gl.uniformMatrix4fv(captureU.viewProj, false, new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]));
      gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, instance, gl.DYNAMIC_DRAW);
      gl.bindVertexArray(vao);
      gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK, feedback);
      gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, feedbackBuffer);
      gl.enable(gl.RASTERIZER_DISCARD);
      gl.beginTransformFeedback(gl.POINTS);
      gl.drawArraysInstanced(gl.POINTS, 0, vertexCount, 1);
      gl.endTransformFeedback();
      gl.disable(gl.RASTERIZER_DISCARD);
      gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, null);
      gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK, null);
      gl.bindVertexArray(null);

      const raw = new Float32Array(vertexCount * CAPTURE_FLOATS);
      gl.bindBuffer(gl.COPY_READ_BUFFER, feedbackBuffer);
      gl.getBufferSubData(gl.COPY_READ_BUFFER, 0, raw);
      gl.bindBuffer(gl.COPY_READ_BUFFER, null);
      const out = {
        positions: new Float32Array(vertexCount * 3),
        pivots: new Float32Array(vertexCount * 3),
        axes: new Float32Array(vertexCount * 3),
        browning: new Float32Array(vertexCount),
        opacity: new Float32Array(vertexCount),
      };
      for (let i = 0; i < vertexCount; i++) {
        const o = i * CAPTURE_FLOATS;
        for (let c = 0; c < 3; c++) {
          out.positions[3 * i + c] = raw[o + c];
          out.pivots[3 * i + c] = raw[o + 3 + c];
          out.axes[3 * i + c] = raw[o + 6 + c];
        }
        out.browning[i] = raw[o + 9];
        out.opacity[i] = raw[o + 10];
      }
      return out;
    },

    gpuTimer,

    dispose() {
      for (const tex of textures) gl.deleteTexture(tex);
      for (const buffer of buffers) gl.deleteBuffer(buffer);
      gl.deleteTransformFeedback(feedback);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(drawProgram);
      gl.deleteProgram(captureProgram);
    },
  };
}

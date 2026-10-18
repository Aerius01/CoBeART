// Background Shader Registry
// The only background model: every shader the fluid simulation can draw behind the dye is listed here.

import electricClouds from './electric-clouds/shader.js';
import circles from './circles/shader.js';
import zephyr from './zephyr/shader.js';

/**
 * Per-frame data handed to every background, whether or not its shader uses it.
 * @typedef {Object} BackgroundContext
 * @property {number} time Seconds since the simulation started.
 * @property {{width: number, height: number}} resolution Canvas size in pixels.
 * @property {?Object} audio Latest audio (contract/audio.schema.json, e.g. `rms`, `peak`, `zcr`,
 *   `dominant_frequency`), or null until the first one arrives and while the bridge reports it stale.
 * @property {Object<string, Object>} bodies `bodies` of the latest frame (contract/frame.schema.json), keyed by role
 *   (`right_hand`, `head`, ...; each with `x`, `y`, `z`, velocities, quaternion...); a role is absent when untracked,
 *   and the object is empty until the first frame arrives.
 */

/**
 * A registry entry. The fragment shader is compiled against the sim's base vertex shader (varying `vUv`).
 * @typedef {Object} BackgroundDefinition
 * @property {string} name Label shown in the background selector.
 * @property {string} fragmentShader GLSL ES fragment shader source.
 * @property {(gl: WebGLRenderingContext, program: {uniforms: Object<string, WebGLUniformLocation>},
 *   ctx: BackgroundContext) => void} setUniforms Sets every uniform the shader uses; the program is already bound.
 */

/** @type {ReadonlyArray<BackgroundDefinition>} */
export default [
  electricClouds,
  circles,
  zephyr
];

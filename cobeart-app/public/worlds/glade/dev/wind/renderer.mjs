// Wind prototype renderer: one instanced draw of the plant mesh. The vertex shader bends each instance in plant-local
// space (leaf flip and flutter about the leaf axis, leaf sway about its node, then the stem arc from the spring lean)
// before placing it in the arena. Two-sided: back faces get the underside colour so flipped blades read from above.

const VERTEX_SHADER = /* glsl */ `#version 300 es
precision highp float;

layout(location = 0) in vec3 a_position;  // model metres, Z up
layout(location = 1) in vec3 a_normal;
layout(location = 2) in vec3 a_pivot;     // leaf attachment point, or stem axis point at the vertex's height
layout(location = 3) in vec3 a_axis;      // leaf initial direction, or stem tangent
layout(location = 4) in vec4 a_meta;      // s, stem_t, organ, phase
layout(location = 5) in float a_material;
layout(location = 6) in vec4 i_place;     // x mm, y mm, rotation rad, mm per model metre
layout(location = 7) in vec4 i_motion;    // lean x, lean y (arena axes, rad), wind x, wind y (arena axes, m/s)

uniform mat4 u_viewProj;
uniform float u_time;
uniform float u_stemHeight;   // model metres
uniform float u_speedRef;     // m/s
uniform float u_leafBend;     // rad at full drive, at the leaf tip
uniform float u_leafSwayHz;
uniform float u_flutter;      // rad at full drive
uniform float u_flutterHz;
uniform vec2 u_flip;          // wind speeds (m/s) where flipping starts and is complete

out vec3 v_normal;
out float v_material;
out float v_s;

const float TAU = 6.2831853;

vec3 rotateAbout(vec3 v, vec3 k, float a) {
  float c = cos(a);
  return v * c + cross(k, v) * sin(a) + k * dot(k, v) * (1.0 - c);
}

void main() {
  float s = a_meta.x;
  float stemT = a_meta.y;
  float organ = a_meta.z;
  float phase = a_meta.w;
  float cs = cos(i_place.z);
  float sn = sin(i_place.z);
  mat2 toLocal = mat2(cs, -sn, sn, cs);

  vec2 windL = toLocal * i_motion.zw;
  float speed = length(windL);
  vec3 windDir = speed > 1e-4 ? vec3(windL / speed, 0.0) : vec3(1.0, 0.0, 0.0);
  float drive = tanh(speed / u_speedRef);
  vec3 p = a_position;
  vec3 n = a_normal;

  if (organ > 0.5) {
    vec3 A = normalize(a_axis);
    // Flip and flutter: twist about the leaf's base axis, growing over the first half of the blade.
    vec3 side = cross(vec3(0.0, 0.0, 1.0), A);
    float sideLen = length(side);
    float flipSign = (sideLen > 1e-3 && dot(side, windDir) < 0.0) ? -1.0 : 1.0;
    float flip = 3.14159265 * smoothstep(u_flip.x, u_flip.y, speed) * flipSign;
    float flutter = u_flutter * drive * sin(u_time * TAU * u_flutterHz * (0.8 + 0.4 * phase) + phase * TAU);
    float twist = (flip + flutter) * smoothstep(0.0, 0.5, s);
    p = a_pivot + rotateAbout(p - a_pivot, A, twist);
    n = rotateAbout(n, A, twist);
    // Sway: bend toward the wind about the node, progressively along the blade.
    vec3 K = cross(A, windDir);
    float kLen = length(K);
    if (kLen > 1e-3) {
      K /= kLen;
      float osc = 0.75 + 0.25 * sin(u_time * TAU * u_leafSwayHz * (0.8 + 0.4 * phase) + phase * TAU);
      float bend = u_leafBend * drive * osc * s;
      p = a_pivot + rotateAbout(p - a_pivot, K, bend);
      n = rotateAbout(n, K, bend);
    }
  }

  // Stem: a circular arc whose tip angle is the spring lean. Each vertex rides rigidly with the stem point at its
  // attachment height, so lengths are preserved.
  vec2 leanL = toLocal * i_motion.xy;
  float phi = length(leanL);
  if (phi > 1e-5) {
    vec3 ld = vec3(leanL / phi, 0.0);
    vec3 B = vec3(-ld.y, ld.x, 0.0);
    float h = stemT * u_stemHeight;
    float x = phi * stemT;
    float arcSide = x > 1e-4 ? h * (1.0 - cos(x)) / x : 0.5 * h * x;
    float arcUp = x > 1e-4 ? h * sin(x) / x : h;
    p = ld * arcSide + vec3(0.0, 0.0, arcUp) + rotateAbout(p - vec3(0.0, 0.0, h), B, x);
    n = rotateAbout(n, B, x);
  }

  vec3 pl = p * i_place.w;
  vec3 world = vec3(cs * pl.x - sn * pl.y + i_place.x, sn * pl.x + cs * pl.y + i_place.y, pl.z);
  v_normal = vec3(cs * n.x - sn * n.y, sn * n.x + cs * n.y, n.z);
  v_material = a_material;
  v_s = s;
  gl_Position = u_viewProj * vec4(world, 1.0);
}
`;

const FRAGMENT_SHADER = /* glsl */ `#version 300 es
precision highp float;

in vec3 v_normal;
in float v_material;
in float v_s;

uniform vec3 u_lightDir;  // toward the light, arena axes
uniform float u_stemMaterial;

out vec4 outColor;

const vec3 LEAF_TOP = vec3(0.16, 0.42, 0.10);
const vec3 LEAF_UNDER = vec3(0.55, 0.66, 0.42);
const vec3 STEM = vec3(0.42, 0.52, 0.20);

void main() {
  vec3 n = normalize(v_normal);
  if (!gl_FrontFacing) n = -n;
  bool stem = abs(v_material - u_stemMaterial) < 0.5;
  // The generator winds leaf faces so their normals point to the underside (away from the sky at rest).
  vec3 albedo = stem ? STEM : (gl_FrontFacing ? LEAF_UNDER : LEAF_TOP);
  float light = 0.35 + 0.65 * max(dot(n, u_lightDir), 0.0);
  float occlusion = mix(0.55, 1.0, smoothstep(0.0, 0.35, v_s));
  outColor = vec4(albedo * light * occlusion, 1.0);
}
`;

/** Floats per instance: place (x, y, rotation, mm per metre) then motion (lean x, lean y, wind x, wind y). */
export const INSTANCE_FLOATS = 8;

const MESH_ATTRIBUTES = [
  { name: 'POSITION', location: 0, size: 3 },
  { name: 'NORMAL', location: 1, size: 3 },
  { name: '_PIVOT', location: 2, size: 3 },
  { name: '_AXIS', location: 3, size: 3 },
];
const META = ['_S', '_STEM_T', '_ORGAN', '_PHASE'];

/**
 * @param {WebGL2RenderingContext} gl
 * @param {number} type
 * @param {string} source
 */
function compile(gl, type, source) {
  const shader = /** @type {WebGLShader} */ (gl.createShader(type));
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(`wind renderer: ${type === gl.VERTEX_SHADER ? 'vertex' : 'fragment'} shader: ` +
      gl.getShaderInfoLog(shader));
  }
  return shader;
}

/**
 * Column-major view-projection for the canonical top-down view (D18: arena x_max at the left, y_min at the top),
 * stretched to fill the canvas and zoomed about the arena centre.
 * @param {{x: [number, number], y: [number, number]}} arena
 * @param {number} zoom
 * @param {number} zMaxMm height mapped to the near plane
 */
export function topDownMatrix(arena, zoom, zMaxMm) {
  const sx = (arena.x[1] - arena.x[0]) / zoom;
  const sy = (arena.y[1] - arena.y[0]) / zoom;
  const cx = (arena.x[0] + arena.x[1]) / 2;
  const cy = (arena.y[0] + arena.y[1]) / 2;
  const m = new Float32Array(16);
  m[0] = -2 / sx;
  m[12] = 2 * cx / sx;
  m[5] = -2 / sy;
  m[13] = 2 * cy / sy;
  m[10] = -2 / zMaxMm;
  m[14] = 1;
  m[15] = 1;
  return m;
}

/**
 * Column-major perspective view of the arena from its y_max side, tilted down, keeping D18's orientation
 * (x_max on the left, y_min away from the viewer).
 * @param {{x: [number, number], y: [number, number]}} arena
 * @param {number} zoom
 * @param {number} aspect canvas width / height
 */
export function obliqueMatrix(arena, zoom, aspect) {
  const cx = (arena.x[0] + arena.x[1]) / 2;
  const cy = (arena.y[0] + arena.y[1]) / 2;
  const span = Math.max(arena.x[1] - arena.x[0], arena.y[1] - arena.y[0]);
  const dist = 1.3 * span / zoom;
  const elev = 40 * Math.PI / 180;
  const eye = [cx, cy + dist * Math.cos(elev), dist * Math.sin(elev)];
  const f = normalize([cx - eye[0], cy - eye[1], -eye[2]]);
  const r = normalize(cross(f, [0, 0, 1]));
  const u = cross(r, f);
  const view = [
    r[0], u[0], -f[0], 0,
    r[1], u[1], -f[1], 0,
    r[2], u[2], -f[2], 0,
    -dot(r, eye), -dot(u, eye), dot(f, eye), 1,
  ];
  const near = dist * 0.05;
  const far = dist * 4;
  const t = 1 / Math.tan((35 * Math.PI / 180) / 2);
  const proj = [
    t / aspect, 0, 0, 0,
    0, t, 0, 0,
    0, 0, (far + near) / (near - far), -1,
    0, 0, 2 * far * near / (near - far), 0,
  ];
  const out = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let row = 0; row < 4; row++) {
      let v = 0;
      for (let k = 0; k < 4; k++) v += proj[k * 4 + row] * view[c * 4 + k];
      out[c * 4 + row] = v;
    }
  }
  return out;
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const normalize = (a) => { const l = Math.hypot(...a); return [a[0] / l, a[1] / l, a[2] / l]; };

/**
 * @typedef {object} DrawUniforms
 * @property {Float32Array} viewProj
 * @property {number} time s
 * @property {number} speedRef m/s
 * @property {number} leafBend rad
 * @property {number} leafSwayHz
 * @property {number} flutter rad
 * @property {number} flutterHz
 * @property {[number, number]} flip m/s
 */

/**
 * @param {HTMLCanvasElement} canvas
 * @param {import('./glb.mjs').MergedMesh} mesh
 */
export function createWindRenderer(canvas, mesh) {
  const gl = canvas.getContext('webgl2', { antialias: true });
  if (!gl) throw new Error('wind renderer: WebGL2 is not available in this browser');

  const program = /** @type {WebGLProgram} */ (gl.createProgram());
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`wind renderer: link: ${gl.getProgramInfoLog(program)}`);
  }
  /** @type {Record<string, WebGLUniformLocation | null>} */
  const u = {};
  for (const name of ['viewProj', 'time', 'stemHeight', 'speedRef', 'leafBend', 'leafSwayHz', 'flutter', 'flutterHz',
    'flip', 'lightDir', 'stemMaterial']) {
    u[name] = gl.getUniformLocation(program, `u_${name}`);
  }

  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const upload = (/** @type {Float32Array} */ data, /** @type {number} */ location, /** @type {number} */ size) => {
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
  };
  for (const { name, location, size } of MESH_ATTRIBUTES) {
    const attr = mesh.attributes.get(name);
    if (!attr || attr.size !== size) throw new Error(`wind renderer: mesh attribute ${name} (vec${size}) is missing`);
    upload(attr.data, location, size);
  }
  const meta = new Float32Array(mesh.vertexCount * 4);
  for (const [k, name] of META.entries()) {
    const attr = mesh.attributes.get(name);
    if (!attr || attr.size !== 1) throw new Error(`wind renderer: mesh attribute ${name} (float) is missing; ` +
      'export with build_grass_plant.py');
    for (let i = 0; i < mesh.vertexCount; i++) meta[i * 4 + k] = attr.data[i];
  }
  upload(meta, 4, 4);
  upload(mesh.material, 5, 1);
  const indexBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);

  const instanceBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  for (const [k, location] of [6, 7].entries()) {
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 4, gl.FLOAT, false, INSTANCE_FLOATS * 4, k * 16);
    gl.vertexAttribDivisor(location, 1);
  }
  gl.bindVertexArray(null);

  // Stem height: the highest stem-axis pivot (organ 0).
  const pivot = /** @type {{data: Float32Array}} */ (mesh.attributes.get('_PIVOT')).data;
  const organ = /** @type {{data: Float32Array}} */ (mesh.attributes.get('_ORGAN')).data;
  let stemHeight = 0;
  for (let i = 0; i < mesh.vertexCount; i++) if (organ[i] === 0) stemHeight = Math.max(stemHeight, pivot[i * 3 + 2]);
  const stemMaterial = mesh.materialNames.indexOf('Grass_Stalk');
  if (stemMaterial < 0) throw new Error(`wind renderer: no Grass_Stalk material in [${mesh.materialNames}]`);
  const light = normalize([0.3, -0.4, 1.0]);

  return {
    resize() {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = Math.round(canvas.clientHeight * dpr);
    },
    /**
     * @param {Float32Array} instances INSTANCE_FLOATS per plant
     * @param {number} count
     * @param {DrawUniforms} d
     */
    draw(instances, count, d) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0.03, 0.035, 0.025, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.useProgram(program);
      gl.uniformMatrix4fv(u.viewProj, false, d.viewProj);
      gl.uniform1f(u.time, d.time);
      gl.uniform1f(u.stemHeight, stemHeight);
      gl.uniform1f(u.speedRef, d.speedRef);
      gl.uniform1f(u.leafBend, d.leafBend);
      gl.uniform1f(u.leafSwayHz, d.leafSwayHz);
      gl.uniform1f(u.flutter, d.flutter);
      gl.uniform1f(u.flutterHz, d.flutterHz);
      gl.uniform2f(u.flip, d.flip[0], d.flip[1]);
      gl.uniform3f(u.lightDir, light[0], light[1], light[2]);
      gl.uniform1f(u.stemMaterial, stemMaterial);
      gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, instances.subarray(0, count * INSTANCE_FLOATS), gl.DYNAMIC_DRAW);
      gl.bindVertexArray(vao);
      gl.drawElementsInstanced(gl.TRIANGLES, mesh.indices.length, gl.UNSIGNED_INT, 0, count);
      gl.bindVertexArray(null);
    },
  };
}

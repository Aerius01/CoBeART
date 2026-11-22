// Grass VAT shaders (GP roadmap, Phase 5, G51 to G54 and G60). The vertex shader fetches the two bake stages around
// its instance's model time, rebuilds the per-vertex inputs by the grass profile (../plant/grass_vat_profile.md),
// applies the decline rule of ../lpy/decline.py (declined_vertex, ported in one delimited block), then the wind of
// ../wind/renderer.mjs on the declined mesh, then places the plant in the arena. With CAPTURE defined the same
// source also writes the declined model-space values for transform feedback (the GLSL check against Python).

/** Attribute locations shared by the shader and the renderer. */
export const LOCATIONS = Object.freeze({
  organ: 0, // uint, the vertex's organ index
  kind: 1, // uint, 0 sheath, 1 blade
  s: 2, // float, fraction along the organ
  place: 3, // instance: x mm, y mm, rotation rad, mm per model metre
  motion: 4, // instance: lean x, lean y (arena axes, rad), wind x, wind y (arena axes, m/s)
  growth: 5, // instance: g, v, i0 (stage index, as a float), w (blend weight of stage i0 + 1)
});

/** Texture units of the shader's samplers. */
export const TEXTURE_UNITS = Object.freeze({
  position: 0,
  normal: 1,
  organStage: 2,
  organStatic: 3,
});

/** Transform feedback varyings of the capture program, interleaved: 11 floats per vertex. */
export const CAPTURE_VARYINGS = Object.freeze(['tf_position', 'tf_pivot', 'tf_axis', 'tf_browning', 'tf_opacity']);
export const CAPTURE_FLOATS = 11;

const VERTEX_BODY = /* glsl */ `
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp isampler2D;
precision highp sampler2DArray;

layout(location = ${LOCATIONS.organ}) in uint a_organ;
layout(location = ${LOCATIONS.kind}) in uint a_kind;
layout(location = ${LOCATIONS.s}) in float a_s;
layout(location = ${LOCATIONS.place}) in vec4 i_place;
layout(location = ${LOCATIONS.motion}) in vec4 i_motion;
layout(location = ${LOCATIONS.growth}) in vec4 i_growth;

uniform sampler2D u_position;        // RGB16F, vertexTexel(): healthy position (m)
uniform isampler2D u_normal;         // RG16I, vertexTexel(): oct16 underside normal
uniform int u_vertexRowWidth;        // texels per row (vertexCount when it fits, G51)
uniform int u_vertexRowsPerStage;    // rows per stage (1 when vertexCount fits)
uniform sampler2DArray u_organStage; // RGBA32F, x organ, y stage; layers: collar+arch, direction+sheath_length, emergence
uniform sampler2D u_organStatic;     // RGBA32F, x organ, y row (vat-textures.mjs ORGAN_STATIC_ROWS)
uniform int u_stageCount;
uniform float u_tEnd;                // P, last stage time
uniform float u_fadeDuration;        // P

uniform mat4 u_viewProj;
uniform float u_time;
uniform float u_stemHeight;   // model metres (SHEATH_PLATEAU_M)
uniform float u_speedRef;     // m/s
uniform float u_leafBend;     // rad at full drive, at the leaf tip
uniform float u_leafSwayHz;
uniform float u_flutter;      // rad at full drive
uniform float u_flutterHz;
uniform vec2 u_flip;          // wind speeds (m/s) where flipping starts and is complete
uniform float u_dryWindResponse; // drive factor of a dead leaf (health 0)

out vec3 v_normal;
flat out float v_kind;
out float v_s;
out float v_browning;
out float v_opacity;

#ifdef CAPTURE
uniform float u_captureT;     // P, model time of the capture (exact, not through g)
out vec3 tf_position;
out vec3 tf_pivot;
out vec3 tf_axis;
out float tf_browning;
out float tf_opacity;
#endif

const float TAU = 6.2831853;
const float SNORM16 = 32767.0;

// Rodrigues rotation in half-angle form: 1 - cos(a) = 2 sin^2(a / 2) stays accurate for small angles even where
// the GL's cos() is coarse, so the rotation stays norm-preserving.
vec3 rotateAbout(vec3 v, vec3 k, float a) {
  float h = 0.5 * a;
  float sh = sin(h);
  float s = 2.0 * sh * cos(h);
  float oneMinusC = 2.0 * sh * sh;
  return v * (1.0 - oneMinusC) + cross(k, v) * s + k * dot(k, v) * oneMinusC;
}

// ==== BEGIN decline port: decline.py declined_vertex (look, health, death_point, plant_fade, tiller_lean_rad,
// opacity, bend_cumulants, _profile_moment, chord_change). Clamps stand where Python raises. ====

struct DeclineParams {
  float V_DEAD;
  float DECLINE_AGE_SPREAD;
  float DECLINE_AGE_REF;
  float DECLINE_JITTER;
  float DECLINE_RESIDUAL;
  float SHRIVEL_FRACTION;
  float ANGLE_SENESCENT_DEG;
  float SHRIVEL_ANGLE_DEG;
  float SHRIVEL_ARCH_RAD;
  float DECLINE_SPLAY_DEG;
};
uniform DeclineParams u_decline;
uniform float u_collarBend;   // GeometryParams.collar_bend

const float MAX_DEATH_OFFSET = 0.95;
const float DEG_TO_RAD = 0.017453292519943295;

float smoothstep01(float x) {
  x = clamp(x, 0.0, 1.0);
  return x * x * (3.0 - 2.0 * x);
}

float deathPoint(float age, float draw) {
  float offset = clamp(u_decline.DECLINE_AGE_SPREAD * min(age / u_decline.DECLINE_AGE_REF, 1.0)
    + u_decline.DECLINE_JITTER * draw, 0.0, MAX_DEATH_OFFSET);
  return u_decline.V_DEAD + (1.0 - u_decline.V_DEAD) * offset;
}

float leafHealth(float sen, float v, float age, float draw) {
  float vk = deathPoint(age, draw);
  return min(1.0 - sen, clamp((v - vk) / (1.0 - vk), 0.0, 1.0));
}

struct Look {
  float sizeScale;
  float extraAngle;
  float extraArch;
  float browning;
};

Look leafLook(float h) {
  float shrivel = smoothstep01(1.0 - h / u_decline.SHRIVEL_FRACTION);
  return Look(
    1.0 + (u_decline.DECLINE_RESIDUAL - 1.0) * shrivel,
    (u_decline.ANGLE_SENESCENT_DEG * (1.0 - h) + u_decline.SHRIVEL_ANGLE_DEG * shrivel) * DEG_TO_RAD,
    u_decline.SHRIVEL_ARCH_RAD * shrivel,
    1.0 - h);
}

float plantFade(float v) {
  return clamp((u_decline.V_DEAD - v) / u_decline.V_DEAD, 0.0, 1.0);
}

float tillerLeanRad(float v) {
  return u_decline.DECLINE_SPLAY_DEG * DEG_TO_RAD * smoothstep01((1.0 - v) / (1.0 - u_decline.V_DEAD));
}

float leafOpacity(float fade, float v) {
  return (1.0 - fade) * (1.0 - plantFade(v));
}

float ipow(float x, int n) {
  float r = 1.0;
  for (int k = 0; k < n; k++) r *= x;
  return r;
}

// Integral over [0, x] of smoothstep(u)^i u^n (smoothstep(u) = 3 u^2 - 2 u^3), i <= 3.
float smoothPowerIntegral(int i, int n, float x) {
  float m = float(n);
  if (i == 0) return ipow(x, n + 1) / (m + 1.0);
  if (i == 1) return 3.0 * ipow(x, n + 3) / (m + 3.0) - 2.0 * ipow(x, n + 4) / (m + 4.0);
  if (i == 2) {
    return 9.0 * ipow(x, n + 5) / (m + 5.0) - 12.0 * ipow(x, n + 6) / (m + 6.0) + 4.0 * ipow(x, n + 7) / (m + 7.0);
  }
  return 27.0 * ipow(x, n + 7) / (m + 7.0) - 54.0 * ipow(x, n + 8) / (m + 8.0)
    + 36.0 * ipow(x, n + 9) / (m + 9.0) - 8.0 * ipow(x, n + 10) / (m + 10.0);
}

// _profile_moment: mean over u in [0, s] of smoothstep(u / c)^i u^(2 j), s > 0.
float profileMoment(int i, int j, float s, float c) {
  int n = 2 * j;
  float total = ipow(c, n + 1) * smoothPowerIntegral(i, n, min(s, c) / c);
  if (s > c) total += (ipow(s, n + 1) - ipow(c, n + 1)) / float(n + 1);
  return total / s;
}

// The nine profile moments m(i, j) that bend_cumulants uses, for one s; shared by the healthy and declined sets.
struct Moments {
  float m10;
  float m01;
  float m20;
  float m11;
  float m02;
  float m30;
  float m21;
  float m12;
  float m03;
};

Moments profileMoments(float s, float c) {
  return Moments(
    profileMoment(1, 0, s, c), profileMoment(0, 1, s, c),
    profileMoment(2, 0, s, c), profileMoment(1, 1, s, c), profileMoment(0, 2, s, c),
    profileMoment(3, 0, s, c), profileMoment(2, 1, s, c), profileMoment(1, 2, s, c), profileMoment(0, 3, s, c));
}

// bend_cumulants: mean, variance and third central moment of the midrib angle over [0, s], s > 0.
vec3 bendCumulants(float a, float b, Moments m) {
  float e1 = a * m.m10 + b * m.m01;
  float e2 = a * a * m.m20 + 2.0 * a * b * m.m11 + b * b * m.m02;
  float e3 = a * a * a * m.m30 + 3.0 * a * a * b * m.m21 + 3.0 * a * b * b * m.m12 + b * b * b * m.m03;
  return vec3(e1, e2 - e1 * e1, e3 - 3.0 * e1 * e2 + 2.0 * e1 * e1 * e1);
}

// chord_change: (turn rad, length factor) of the midrib chord from the collar to s when the bend grows.
// (0, 1) at s = 0, as in Python (both cumulant sets are 0 there).
vec2 chordChange(float angle, float arch, float extraAngle, float extraArch, float s, float c) {
  if (s <= 0.0) return vec2(0.0, 1.0);
  Moments m = profileMoments(s, c);
  vec3 h = bendCumulants(angle, arch, m);
  vec3 d = bendCumulants(angle + extraAngle, arch + extraArch, m);
  return vec2((d.x - d.z / 6.0) - (h.x - h.z / 6.0), exp(-(d.y - h.y) / 2.0));
}

struct Declined {
  vec3 position;
  vec3 normal;
  vec3 pivot;
  vec3 axis;
  float sizeScale;
  float health;
  float browning;
  float opacity;
};

// declined_vertex, plus the normal turned by the blade's chord turn and the tiller lean (Python leaves normals).
Declined declinedVertex(vec3 position, vec3 normal, bool blade, float s, vec3 collar, vec3 side, vec3 axis,
                        vec3 tillerBase, vec3 tillerAxis, float tillerAz, bool leans, float age, float draw,
                        float sen, float fade, float arch, float v) {
  float h = leafHealth(clamp(sen, 0.0, 1.0), v, max(age, 0.0), clamp(draw, -1.0, 1.0));
  Look lk = leafLook(h);
  vec3 drawn;
  vec3 n = normal;
  vec3 pivot;
  vec3 outAxis;
  if (!blade) {
    drawn = tillerBase + (position - tillerBase) * lk.sizeScale;
    pivot = tillerBase;
    outAxis = tillerAxis;
  } else {
    vec3 newCollar = tillerBase + (collar - tillerBase) * lk.sizeScale;
    // A healthy leaf (no extra angle or arch) has chord change (0, 1) exactly; skip the moments.
    vec2 chord = vec2(0.0, 1.0);
    if (lk.extraAngle != 0.0 || lk.extraArch != 0.0) {
      float angle = acos(clamp(dot(axis, tillerAxis), -1.0, 1.0));
      chord = chordChange(angle, arch, lk.extraAngle, lk.extraArch, clamp(s, 0.0, 1.0), u_collarBend);
    }
    vec3 offset = (position - collar) * (lk.sizeScale * chord.y);
    drawn = newCollar + rotateAbout(offset, side, chord.x);
    n = rotateAbout(n, side, chord.x);
    pivot = newCollar;
    outAxis = rotateAbout(axis, side, lk.extraAngle);
  }
  float lean = leans ? tillerLeanRad(v) : 0.0;
  if (lean != 0.0) {
    vec3 leanAxis = vec3(-sin(tillerAz), cos(tillerAz), 0.0);
    drawn = tillerBase + rotateAbout(drawn - tillerBase, leanAxis, lean);
    pivot = tillerBase + rotateAbout(pivot - tillerBase, leanAxis, lean);
    outAxis = rotateAbout(outAxis, leanAxis, lean);
    n = rotateAbout(n, leanAxis, lean);
  }
  return Declined(drawn, n, pivot, outAxis, lk.sizeScale, h, lk.browning, leafOpacity(clamp(fade, 0.0, 1.0), v));
}

// ==== END decline port ====

vec3 octDecode(ivec2 e) {
  vec2 f = max(vec2(e) / SNORM16, vec2(-1.0));
  float z = 1.0 - abs(f.x) - abs(f.y);
  if (z < 0.0) {
    f = (1.0 - abs(f.yx)) * vec2(f.x >= 0.0 ? 1.0 : -1.0, f.y >= 0.0 ? 1.0 : -1.0);
  }
  return normalize(vec3(f, z));
}

// Texel of vertex vid at a stage (vat-textures.mjs vertexTextureLayout).
ivec2 vertexTexel(int vid, int stage) {
  return ivec2(vid % u_vertexRowWidth, stage * u_vertexRowsPerStage + vid / u_vertexRowWidth);
}

vec4 organStage(int organ, int layer, int i0, int i1, float w) {
  return mix(texelFetch(u_organStage, ivec3(organ, i0, layer), 0),
             texelFetch(u_organStage, ivec3(organ, i1, layer), 0), w);
}

vec4 organStatic(int organ, int row) {
  return texelFetch(u_organStatic, ivec2(organ, row), 0);
}

void main() {
  int organ = int(a_organ);
  bool blade = a_kind == 1u;
  float s = a_s;
  float v = clamp(i_growth.y, 0.0, 1.0);
#ifdef CAPTURE
  float t = u_captureT;
#else
  float t = clamp(i_growth.x, 0.0, 1.0) * u_tEnd;
#endif
  vec4 life = organStatic(organ, 0);        // appear, vanish, draw, phase
  vec4 events = organStatic(organ, 1);      // senescent_time, senescence_duration, death_time, tiller
  float fade = clamp((t - events.z) / u_fadeDuration, 0.0, 1.0);

#ifndef CAPTURE
  // G53: a hidden organ's vertices go outside the clip volume before any other fetch, so its triangles are clipped
  // away cheaply. The capture program keeps every vertex.
  if (t < life.x || t >= life.y || leafOpacity(fade, v) <= 0.0) {
    v_normal = vec3(0.0, 0.0, 1.0);
    v_kind = 0.0;
    v_s = 0.0;
    v_browning = 0.0;
    v_opacity = 0.0;
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    return;
  }
#endif

  // The stage pair comes with the instance (vat-textures.mjs stagePair of t).
  int last = u_stageCount - 1;
  int i0 = clamp(int(i_growth.z + 0.5), 0, last);
  int i1 = min(i0 + 1, last);
  float w = clamp(i_growth.w, 0.0, 1.0);
  int vid = gl_VertexID;
  ivec2 at0 = vertexTexel(vid, i0);
  ivec2 at1 = vertexTexel(vid, i1);
  vec3 position = mix(texelFetch(u_position, at0, 0).xyz, texelFetch(u_position, at1, 0).xyz, w);
  vec3 n0 = octDecode(texelFetch(u_normal, at0, 0).xy);
  vec3 nb = mix(n0, octDecode(texelFetch(u_normal, at1, 0).xy), w);
  vec3 normal = length(nb) > 1e-6 ? normalize(nb) : n0;

  vec4 collarArch = organStage(organ, 0, i0, i1, w);
  vec4 directionSheath = organStage(organ, 1, i0, i1, w);
  float emergence = organStage(organ, 2, i0, i1, w).x;
  vec4 baseAz = organStatic(organ, 2);      // tiller_base, tiller_az
  vec3 tillerAxis = organStatic(organ, 3).xyz;
  vec3 side = organStatic(organ, 4).xyz;

  // Rebuild rules of the grass profile.
  float age = t - life.x;
  float sen = clamp((t - events.x) / events.y, 0.0, 1.0);
  vec3 dir = directionSheath.xyz;
  vec3 axis = blade ? (length(dir) > 1e-6 ? normalize(dir) : tillerAxis) : tillerAxis;
  float stemT = blade ? emergence / u_stemHeight : directionSheath.w * s / u_stemHeight;
  float phase = life.w;

  Declined dv = declinedVertex(position, normal, blade, s, collarArch.xyz, side, axis, baseAz.xyz, tillerAxis,
    baseAz.w, events.w > 0.5, age, life.z, sen, fade, collarArch.w, v);

#ifdef CAPTURE
  tf_position = dv.position;
  tf_pivot = dv.pivot;
  tf_axis = dv.axis;
  tf_browning = dv.browning;
  tf_opacity = dv.opacity;
#endif

  // Wind on the declined mesh (wind/renderer.mjs, G60).
  float cs = cos(i_place.z);
  float sn = sin(i_place.z);
  mat2 toLocal = mat2(cs, -sn, sn, cs);
  vec2 windL = toLocal * i_motion.zw;
  float speed = length(windL);
  vec3 windDir = speed > 1e-4 ? vec3(windL / speed, 0.0) : vec3(1.0, 0.0, 0.0);
  float drive = tanh(speed / u_speedRef) * mix(1.0, u_dryWindResponse, 1.0 - dv.health);
  vec3 p = dv.position;
  vec3 n = dv.normal;

  if (blade) {
    vec3 A = normalize(dv.axis);
    vec3 pivot = dv.pivot;
    // Flip and flutter: twist about the leaf's base axis, growing over the first half of the blade.
    vec3 flipSide = cross(vec3(0.0, 0.0, 1.0), A);
    float flipSign = (length(flipSide) > 1e-3 && dot(flipSide, windDir) < 0.0) ? -1.0 : 1.0;
    float flip = 3.14159265 * smoothstep(u_flip.x, u_flip.y, speed) * flipSign;
    float flutter = u_flutter * drive * sin(u_time * TAU * u_flutterHz * (0.8 + 0.4 * phase) + phase * TAU);
    float twist = (flip + flutter) * smoothstep(0.0, 0.5, s);
    p = pivot + rotateAbout(p - pivot, A, twist);
    n = rotateAbout(n, A, twist);
    // Sway: bend toward the wind about the pivot, progressively along the blade.
    vec3 K = cross(A, windDir);
    float kLen = length(K);
    if (kLen > 1e-3) {
      K /= kLen;
      float osc = 0.75 + 0.25 * sin(u_time * TAU * u_leafSwayHz * (0.8 + 0.4 * phase) + phase * TAU);
      float bend = u_leafBend * drive * osc * s;
      p = pivot + rotateAbout(p - pivot, K, bend);
      n = rotateAbout(n, K, bend);
    }
  }

  // Stem arc from the spring lean; each vertex rides with the stem point at its declined attachment height.
  vec2 leanL = toLocal * i_motion.xy;
  float phi = length(leanL);
  if (phi > 1e-5) {
    float stemTd = stemT * dv.sizeScale;
    vec3 ld = vec3(leanL / phi, 0.0);
    vec3 B = vec3(-ld.y, ld.x, 0.0);
    float h = stemTd * u_stemHeight;
    float x = phi * stemTd;
    float arcSide = x > 1e-4 ? h * (1.0 - cos(x)) / x : 0.5 * h * x;
    float arcUp = x > 1e-4 ? h * sin(x) / x : h;
    p = ld * arcSide + vec3(0.0, 0.0, arcUp) + rotateAbout(p - vec3(0.0, 0.0, h), B, x);
    n = rotateAbout(n, B, x);
  }

  vec3 pl = p * i_place.w;
  vec3 world = vec3(cs * pl.x - sn * pl.y + i_place.x, sn * pl.x + cs * pl.y + i_place.y, pl.z);
  v_normal = vec3(cs * n.x - sn * n.y, sn * n.x + cs * n.y, n.z);
  v_kind = blade ? 1.0 : 0.0;
  v_s = s;
  v_browning = dv.browning;
  v_opacity = dv.opacity;
  gl_Position = u_viewProj * vec4(world, 1.0);
}
`;

/**
 * The vertex shader source; `capture` adds the transform feedback outputs and takes `t` from `u_captureT`.
 * @param {{capture?: boolean}} [options]
 * @returns {string}
 */
export function grassVertexShader({ capture = false } = {}) {
  return `#version 300 es\n${capture ? '#define CAPTURE\n' : ''}${VERTEX_BODY}`;
}

export const GRASS_FRAGMENT_SHADER = /* glsl */ `#version 300 es
precision highp float;

in vec3 v_normal;
flat in float v_kind;
in float v_s;
in float v_browning;
in float v_opacity;

uniform vec3 u_lightDir;  // toward the light, arena axes

out vec4 outColor;

const vec3 LEAF_TOP = vec3(0.16, 0.42, 0.10);
const vec3 LEAF_UNDER = vec3(0.55, 0.66, 0.42);
const vec3 STEM = vec3(0.42, 0.52, 0.20);
const vec3 BROWN_TOP = vec3(0.55, 0.42, 0.22);
const vec3 BROWN_UNDER = vec3(0.70, 0.60, 0.40);
const vec3 BROWN_STEM = vec3(0.62, 0.50, 0.30);

// Interleaved gradient noise: a stable per-pixel threshold in [0, 1) for the dithered fade (G54).
float ign(vec2 p) {
  return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715))));
}

void main() {
  if (v_opacity < 1.0 && v_opacity <= ign(gl_FragCoord.xy)) discard;
  vec3 n = normalize(v_normal);
  if (!gl_FrontFacing) n = -n;
  bool sheath = v_kind < 0.5;
  // Leaf faces are wound so their normals point to the underside (away from the sky at rest).
  vec3 green = sheath ? STEM : (gl_FrontFacing ? LEAF_UNDER : LEAF_TOP);
  vec3 brown = sheath ? BROWN_STEM : (gl_FrontFacing ? BROWN_UNDER : BROWN_TOP);
  vec3 albedo = mix(green, brown, clamp(v_browning, 0.0, 1.0));
  float light = 0.35 + 0.65 * max(dot(n, u_lightDir), 0.0);
  float occlusion = mix(0.55, 1.0, smoothstep(0.0, 0.35, v_s));
  outColor = vec4(albedo * light * occlusion, 1.0);
}
`;

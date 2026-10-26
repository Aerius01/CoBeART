// GLSL ES 3.00 sources for the Glade renderer (BRIEF.md "Rendering", GL grill G23 to G27).
//
// Arena to clip space (D18, stretch to fill): ndc = 1 - 2 * (p - arenaMin) / arenaSpan on both axes, so
// x_max lands on the left edge, x_min on the right, y_min at the top and y_max at the bottom.
// Screen right is arena -x and screen down is arena +y, so a quad corner's arena offset is not its screen
// offset: the vertex shader picks the atlas u from the arena -x side so the art is never mirrored on screen.

/** Attribute locations shared by the plant program and the instance VAO. */
export const PLANT_ATTRIB = Object.freeze({ position: 0, seed: 1, frameDirection: 2 });

/** Vertices per plant instance: a halo quad (6) then a sprite quad (6). */
export const VERTICES_PER_INSTANCE = 12;

/**
 * Plant vertex shader. One instance per plant, gl_VertexID 0..5 the halo quad and 6..11 the sprite quad.
 * Instance attributes: position in arena mm (f32 x2), seed (u32), frame | direction << 16 (u32).
 */
export const PLANT_VERTEX_SHADER = /* glsl */ `#version 300 es
precision highp float;
precision highp int;

layout(location = 0) in vec2 a_position;
layout(location = 1) in uint a_seed;
layout(location = 2) in uint a_frameDirection;

uniform vec2 u_arenaMin;        // arena (x_min, y_min) in mm
uniform vec2 u_arenaSpan;       // arena (x_max - x_min, y_max - y_min) in mm
uniform float u_spriteSize;     // sprite_size_mm
uniform float u_sizeJitter;     // size_jitter, fraction
uniform float u_brightnessJitter; // brightness_jitter, fraction
uniform float u_shadowScale;    // shadow_scale, halo size relative to the sprite
uniform vec2 u_shadowOffset;    // halo offset in arena mm (G26), precomputed on the CPU
uniform int u_nFrames;          // frames per sprite set in the bound atlas

out vec2 v_uv;
flat out float v_layer;
flat out float v_brightness;
flat out int v_isHalo;

// Two triangles covering the unit square, in arena-oriented local axes (+x arena +x, +y arena +y).
const vec2 CORNERS[6] = vec2[6](
  vec2(0.0, 0.0), vec2(1.0, 0.0), vec2(0.0, 1.0),
  vec2(0.0, 1.0), vec2(1.0, 0.0), vec2(1.0, 1.0));

// PCG hash (O'Neill; Jarzynski and Olano 2020): well-mixed 32-bit output from a 32-bit input.
uint pcg(uint v) {
  uint state = v * 747796405u + 2891336453u;
  uint word = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u;
  return (word >> 22u) ^ word;
}

// Top 24 bits to a float in [0, 1), exact in single precision.
float unit01(uint h) {
  return float(h >> 8u) * (1.0 / 16777216.0);
}

void main() {
  int quad = gl_VertexID / 6;           // 0 halo, 1 sprite (the halo is skipped by starting at vertex 6)
  vec2 corner = CORNERS[gl_VertexID % 6];

  uint h0 = pcg(a_seed);
  uint h1 = pcg(h0);
  uint h2 = pcg(h1);
  float angle = unit01(h0) * 6.28318530718;
  float size = u_spriteSize * (1.0 + u_sizeJitter * (2.0 * unit01(h1) - 1.0));
  float brightness = 1.0 + u_brightnessJitter * (2.0 * unit01(h2) - 1.0);

  bool halo = quad == 0;
  float extent = halo ? size * u_shadowScale : size;
  float c = cos(angle);
  float s = sin(angle);
  vec2 local = mat2(c, s, -s, c) * ((corner - 0.5) * extent);
  vec2 p = a_position + local + (halo ? u_shadowOffset : vec2(0.0));

  gl_Position = vec4(1.0 - 2.0 * (p - u_arenaMin) / u_arenaSpan, 0.0, 1.0);

  int frame = int(a_frameDirection & 0xFFFFu);
  int direction = int(a_frameDirection >> 16u);
  // Atlas uv (0, 0) is the art's top-left. Art right (u+) must go to screen right, which is arena -x under
  // D18, and art down (v+) to screen down, which is arena +y; hence u = 1 - corner.x, v = corner.y.
  v_uv = vec2(1.0 - corner.x, corner.y);
  v_layer = float(direction * u_nFrames + frame);
  v_brightness = brightness;
  v_isHalo = halo ? 1 : 0;
}
`;

/**
 * Plant fragment shader. The atlas is premultiplied; output is premultiplied for blendFunc(ONE, ONE_MINUS_SRC_ALPHA).
 * Halo: the sprite's alpha sampled with LOD bias u_shadowBlur, drawn black at u_shadowStrength.
 */
export const PLANT_FRAGMENT_SHADER = /* glsl */ `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2DArray;

uniform sampler2DArray u_atlas;
uniform float u_shadowStrength; // shadow_strength, opacity
uniform float u_shadowBlur;     // shadow_blur, mip LOD bias

in vec2 v_uv;
flat in float v_layer;
flat in float v_brightness;
flat in int v_isHalo;

out vec4 outColor;

void main() {
  vec3 coord = vec3(v_uv, v_layer);
  if (v_isHalo == 1) {
    // Branch is uniform per primitive (flat varying), so implicit derivatives stay valid.
    float a = texture(u_atlas, coord, u_shadowBlur).a * u_shadowStrength;
    outColor = vec4(0.0, 0.0, 0.0, a);
  } else {
    vec4 c = texture(u_atlas, coord);
    // Brightness scales RGB only (G27); capping at alpha keeps the colour validly premultiplied.
    outColor = vec4(min(c.rgb * v_brightness, vec3(c.a)), c.a);
  }
}
`;

/** Heatmap vertex shader: one full-screen triangle from gl_VertexID, no attributes. */
export const HEATMAP_VERTEX_SHADER = /* glsl */ `#version 300 es
precision highp float;

out vec2 v_cell;

void main() {
  vec2 ndc = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);
  // Inverse of the D18 map: (p - arenaMin) / arenaSpan = (1 - ndc) / 2, i.e. the grid texture coordinate
  // whose s follows arena x (ix) and t follows arena y (iy, the texture row).
  v_cell = (1.0 - ndc) * 0.5;
  gl_Position = vec4(ndc, 0.0, 1.0);
}
`;

/** Heatmap fragment shader: density (E/m²) on a ramp from 0 to u_eCap, magenta tint above the cap. */
export const HEATMAP_FRAGMENT_SHADER = /* glsl */ `#version 300 es
precision highp float;
precision highp sampler2D;

uniform sampler2D u_density; // R32F, NEAREST, one texel per cell
uniform float u_eCap;        // E_cap in E/m²

in vec2 v_cell;
out vec4 outColor;

const vec3 RAMP_LOW = vec3(0.04, 0.08, 0.32);
const vec3 RAMP_MID = vec3(0.06, 0.50, 0.50);
const vec3 RAMP_HIGH = vec3(0.95, 0.88, 0.30);
const vec3 OVER_LOW = vec3(0.70, 0.08, 0.55);
const vec3 OVER_HIGH = vec3(1.00, 0.50, 0.95);

void main() {
  float d = max(texture(u_density, v_cell).r, 0.0);
  float t = clamp(d / u_eCap, 0.0, 1.0);
  // Black at zero so an unpowered floor stays black, then blue, teal and yellow at the cap.
  vec3 ramp = t < 0.5
    ? mix(RAMP_LOW * smoothstep(0.0, 0.15, t), RAMP_MID, smoothstep(0.15, 0.5, t))
    : mix(RAMP_MID, RAMP_HIGH, (t - 0.5) * 2.0);
  float over = clamp((d - u_eCap) / u_eCap, 0.0, 1.0);
  outColor = vec4(d > u_eCap ? mix(OVER_LOW, OVER_HIGH, over) : ramp, 1.0);
}
`;

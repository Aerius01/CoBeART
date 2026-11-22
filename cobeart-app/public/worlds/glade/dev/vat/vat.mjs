// VAT loader (format: vat_format.md): parses a cobeart-vat GLB into typed arrays ready for a GPU upload. No dependencies.

const GLB_MAGIC = 0x46546c67; // 'glTF'
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;
const FORMAT = 'cobeart-vat';
const VERSION = 1;
const SNORM16 = 32767;
const F16_MAX = 65504;

const DOMAINS = ['vertex', 'organ'];
const TIMINGS = ['static', 'stage'];
/** Typed array and bytes per stored value (oct16 stores 2 bytes per int16, 2 int16 per xyz triple). */
const STORAGE = {
  f32: { Array: Float32Array, bytes: 4 },
  f16: { Array: Uint16Array, bytes: 2 },
  u32: { Array: Uint32Array, bytes: 4 },
  oct16: { Array: Int16Array, bytes: 2 },
};

/**
 * @typedef {object} VatBlock
 * @property {string} name
 * @property {'vertex' | 'organ'} domain
 * @property {'static' | 'stage'} timing
 * @property {number} components
 * @property {'f32' | 'f16' | 'u32' | 'oct16'} storage
 * @property {Float32Array | Uint16Array | Int16Array | Uint32Array} raw the stored array: f16 as bits, oct16 as int16 pairs
 */

/**
 * @typedef {object} Vat
 * @property {string} species
 * @property {Record<string, unknown>} speciesParams
 * @property {number} vertexCount
 * @property {number} organCount
 * @property {string[]} organIds
 * @property {number[]} stageTimes
 * @property {number} previewStage
 * @property {number} never value that stands for an event that never happens
 * @property {Uint32Array} indices triangle indices
 * @property {Map<string, VatBlock>} blocks
 */

/**
 * Float16 bits to a number.
 * @param {number} bits
 * @returns {number}
 */
export function f16ToNumber(bits) {
  const sign = bits & 0x8000 ? -1 : 1;
  const exponent = (bits >> 10) & 0x1f;
  const fraction = bits & 0x3ff;
  if (exponent === 0) return sign * fraction * 2 ** -24;
  if (exponent === 0x1f) return fraction ? NaN : sign * Infinity;
  return sign * (1 + fraction / 1024) * 2 ** (exponent - 15);
}

/**
 * Octahedral int16 snorm pair to a unit vector.
 * @param {number} i
 * @param {number} j
 * @returns {[number, number, number]}
 */
export function octToUnit(i, j) {
  let x = Math.max(-1, i / SNORM16);
  let y = Math.max(-1, j / SNORM16);
  const z = 1 - Math.abs(x) - Math.abs(y);
  if (z < 0) {
    const nx = (1 - Math.abs(y)) * (x >= 0 ? 1 : -1);
    y = (1 - Math.abs(x)) * (y >= 0 ? 1 : -1);
    x = nx;
  }
  const norm = Math.hypot(x, y, z);
  return [x / norm, y / norm, z / norm];
}

/**
 * @param {ArrayBuffer} buffer
 * @returns {{gltf: any, binOffset: number, binLength: number}}
 */
function parseContainer(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 20) {
    throw new Error(`vat: buffer is ${buffer?.byteLength ?? 'not an ArrayBuffer'} bytes, too short for a GLB header`);
  }
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(4, true) !== 2) {
    throw new Error('vat: not a glTF 2.0 binary (bad magic or version)');
  }
  const total = view.getUint32(8, true);
  if (total !== buffer.byteLength) {
    throw new Error(`vat: header declares ${total} bytes but the buffer has ${buffer.byteLength} (truncated or padded file)`);
  }
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== CHUNK_JSON || 28 + jsonLength > total) {
    throw new Error('vat: first chunk is not a JSON chunk that fits the file');
  }
  const binStart = 20 + jsonLength;
  const binLength = view.getUint32(binStart, true);
  if (view.getUint32(binStart + 4, true) !== CHUNK_BIN || binStart + 8 + binLength > total) {
    throw new Error('vat: second chunk is not a BIN chunk that fits the file');
  }
  let gltf;
  try {
    gltf = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)));
  } catch (error) {
    throw new Error(`vat: JSON chunk is not valid JSON (${error.message})`);
  }
  return { gltf, binOffset: binStart + 8, binLength };
}

/**
 * Parses a VAT GLB. Typed arrays are views onto `arrayBuffer`.
 * @param {ArrayBuffer} arrayBuffer
 * @returns {Vat}
 */
export function parseVat(arrayBuffer) {
  const { gltf, binOffset, binLength } = parseContainer(arrayBuffer);
  const header = gltf.extras?.vat;
  if (!header || header.format !== FORMAT) throw new Error(`vat: extras.vat.format must be '${FORMAT}'`);
  if (header.version !== VERSION) {
    throw new Error(`vat: extras.vat.version is ${header.version}; this loader supports ${VERSION}`);
  }
  const { vertex_count: vertexCount, organ_count: organCount } = header;
  const stageCount = header.stage_times.length;
  if (!(stageCount > 0) || !header.stage_times.every((t, k) => Number.isFinite(t) && (k === 0 || t > header.stage_times[k - 1]))) {
    throw new Error('vat: stage_times must be a non-empty, finite, strictly increasing list');
  }
  if (!Number.isInteger(header.preview_stage) || header.preview_stage < 0 || header.preview_stage >= stageCount) {
    throw new Error(`vat: preview_stage ${header.preview_stage} must be an index below ${stageCount}`);
  }
  if (header.organ_ids.length !== organCount) {
    throw new Error(`vat: organ_count ${organCount} disagrees with organ_ids (${header.organ_ids.length})`);
  }

  /** The typed array over bufferView `index`, whose byte length must be exactly `expected` bytes. */
  const viewOf = (index, expected, Ctor, label) => {
    const bufferView = gltf.bufferViews?.[index];
    if (!bufferView) throw new Error(`vat: ${label}: bufferView ${index} does not exist`);
    if (bufferView.byteLength !== expected) {
      throw new Error(`vat: ${label}: bufferView ${index} is ${bufferView.byteLength} bytes, header implies ${expected}`);
    }
    const offset = bufferView.byteOffset ?? 0;
    if (offset % 4 || offset + expected > binLength) {
      throw new Error(`vat: ${label}: bufferView ${index} (offset ${offset}, ${expected} bytes) is misaligned or outside the ${binLength}-byte BIN chunk`);
    }
    return new Ctor(arrayBuffer, binOffset + offset, expected / Ctor.BYTES_PER_ELEMENT);
  };

  /** @type {Map<string, VatBlock>} */
  const blocks = new Map();
  for (const entry of header.blocks) {
    const { name, domain, timing, components, storage } = entry;
    if (blocks.has(name)) throw new Error(`vat: block '${name}' is listed twice`);
    if (!DOMAINS.includes(domain)) throw new Error(`vat: block '${name}': domain '${domain}' must be one of ${DOMAINS}`);
    if (!TIMINGS.includes(timing)) throw new Error(`vat: block '${name}': timing '${timing}' must be one of ${TIMINGS}`);
    if (!STORAGE[storage]) throw new Error(`vat: block '${name}': storage '${storage}' must be one of ${Object.keys(STORAGE)}`);
    if (!Number.isInteger(components) || components < 1 || components > 4) {
      throw new Error(`vat: block '${name}': components ${components} must be an integer in 1..4`);
    }
    if (storage === 'oct16' && components !== 3) throw new Error(`vat: block '${name}': oct16 requires components 3, got ${components}`);
    const count = domain === 'vertex' ? vertexCount : organCount;
    const values = count * components * (timing === 'stage' ? stageCount : 1);
    const { Array: Ctor, bytes } = STORAGE[storage];
    const stored = storage === 'oct16' ? (values / 3) * 2 : values;
    const raw = viewOf(entry.bufferView, stored * bytes, Ctor, `block '${name}'`);
    blocks.set(name, { name, domain, timing, components, storage, raw });
  }

  const organ = blocks.get('organ');
  const position = blocks.get('position');
  if (!organ || !position) throw new Error(`vat: required block '${organ ? 'position' : 'organ'}' is missing`);
  if (organ.domain !== 'vertex' || organ.timing !== 'static' || organ.components !== 1 || organ.storage !== 'u32') {
    throw new Error("vat: block 'organ' must be vertex, static, 1 component, u32");
  }
  if (position.domain !== 'vertex' || position.timing !== 'stage' || position.components !== 3 || !['f32', 'f16'].includes(position.storage)) {
    throw new Error("vat: block 'position' must be vertex, stage, 3 components, f32 or f16");
  }
  organ.raw.forEach((o, v) => {
    if (o >= organCount) throw new Error(`vat: block 'organ' element ${v}: organ index ${o} is not below organ_count ${organCount}`);
  });
  for (const block of blocks.values()) checkFinite(block, vertexCount, organCount, stageCount);

  const primitive = gltf.meshes?.[0]?.primitives?.[0];
  const accessor = gltf.accessors?.[primitive?.indices];
  if (!accessor) throw new Error('vat: the mesh primitive has no indices accessor');
  const indices = viewOf(accessor.bufferView, accessor.count * 4, Uint32Array, 'indices');
  if (indices.length === 0 || indices.length % 3) throw new Error(`vat: indices has ${indices.length} entries; need a non-empty multiple of 3`);
  indices.forEach((index, k) => {
    if (index >= vertexCount) throw new Error(`vat: indices[${k}] = ${index} is not below vertex_count ${vertexCount}`);
  });

  return {
    species: header.species,
    speciesParams: header.species_params,
    vertexCount,
    organCount,
    organIds: header.organ_ids,
    stageTimes: header.stage_times,
    previewStage: header.preview_stage,
    never: header.never,
    indices,
    blocks,
  };
}

/**
 * Throws for a non-finite stored float, naming block, stage and element.
 * @param {VatBlock} block
 * @param {number} vertexCount
 * @param {number} organCount
 * @param {number} stageCount
 */
function checkFinite(block, vertexCount, organCount, stageCount) {
  if (block.storage !== 'f32' && block.storage !== 'f16') return;
  const perStage = (block.domain === 'vertex' ? vertexCount : organCount) * block.components;
  const bad = block.storage === 'f32'
    ? block.raw.findIndex((v) => !Number.isFinite(v))
    : block.raw.findIndex((bits) => (bits & 0x7c00) === 0x7c00);
  if (bad >= 0) {
    const element = Math.floor((bad % perStage) / block.components);
    const stage = block.timing === 'stage' ? `stage ${Math.floor(bad / perStage)}, ` : '';
    throw new Error(`vat: block '${block.name}' ${stage}element ${element}: stored value is not finite`);
  }
}

/**
 * Decodes a block: f16 to floats, oct16 to unit xyz triples, f32 and u32 as stored.
 * @param {VatBlock} block
 * @returns {Float32Array | Uint32Array}
 */
export function decodeBlock(block) {
  const { name, storage, raw } = block;
  if (storage === 'f32' || storage === 'u32') return raw;
  if (storage === 'f16') {
    const out = new Float32Array(raw.length);
    for (let k = 0; k < raw.length; k++) {
      const value = f16ToNumber(raw[k]);
      if (!Number.isFinite(value) || Math.abs(value) > F16_MAX) throw new Error(`vat: block '${name}' value ${k} is not finite`);
      out[k] = value;
    }
    return out;
  }
  if (storage === 'oct16') {
    const out = new Float32Array((raw.length / 2) * 3);
    for (let k = 0; k < raw.length / 2; k++) out.set(octToUnit(raw[2 * k], raw[2 * k + 1]), 3 * k);
    return out;
  }
  throw new Error(`vat: block '${name}' has unknown storage '${storage}'`);
}

/**
 * Fetches and parses a VAT file.
 * @param {string} url
 * @returns {Promise<Vat>}
 */
export async function loadVat(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`vat: GET ${url} returned ${response.status}`);
  return parseVat(await response.arrayBuffer());
}

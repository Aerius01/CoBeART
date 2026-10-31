// Minimal GLB reader for the wind prototype: one mesh, float vertex attributes, merged into a single indexed buffer.

const GLB_MAGIC = 0x46546c67; // 'glTF'
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;
const FLOAT = 5126;
const INDEX_ARRAYS = { 5121: Uint8Array, 5123: Uint16Array, 5125: Uint32Array };
const TYPE_SIZE = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };

/**
 * @typedef {object} MergedMesh
 * @property {number} vertexCount
 * @property {Map<string, {data: Float32Array, size: number}>} attributes glTF attribute name to packed data
 * @property {Float32Array} material per-vertex index into `materialNames`
 * @property {string[]} materialNames
 * @property {Uint32Array} indices
 */

/**
 * @param {object} gltf
 * @param {ArrayBuffer} bin
 * @param {number} index accessor index
 * @param {string} label for error messages
 * @returns {{data: Float32Array | Uint8Array | Uint16Array | Uint32Array, size: number}}
 */
function readAccessor(gltf, bin, index, label) {
  const acc = gltf.accessors[index];
  const view = gltf.bufferViews[acc.bufferView];
  const size = TYPE_SIZE[acc.type];
  const Ctor = acc.componentType === FLOAT ? Float32Array : INDEX_ARRAYS[acc.componentType];
  if (!Ctor || !size) throw new Error(`glb: ${label} has unsupported componentType ${acc.componentType} / type ${acc.type}`);
  const offset = (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
  const stride = view.byteStride ?? 0;
  const packed = size * Ctor.BYTES_PER_ELEMENT;
  if (stride && stride !== packed) {
    throw new Error(`glb: ${label} is interleaved (stride ${stride}); re-export without interleaving`);
  }
  return { data: new Ctor(bin.slice(offset, offset + acc.count * packed)), size };
}

/**
 * Loads a GLB and merges its first mesh's primitives; every primitive must carry the same attributes.
 * @param {string} url
 * @returns {Promise<MergedMesh>}
 */
export async function loadGlb(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`glb: GET ${url} returned ${response.status}`);
  const buffer = await response.arrayBuffer();
  const header = new DataView(buffer);
  if (header.getUint32(0, true) !== GLB_MAGIC || header.getUint32(4, true) !== 2) {
    throw new Error(`glb: ${url} is not a glTF 2.0 binary`);
  }
  const jsonLength = header.getUint32(12, true);
  if (header.getUint32(16, true) !== CHUNK_JSON) throw new Error(`glb: ${url} first chunk is not JSON`);
  const gltf = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)));
  const binStart = 20 + jsonLength;
  if (header.getUint32(binStart + 4, true) !== CHUNK_BIN) throw new Error(`glb: ${url} second chunk is not BIN`);
  const bin = buffer.slice(binStart + 8, binStart + 8 + header.getUint32(binStart, true));

  const primitives = gltf.meshes?.[0]?.primitives ?? [];
  if (primitives.length === 0) throw new Error(`glb: ${url} has no mesh primitives`);
  const names = Object.keys(primitives[0].attributes);
  /** @type {Map<string, {parts: Float32Array[], size: number}>} */
  const parts = new Map(names.map((n) => [n, { parts: [], size: 0 }]));
  const materialParts = [];
  const indexParts = [];
  let vertexCount = 0;
  for (const [pi, prim] of primitives.entries()) {
    const primNames = Object.keys(prim.attributes);
    if (primNames.length !== names.length || !names.every((n) => n in prim.attributes)) {
      throw new Error(`glb: primitive ${pi} attributes [${primNames}] differ from primitive 0 [${names}]`);
    }
    let count = 0;
    for (const name of names) {
      const { data, size } = readAccessor(gltf, bin, prim.attributes[name], `primitive ${pi} ${name}`);
      if (!(data instanceof Float32Array)) throw new Error(`glb: primitive ${pi} ${name} must be float`);
      const entry = /** @type {{parts: Float32Array[], size: number}} */ (parts.get(name));
      entry.parts.push(data);
      entry.size = size;
      count = data.length / size;
    }
    materialParts.push(new Float32Array(count).fill(prim.material ?? 0));
    const { data: idx } = readAccessor(gltf, bin, prim.indices, `primitive ${pi} indices`);
    indexParts.push(Uint32Array.from(idx, (i) => i + vertexCount));
    vertexCount += count;
  }

  const concat = (/** @type {ArrayLike<number>[]} */ arrays, /** @type {any} */ Ctor) => {
    const out = new Ctor(arrays.reduce((n, a) => n + a.length, 0));
    let o = 0;
    for (const a of arrays) { out.set(a, o); o += a.length; }
    return out;
  };
  return {
    vertexCount,
    attributes: new Map([...parts].map(([n, e]) => [n, { data: concat(e.parts, Float32Array), size: e.size }])),
    material: concat(materialParts, Float32Array),
    materialNames: (gltf.materials ?? []).map((/** @type {{name?: string}} */ m) => m.name ?? ''),
    indices: concat(indexParts, Uint32Array),
  };
}

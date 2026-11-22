// Glade VAT loader (public/worlds/glade/dev/vat/vat.mjs, an ES module) against the committed tiny fixture.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const VAT_DIR = path.join(__dirname, '..', 'public', 'worlds', 'glade', 'dev', 'vat');
const loadVat = () => import('../public/worlds/glade/dev/vat/vat.mjs');

/** The fixture as a fresh ArrayBuffer. */
const fixtureBuffer = () => {
  const bytes = fs.readFileSync(path.join(VAT_DIR, 'fixtures', 'tiny.vat.glb'));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
};

/** Rebuilds a GLB from an ArrayBuffer after `edit` changed its parsed JSON (the BIN chunk is kept). */
const withEditedHeader = (buffer, edit) => {
  const view = new DataView(buffer);
  const jsonLength = view.getUint32(12, true);
  const gltf = JSON.parse(Buffer.from(buffer, 20, jsonLength).toString('utf8'));
  edit(gltf);
  let text = Buffer.from(JSON.stringify(gltf), 'utf8');
  text = Buffer.concat([text, Buffer.alloc((4 - (text.length % 4)) % 4, 0x20)]);
  const bin = Buffer.from(buffer, 28 + jsonLength);
  const head = Buffer.alloc(20);
  head.writeUInt32LE(0x46546c67, 0);
  head.writeUInt32LE(2, 4);
  head.writeUInt32LE(28 + text.length + bin.length, 8);
  head.writeUInt32LE(text.length, 12);
  head.writeUInt32LE(0x4e4f534a, 16);
  const binHead = Buffer.alloc(8);
  binHead.writeUInt32LE(bin.length, 0);
  binHead.writeUInt32LE(0x004e4942, 4);
  const out = Buffer.concat([head, text, binHead, bin]);
  return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength);
};

test('fixture header fields', async () => {
  const { parseVat } = await loadVat();
  const vat = parseVat(fixtureBuffer());
  assert.equal(vat.species, 'tiny');
  assert.deepEqual(vat.speciesParams, { seed: 1, note: 'synthetic loader fixture' });
  assert.equal(vat.vertexCount, 12);
  assert.equal(vat.organCount, 2);
  assert.deepEqual(vat.organIds, ['1', '2']);
  assert.deepEqual(vat.stageTimes, [0, 1.5, 3]);
  assert.equal(vat.previewStage, 2);
  assert.equal(vat.never, 1.0e30);
  assert.ok(vat.indices instanceof Uint32Array);
  assert.equal(vat.indices.length, 24);
  assert.ok(vat.indices.every((i) => i < 12));
});

test('block shapes and typed-array kinds', async () => {
  const { parseVat } = await loadVat();
  const { blocks } = parseVat(fixtureBuffer());
  const expected = {
    position: ['vertex', 'stage', 3, 'f16', Uint16Array, 108],
    normal: ['vertex', 'stage', 3, 'oct16', Int16Array, 72],
    organ: ['vertex', 'static', 1, 'u32', Uint32Array, 12],
    uv: ['vertex', 'static', 2, 'f32', Float32Array, 24],
    s: ['vertex', 'static', 1, 'f16', Uint16Array, 12],
    birth: ['organ', 'static', 1, 'f32', Float32Array, 2],
    events: ['organ', 'static', 4, 'f32', Float32Array, 8],
    kind: ['organ', 'static', 1, 'u32', Uint32Array, 2],
    tilt: ['organ', 'static', 1, 'f16', Uint16Array, 2],
    arch: ['organ', 'stage', 1, 'f32', Float32Array, 6],
    collar: ['organ', 'stage', 3, 'f16', Uint16Array, 18],
  };
  assert.deepEqual([...blocks.keys()], Object.keys(expected));
  for (const [name, [domain, timing, components, storage, Ctor, length]] of Object.entries(expected)) {
    const block = blocks.get(name);
    assert.deepEqual([block.domain, block.timing, block.components, block.storage], [domain, timing, components, storage], name);
    assert.ok(block.raw instanceof Ctor, `${name} raw kind`);
    assert.equal(block.raw.length, length, `${name} raw length`);
  }
});

test('decoded values', async () => {
  const { parseVat, decodeBlock } = await loadVat();
  const { blocks, never } = parseVat(fixtureBuffer());
  assert.deepEqual(Array.from(decodeBlock(blocks.get('organ'))), [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]);
  assert.deepEqual(Array.from(decodeBlock(blocks.get('kind'))), [0, 1]);
  assert.deepEqual(Array.from(decodeBlock(blocks.get('birth'))), [0, 1.5]);
  assert.deepEqual(Array.from(decodeBlock(blocks.get('tilt'))), [0.5, -1.25]);
  const events = decodeBlock(blocks.get('events'));
  assert.deepEqual(Array.from(events.slice(0, 6)), [1, 0.5, 2, 2.5, 2, 1]);
  assert.equal(events[6], Math.fround(never));
  const position = decodeBlock(blocks.get('position'));
  assert.ok(position instanceof Float32Array && position.length === 108);
  // stage 2, organ 1, row 0, column 1 (vertex 7): x = 0.125 + 0.0625 * 3, z = 0.01 (float16 tolerance)
  const first = 2 * 36 + 7 * 3;
  assert.ok(Math.abs(position[first] - 0.3125) < 0.3125 * 2 ** -11);
  assert.ok(Math.abs(position[first + 2] - 0.01) < 0.01 * 2 ** -11);
  const normal = decodeBlock(blocks.get('normal'));
  assert.equal(normal.length, 108);
  for (let v = 0; v < 36; v++) {
    assert.ok(Math.abs(Math.hypot(normal[3 * v], normal[3 * v + 1], normal[3 * v + 2]) - 1) < 1e-6, `normal ${v} is unit`);
  }
  // stage 2 normals point down (z < 0), the sign-not-zero branch
  assert.ok(normal[2 * 36 + 2] < 0 && normal[2] > 0);
});

test('f16 and octahedral decoders', async () => {
  const { f16ToNumber, octToUnit } = await loadVat();
  assert.equal(f16ToNumber(0x3c00), 1);
  assert.equal(f16ToNumber(0xc000), -2);
  assert.equal(f16ToNumber(0x7bff), 65504);
  assert.equal(f16ToNumber(0x0001), 2 ** -24);
  assert.deepEqual(octToUnit(0, 0), [0, 0, 1]);
  const [x, y, z] = octToUnit(32767, 32767);
  assert.ok(Math.abs(x) < 1e-3 && Math.abs(y) < 1e-3 && z < -0.99, 'the corner decodes to straight down');
});

test('errors on a truncated buffer, a bad header and mismatched block lengths', async () => {
  const { parseVat } = await loadVat();
  const good = fixtureBuffer();
  assert.throws(() => parseVat(good.slice(0, good.byteLength - 8)), /truncated/);
  assert.throws(() => parseVat(good.slice(0, 10)), /too short/);
  assert.throws(() => parseVat(new ArrayBuffer(64)), /not a glTF 2\.0 binary/);
  assert.throws(
    () => parseVat(withEditedHeader(good, (g) => { g.extras.vat.format = 'other'; })), /extras\.vat\.format/);
  assert.throws(
    () => parseVat(withEditedHeader(good, (g) => { g.extras.vat.organ_count = 3; g.extras.vat.organ_ids.push('3'); })),
    /block 'birth': bufferView \d+ is 8 bytes, header implies 12/);
  assert.throws(
    () => parseVat(withEditedHeader(good, (g) => { g.extras.vat.blocks[0].components = 2; })),
    /block 'position': bufferView \d+ is 216 bytes, header implies 144/);
  assert.throws(
    () => parseVat(withEditedHeader(good, (g) => { g.extras.vat.blocks[0].storage = 'f64'; })), /block 'position': storage 'f64'/);
  assert.throws(
    () => parseVat(withEditedHeader(good, (g) => { g.extras.vat.blocks.splice(2, 1); })), /required block 'organ' is missing/);
});

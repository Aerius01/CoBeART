// Prints every block of a VAT file, decoded through vat.mjs, as JSON (used by test_vat_writer.py): node decode_vat.mjs FILE
import { readFileSync } from 'node:fs';
import { decodeBlock, parseVat } from '../vat.mjs';

const bytes = readFileSync(process.argv[2]);
const vat = parseVat(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
const blocks = Object.fromEntries([...vat.blocks].map(([name, block]) => [name, Array.from(decodeBlock(block))]));
console.log(JSON.stringify({ blocks, indices: Array.from(vat.indices), stageTimes: vat.stageTimes, never: vat.never }));

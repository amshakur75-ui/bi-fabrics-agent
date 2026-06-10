import { test } from 'node:test';
import assert from 'node:assert/strict';
import zlib from 'node:zlib';
import { readZipEntries } from './zip.js';

/** Build a valid single-entry ZIP by hand (method 0 = stored, 8 = deflate). */
function makeZip(name, content, method = 0) {
  const nameBuf = Buffer.from(name, 'utf8');
  const raw = Buffer.from(content, 'utf8');
  const data = method === 8 ? zlib.deflateRawSync(raw) : raw;

  const lh = Buffer.alloc(30);
  lh.writeUInt32LE(0x04034b50, 0);
  lh.writeUInt16LE(20, 4);
  lh.writeUInt16LE(method, 8);
  lh.writeUInt32LE(0, 14);            // crc (reader ignores)
  lh.writeUInt32LE(data.length, 18);  // compressed size
  lh.writeUInt32LE(raw.length, 22);   // uncompressed size
  lh.writeUInt16LE(nameBuf.length, 26);
  const local = Buffer.concat([lh, nameBuf, data]);

  const cd = Buffer.alloc(46);
  cd.writeUInt32LE(0x02014b50, 0);
  cd.writeUInt16LE(20, 4);
  cd.writeUInt16LE(20, 6);
  cd.writeUInt16LE(method, 10);
  cd.writeUInt32LE(0, 16);            // crc
  cd.writeUInt32LE(data.length, 20);
  cd.writeUInt32LE(raw.length, 24);
  cd.writeUInt16LE(nameBuf.length, 28);
  cd.writeUInt32LE(0, 42);            // local header offset
  const central = Buffer.concat([cd, nameBuf]);

  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(1, 8);           // entries on this disk
  eocd.writeUInt16LE(1, 10);          // total entries
  eocd.writeUInt32LE(central.length, 12);
  eocd.writeUInt32LE(local.length, 16); // central dir offset

  return Buffer.concat([local, central, eocd]);
}

test('reads a stored (uncompressed) ZIP entry', () => {
  const zip = makeZip('DaxModel.json', '{"hello":1}', 0);
  const entries = readZipEntries(zip);
  assert.equal(entries.get('DaxModel.json').toString('utf8'), '{"hello":1}');
});

test('reads a deflated ZIP entry', () => {
  const payload = JSON.stringify({ a: 'x'.repeat(200) });
  const zip = makeZip('DaxVpaView.json', payload, 8);
  const entries = readZipEntries(zip);
  assert.equal(entries.get('DaxVpaView.json').toString('utf8'), payload);
});

test('throws on non-ZIP input', () => {
  assert.throws(() => readZipEntries(Buffer.from('not a zip at all')), /not a ZIP/);
});

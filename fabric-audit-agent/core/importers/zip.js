import zlib from 'node:zlib';

/**
 * Minimal, dependency-free ZIP reader. Enough to crack open a `.vpax`
 * (which is just a ZIP of JSON). Supports stored (0) and deflate (8) entries.
 * Ignores CRC/encryption/zip64 — fine for VertiPaq exports.
 *
 * @param {Buffer} buf
 * @returns {Map<string, Buffer>}  entry name -> uncompressed bytes
 */
export function readZipEntries(buf) {
  if (!Buffer.isBuffer(buf)) throw new Error('readZipEntries expects a Buffer');
  const SIG_EOCD = 0x06054b50;
  const SIG_CD = 0x02014b50;

  // Locate End Of Central Directory (scan back from the end; min record is 22 bytes).
  let eocd = -1;
  for (let i = buf.length - 22; i >= 0; i--) {
    if (buf.readUInt32LE(i) === SIG_EOCD) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('not a ZIP file (no end-of-central-directory record)');

  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16); // central directory offset
  const entries = new Map();

  for (let n = 0; n < count; n++) {
    if (p + 46 > buf.length || buf.readUInt32LE(p) !== SIG_CD) break;
    const method = buf.readUInt16LE(p + 10);
    const compSize = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const localOff = buf.readUInt32LE(p + 42);
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen);

    // Jump to the local header to find where the data actually starts.
    const lhNameLen = buf.readUInt16LE(localOff + 26);
    const lhExtraLen = buf.readUInt16LE(localOff + 28);
    const dataStart = localOff + 30 + lhNameLen + lhExtraLen;
    const comp = buf.subarray(dataStart, dataStart + compSize);

    let data;
    if (method === 0) data = Buffer.from(comp);
    else if (method === 8) data = zlib.inflateRawSync(comp);
    else throw new Error(`unsupported ZIP compression method ${method} for "${name}"`);

    if (!name.endsWith('/')) entries.set(name, data); // skip directory entries
    p += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { vpaxToModels } from './vpax.js';

/** Build a single-entry stored ZIP (enough to wrap one JSON file as a .vpax). */
function makeVpax(name, obj) {
  const nameBuf = Buffer.from(name, 'utf8');
  const data = Buffer.from(JSON.stringify(obj), 'utf8');

  const lh = Buffer.alloc(30);
  lh.writeUInt32LE(0x04034b50, 0);
  lh.writeUInt16LE(20, 4);
  lh.writeUInt32LE(data.length, 18);
  lh.writeUInt32LE(data.length, 22);
  lh.writeUInt16LE(nameBuf.length, 26);
  const local = Buffer.concat([lh, nameBuf, data]);

  const cd = Buffer.alloc(46);
  cd.writeUInt32LE(0x02014b50, 0);
  cd.writeUInt32LE(data.length, 20);
  cd.writeUInt32LE(data.length, 24);
  cd.writeUInt16LE(nameBuf.length, 28);
  cd.writeUInt32LE(0, 42);
  const central = Buffer.concat([cd, nameBuf]);

  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(1, 8);
  eocd.writeUInt16LE(1, 10);
  eocd.writeUInt32LE(central.length, 12);
  eocd.writeUInt32LE(local.length, 16);

  return Buffer.concat([local, central, eocd]);
}

test('extracts size, bidirectional rels, and auto-date/time from a .vpax', () => {
  const vpax = makeVpax('DaxModel.json', {
    ModelName: 'Sales Model',
    Tables: [
      { TableName: 'Sales', Columns: [{ TotalSize: 3_000_000_000 }, { TotalSize: 2_500_000_000 }] },
      { TableName: 'LocalDateTable_abc', Columns: [{ TotalSize: 100_000_000 }] },
    ],
    Relationships: [
      { CrossFilteringBehavior: 'BothDirections' },
      { CrossFilteringBehavior: 'OneDirection' },
    ],
  });
  const { models } = vpaxToModels(vpax);
  assert.equal(models.length, 1);
  assert.equal(models[0].name, 'Sales Model');
  assert.equal(models[0].sizeGB, 5.6);
  assert.equal(models[0].bidirectionalRels, 1);
  assert.equal(models[0].autoDateTime, true);
});

test('throws a clear error when the .vpax has no model JSON', () => {
  const bad = makeVpax('readme.txt', { not: 'a model' });
  assert.throws(() => vpaxToModels(bad), /no DaxModel/);
});

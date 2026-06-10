import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseCsv } from './csv.js';

test('parses a simple table into header-keyed rows', () => {
  const { headers, rows } = parseCsv('a,b,c\n1,2,3\n4,5,6');
  assert.deepEqual(headers, ['a', 'b', 'c']);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { a: '1', b: '2', c: '3' });
  assert.deepEqual(rows[1], { a: '4', b: '5', c: '6' });
});

test('handles quoted fields with embedded commas and quotes', () => {
  const { rows } = parseCsv('name,note\n"Smith, Jane","said ""hi"""');
  assert.equal(rows[0].name, 'Smith, Jane');
  assert.equal(rows[0].note, 'said "hi"');
});

test('handles CRLF line endings and a UTF-8 BOM', () => {
  const { headers, rows } = parseCsv('﻿x,y\r\n10,20\r\n');
  assert.deepEqual(headers, ['x', 'y']);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0], { x: '10', y: '20' });
});

test('handles quoted newlines inside a field', () => {
  const { rows } = parseCsv('a,b\n"line1\nline2",2');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].a, 'line1\nline2');
  assert.equal(rows[0].b, '2');
});

test('skips blank lines and trims values', () => {
  const { rows } = parseCsv('a,b\n  1 , 2 \n\n3,4\n');
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { a: '1', b: '2' });
  assert.deepEqual(rows[1], { a: '3', b: '4' });
});

test('ragged rows: missing trailing cells become empty strings', () => {
  const { rows } = parseCsv('a,b,c\n1,2');
  assert.deepEqual(rows[0], { a: '1', b: '2', c: '' });
});

test('empty / whitespace-only input returns empty structure', () => {
  assert.deepEqual(parseCsv(''), { headers: [], rows: [] });
  assert.deepEqual(parseCsv('   '), { headers: [], rows: [] }); // blank line is dropped
});

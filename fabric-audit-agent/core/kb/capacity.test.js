import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getRemediation } from './capacity.js';

test('returns a playbook for a known flag type', () => {
  const p = getRemediation('capacity.oversized-model');
  assert.match(p.rootCause, /footprint/i);
  assert.ok(p.fixes.length >= 3);
  assert.equal(typeof p.owner, 'string');
});

test('returns a safe default for an unknown flag type', () => {
  const p = getRemediation('totally.unknown');
  assert.ok(Array.isArray(p.fixes));
  assert.ok(p.fixes.length >= 1);
});

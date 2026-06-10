import { test } from 'node:test';
import assert from 'node:assert/strict';
import { domainOf } from './key-utils.js';

test('domainOf: capacity.throttle::Finance / X → "capacity"', () => {
  assert.equal(domainOf('capacity.throttle::Finance / X'), 'capacity');
});

test('domainOf: "Finance.EU / X" (no "::") → "Finance" (splits whole string on first ".")', () => {
  // The key has no "::", so key.split('::')[0] is the whole string "Finance.EU / X".
  // That token includes a ".", so it returns the part before the first "." → "Finance".
  assert.equal(domainOf('Finance.EU / X'), 'Finance');
});

test('domainOf: null → "other"', () => {
  assert.equal(domainOf(null), 'other');
});

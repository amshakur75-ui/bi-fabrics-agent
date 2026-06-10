import { test } from 'node:test';
import assert from 'node:assert/strict';

test('test runner discovers tests in this folder', () => {
  assert.equal(1 + 1, 2);
});

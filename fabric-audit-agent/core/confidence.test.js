import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scoreConfidence } from './confidence.js';

test('scoreConfidence: capacity.throttle::X → high', () => {
  assert.equal(scoreConfidence({ key: 'capacity.throttle::SomeResource' }), 'high');
});

test('scoreConfidence: meta.detector-error::Y → low', () => {
  assert.equal(scoreConfidence({ key: 'meta.detector-error::SomeResource' }), 'low');
});

test('scoreConfidence: finding with reasonedBy:claude → medium', () => {
  assert.equal(scoreConfidence({ key: 'model.bidirectional::DS', reasonedBy: 'claude' }), 'medium');
});

test('scoreConfidence: missing key → high (default)', () => {
  assert.equal(scoreConfidence({}), 'high');
});

test('scoreConfidence: meta. prefix → low regardless of other fields', () => {
  assert.equal(scoreConfidence({ key: 'meta.parse-error::X', reasonedBy: 'claude' }), 'low');
});

test('scoreConfidence: non-meta, no reasonedBy → high', () => {
  assert.equal(scoreConfidence({ key: 'pipeline.failing::PL1' }), 'high');
});

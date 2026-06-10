import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dedupe } from './dedupe.js';

test('collapses two findings with the same key to one', () => {
  const findings = [
    { key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'x' } },
    { key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'duplicate' } },
  ];
  const result = dedupe(findings);
  assert.equal(result.length, 1);
  assert.equal(result[0].score.reason, 'x'); // first one kept
});

test('keeps keyless findings (so keyless test fakes are unaffected)', () => {
  const findings = [
    { score: { level: 'Critical', reason: 'no key' } },
    { score: { level: 'Warning', reason: 'also no key' } },
  ];
  const result = dedupe(findings);
  assert.equal(result.length, 2);
});

test('preserves order of first appearances', () => {
  const findings = [
    { key: 'a::1', score: { level: 'Critical', reason: 'first' } },
    { key: 'b::2', score: { level: 'Warning', reason: 'second' } },
    { key: 'a::1', score: { level: 'Critical', reason: 'dupe of first' } },
    { key: 'c::3', score: { level: 'Info', reason: 'third' } },
  ];
  const result = dedupe(findings);
  assert.equal(result.length, 3);
  assert.equal(result[0].key, 'a::1');
  assert.equal(result[1].key, 'b::2');
  assert.equal(result[2].key, 'c::3');
});

test('does not mutate the input array', () => {
  const findings = [
    { key: 'x::1', score: { level: 'Warning', reason: 'r' } },
    { key: 'x::1', score: { level: 'Warning', reason: 'r2' } },
  ];
  const copy = [...findings];
  dedupe(findings);
  assert.deepEqual(findings, copy);
});

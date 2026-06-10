import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildHealthScore } from './health-score.js';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function makeF(key, level) {
  return { key, score: { level }, what: 'x', where: 'y' };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('buildHealthScore: 1 Critical + 1 Warning → overall 89', () => {
  const findings = [
    makeF('capacity.throttle::C1', 'Critical'),
    makeF('model.oversized::M1', 'Warning'),
  ];
  const { overall } = buildHealthScore(findings);
  assert.equal(overall, 89); // 100 - (8 + 3)
});

test('buildHealthScore: per-domain isolates by key prefix', () => {
  const findings = [
    makeF('capacity.throttle::C1', 'Critical'), // capacity domain
    makeF('security.access::S1', 'Warning'),     // security domain
  ];
  const { byDomain } = buildHealthScore(findings);
  assert.equal(byDomain.capacity, 92);  // 100 - 8
  assert.equal(byDomain.security, 97);  // 100 - 3
});

test('buildHealthScore: empty findings → { overall: 100, byDomain: {} }', () => {
  const result = buildHealthScore([]);
  assert.equal(result.overall, 100);
  assert.deepEqual(result.byDomain, {});
});

test('buildHealthScore: no-arg call → { overall: 100, byDomain: {} }', () => {
  const result = buildHealthScore();
  assert.equal(result.overall, 100);
  assert.deepEqual(result.byDomain, {});
});

test('buildHealthScore: heavy set floors at 0 (never negative)', () => {
  // 14 Criticals × 8 = 112 penalty → overall should floor at 0
  const findings = Array.from({ length: 14 }, (_, i) =>
    makeF(`capacity.throttle::C${i}`, 'Critical')
  );
  const { overall, byDomain } = buildHealthScore(findings);
  assert.equal(overall, 0);
  assert.equal(byDomain.capacity, 0);
  assert.ok(overall >= 0, 'overall must never be negative');
});

test('buildHealthScore: key without dot prefix goes into "other" domain', () => {
  const findings = [
    makeF('unknownThing::X1', 'Warning'), // no dot in prefix → 'other'
  ];
  const { byDomain } = buildHealthScore(findings);
  assert.equal(byDomain.other, 97); // 100 - 3
});

test('buildHealthScore: multiple domains computed independently', () => {
  const findings = [
    makeF('capacity.throttle::C1', 'Critical'),
    makeF('capacity.contention::C2', 'Warning'),
    makeF('model.oversized::M1', 'Info'),
  ];
  const { overall, byDomain } = buildHealthScore(findings);
  assert.equal(overall, 100 - (8 + 3 + 1)); // 88
  assert.equal(byDomain.capacity, 100 - (8 + 3)); // 89
  assert.equal(byDomain.model, 100 - 1); // 99
});

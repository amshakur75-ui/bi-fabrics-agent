import { test } from 'node:test';
import assert from 'node:assert/strict';
import { correlate } from './correlate.js';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function f(key) { return { key }; }

// ---------------------------------------------------------------------------
// capacity-pressure
// ---------------------------------------------------------------------------

test('correlate: throttle + contention → capacity-pressure with both keys', () => {
  const findings = [
    f('capacity.throttle::Contoso / capacity F64'),
    f('capacity.contention::Contoso / capacity F64'),
  ];
  const result = correlate(findings);
  const cp = result.find(c => c.theme === 'capacity-pressure');
  assert.ok(cp, 'expected a capacity-pressure correlation');
  assert.ok(cp.findingKeys.includes('capacity.throttle::Contoso / capacity F64'));
  assert.ok(cp.findingKeys.includes('capacity.contention::Contoso / capacity F64'));
});

test('correlate: throttle + oversized-model → capacity-pressure', () => {
  const findings = [
    f('capacity.throttle::T / F64'),
    f('capacity.oversized-model::T / WS / DS'),
  ];
  const result = correlate(findings);
  const cp = result.find(c => c.theme === 'capacity-pressure');
  assert.ok(cp, 'expected capacity-pressure with throttle + oversized-model');
  assert.equal(cp.findingKeys.length, 2);
});

test('correlate: throttle alone (no drivers) → no capacity-pressure', () => {
  const findings = [f('capacity.throttle::T / F64')];
  const result = correlate(findings);
  assert.ok(!result.find(c => c.theme === 'capacity-pressure'),
    'no capacity-pressure without a driver');
});

test('correlate: contention alone (no throttle) → no capacity-pressure', () => {
  const findings = [f('capacity.contention::T / F64')];
  const result = correlate(findings);
  assert.ok(!result.find(c => c.theme === 'capacity-pressure'),
    'no capacity-pressure without throttle');
});

// ---------------------------------------------------------------------------
// refresh-chain
// ---------------------------------------------------------------------------

test('correlate: model.refresh-failing + pipeline.failing → refresh-chain', () => {
  const findings = [
    f('model.refresh-failing::Finance / Sales'),
    f('pipeline.failing::Finance / Nightly Load'),
  ];
  const result = correlate(findings);
  const rc = result.find(c => c.theme === 'refresh-chain');
  assert.ok(rc, 'expected refresh-chain correlation');
  assert.ok(rc.findingKeys.includes('model.refresh-failing::Finance / Sales'));
  assert.ok(rc.findingKeys.includes('pipeline.failing::Finance / Nightly Load'));
});

test('correlate: model.refresh-failing alone → no refresh-chain', () => {
  const findings = [f('model.refresh-failing::Finance / Sales')];
  const result = correlate(findings);
  assert.ok(!result.find(c => c.theme === 'refresh-chain'),
    'no refresh-chain with only model failure');
});

test('correlate: pipeline.failing alone → no refresh-chain', () => {
  const findings = [f('pipeline.failing::Finance / Nightly Load')];
  const result = correlate(findings);
  assert.ok(!result.find(c => c.theme === 'refresh-chain'),
    'no refresh-chain with only pipeline failure');
});

// ---------------------------------------------------------------------------
// security-cluster
// ---------------------------------------------------------------------------

test('correlate: 2+ security.* findings → security-cluster', () => {
  const findings = [
    f('security.admin-grant::Finance'),
    f('security.external-share::Finance / Exec Dashboard'),
  ];
  const result = correlate(findings);
  const sc = result.find(c => c.theme === 'security-cluster');
  assert.ok(sc, 'expected security-cluster');
  assert.equal(sc.findingKeys.length, 2);
});

test('correlate: 3 security.* findings → security-cluster with all 3 keys', () => {
  const findings = [
    f('security.admin-grant::Finance'),
    f('security.external-share::Finance / Exec Dashboard'),
    f('security.unusual-access::Finance'),
  ];
  const result = correlate(findings);
  const sc = result.find(c => c.theme === 'security-cluster');
  assert.ok(sc, 'expected security-cluster with 3 findings');
  assert.equal(sc.findingKeys.length, 3);
});

test('correlate: 1 security.* finding → no security-cluster', () => {
  const findings = [f('security.admin-grant::Finance')];
  const result = correlate(findings);
  assert.ok(!result.find(c => c.theme === 'security-cluster'),
    'no security-cluster with only 1 security finding');
});

// ---------------------------------------------------------------------------
// edge cases
// ---------------------------------------------------------------------------

test('correlate: no qualifying findings → []', () => {
  const findings = [
    f('report.too-many-visuals::Finance / Exec Dashboard'),
    f('model.bidirectional::Finance / Sales'),
  ];
  assert.deepEqual(correlate(findings), []);
});

test('correlate: empty array → []', () => {
  assert.deepEqual(correlate([]), []);
});

test('correlate: no-arg call → []', () => {
  assert.deepEqual(correlate(), []);
});

test('correlate: finding with non-string key is ignored', () => {
  const findings = [
    { key: null },
    { key: undefined },
    { key: 42 },
    f('security.admin-grant::Finance'),
  ];
  // Only 1 valid security finding → no cluster
  const result = correlate(findings);
  assert.ok(!result.find(c => c.theme === 'security-cluster'));
});

test('correlate: returned correlations have required shape', () => {
  const findings = [
    f('capacity.throttle::T / F64'),
    f('capacity.contention::T / F64'),
    f('model.refresh-failing::Finance / Sales'),
    f('pipeline.failing::Finance / Nightly Load'),
    f('security.admin-grant::Finance'),
    f('security.external-share::Finance / Exec Dashboard'),
  ];
  const result = correlate(findings);
  assert.equal(result.length, 3);
  for (const c of result) {
    assert.ok(typeof c.theme === 'string' && c.theme.length > 0, 'theme must be non-empty string');
    assert.ok(Array.isArray(c.findingKeys), 'findingKeys must be an array');
    assert.ok(c.findingKeys.length > 0, 'findingKeys must be non-empty');
    assert.ok(typeof c.narrative === 'string' && c.narrative.length > 0, 'narrative must be non-empty string');
  }
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCapacityVerdict } from './verdict.js';

const capFacts = (overrides = {}) => ({
  capacity: {
    tenant: 'C',
    capacityId: 'F64',
    sku: 'F64',
    memoryGB: 64,
    peakCuPct: 96,
    peakAt: '2026-01-01T10:00:00Z',
    throttleMinutes: 42,
    refreshes: [],
    ...overrides,
  },
});

test('buildCapacityVerdict: throttle + contention + oversized → optimize', () => {
  const facts = capFacts();
  const flags = [
    { type: 'capacity.throttle', evidence: { peakCuPct: 96, throttleMinutes: 42 } },
    { type: 'capacity.contention', evidence: {} },
    { type: 'capacity.oversized-model', evidence: {} },
  ];
  const v = buildCapacityVerdict(facts, flags);
  assert.equal(v.decision, 'optimize');
  assert.ok(v.evidence.optimizations.includes('capacity.contention'));
  assert.ok(v.evidence.optimizations.includes('capacity.oversized-model'));
  assert.equal(v.evidence.optimizations.length, 2);
});

test('buildCapacityVerdict: throttle + contention only → optimize', () => {
  const facts = capFacts();
  const flags = [
    { type: 'capacity.throttle', evidence: {} },
    { type: 'capacity.contention', evidence: {} },
  ];
  const v = buildCapacityVerdict(facts, flags);
  assert.equal(v.decision, 'optimize');
  assert.ok(v.evidence.optimizations.includes('capacity.contention'));
});

test('buildCapacityVerdict: throttle only on F64 → size-up with recommendedSku F128', () => {
  const facts = capFacts({ sku: 'F64', capacityId: 'F64' });
  const flags = [
    { type: 'capacity.throttle', evidence: { peakCuPct: 96, throttleMinutes: 42 } },
  ];
  const v = buildCapacityVerdict(facts, flags);
  assert.equal(v.decision, 'size-up');
  assert.equal(v.evidence.recommendedSku, 'F128');
  assert.equal(v.evidence.currentSku, 'F64');
});

test('buildCapacityVerdict: no throttle flag → healthy', () => {
  const facts = capFacts({ peakCuPct: 55, throttleMinutes: 0 });
  const flags = [
    { type: 'capacity.contention', evidence: {} },
  ];
  const v = buildCapacityVerdict(facts, flags);
  assert.equal(v.decision, 'healthy');
  assert.equal(v.evidence.peakCuPct, 55);
});

test('buildCapacityVerdict: no capacity facts → unknown', () => {
  const v = buildCapacityVerdict({}, []);
  assert.equal(v.decision, 'unknown');
  assert.deepEqual(v.evidence, {});
});

test('buildCapacityVerdict: null facts → unknown', () => {
  const v = buildCapacityVerdict(null, []);
  assert.equal(v.decision, 'unknown');
});

test('buildCapacityVerdict: empty flags (no throttle) → healthy', () => {
  const facts = capFacts({ peakCuPct: 70 });
  const v = buildCapacityVerdict(facts, []);
  assert.equal(v.decision, 'healthy');
});

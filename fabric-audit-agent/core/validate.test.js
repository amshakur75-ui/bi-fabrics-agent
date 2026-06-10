import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateFacts } from './validate.js';

// Well-formed estate → ok: true, no issues
const wellFormedFacts = {
  capacity: {
    tenant: 'Contoso', capacityId: 'F64', sku: 'F64', memoryGB: 64,
    peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [],
  },
  models: [{ workspace: 'Finance', name: 'Sales', sizeGB: 4.2 }],
  reports: [{ workspace: 'Finance', name: 'Exec Dashboard', visuals: 41 }],
  pipelines: [{ workspace: 'Finance', name: 'Nightly Load', lastStatus: 'Failed' }],
  lineage: { nodes: [], edges: [] },
};

test('validateFacts returns ok:true for a well-formed estate', () => {
  const result = validateFacts(wellFormedFacts);
  assert.equal(result.ok, true);
  assert.deepEqual(result.issues, []);
});

test('validateFacts reports missing capacity.memoryGB', () => {
  const facts = {
    capacity: { capacityId: 'F64', sku: 'F64', peakCuPct: 96 },
  };
  const result = validateFacts(facts);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some(i => i.domain === 'capacity' && i.issue === 'missing memoryGB'));
});

test('validateFacts reports non-array models domain', () => {
  const facts = { models: 'not-an-array' };
  const result = validateFacts(facts);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some(i => i.domain === 'models' && i.issue === 'expected an array'));
});

test('validateFacts reports non-array capacity.refreshes', () => {
  const facts = {
    capacity: { capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, refreshes: 'oops' },
  };
  const result = validateFacts(facts);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some(i => i.domain === 'capacity' && i.issue === 'refreshes must be an array'));
});

test('validateFacts reports non-array lineage.nodes', () => {
  const facts = { lineage: { nodes: 'bad', edges: [] } };
  const result = validateFacts(facts);
  assert.equal(result.ok, false);
  assert.ok(result.issues.some(i => i.domain === 'lineage'));
});

test('validateFacts returns ok:true for empty facts (no domain present)', () => {
  const result = validateFacts({});
  assert.equal(result.ok, true);
  assert.deepEqual(result.issues, []);
});

test('validateFacts reports all four missing capacity required fields', () => {
  const facts = { capacity: {} };
  const result = validateFacts(facts);
  assert.equal(result.ok, false);
  const domains = result.issues.filter(i => i.domain === 'capacity');
  assert.equal(domains.length, 4, 'expected 4 missing-field issues for empty capacity');
});

test('validateFacts reports non-array reports and pipelines domains', () => {
  const facts = { reports: {}, pipelines: 42 };
  const result = validateFacts(facts);
  assert.ok(result.issues.some(i => i.domain === 'reports'));
  assert.ok(result.issues.some(i => i.domain === 'pipelines'));
});

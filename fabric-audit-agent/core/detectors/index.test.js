import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectAll } from './index.js';
import { detectCapacity } from './capacity.js';

test('detectAll runs the capacity detector', () => {
  const facts = { capacity: { tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [] } };
  const flags = detectAll(facts);
  assert.ok(flags.some(f => f.type === 'capacity.throttle'));
});

test('detectAll returns an array for empty facts', () => {
  assert.deepEqual(detectAll({}), []);
});

// Inc-2: full estate covers all four domains
const estate = {
  capacity: { tenant: 'Contoso', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [{ workspace: 'Finance', dataset: 'Sales', scheduledAt: '06:00', durationMin: 47, sizeGB: 4.2 }] },
  models: [{ workspace: 'Finance', name: 'Sales', sizeGB: 4.2, bidirectionalRels: 9, autoDateTime: true, refreshFailRatePct: 12, observedAt: '2026-06-08T06:00:00.000Z' }],
  reports: [{ workspace: 'Finance', name: 'Exec Dashboard', visuals: 41, mode: 'DirectQuery', slowestVisualMs: 12000, source: 'Synapse' }],
  pipelines: [{ workspace: 'Finance', name: 'Nightly Load', lastStatus: 'Failed', failRatePct: 18, gatewayHealthy: false, lastRunAt: '2026-06-08T02:14:00.000Z' }],
};

test('detectAll on the full estate returns flags from all four domains', () => {
  const flags = detectAll(estate);
  const types = new Set(flags.map(f => f.type.split('.')[0]));
  assert.ok(types.has('capacity'), 'missing capacity flags');
  assert.ok(types.has('model'), 'missing model flags');
  assert.ok(types.has('report'), 'missing report flags');
  assert.ok(types.has('pipeline'), 'missing pipeline flags');
});

// Inc-7: lineage domain
const estateWithLineage = {
  ...estate,
  lineage: {
    nodes: [
      { id: 'pl-nightly', type: 'pipeline', workspace: 'Finance', name: 'Nightly Load', status: 'Failed', failedAt: '2026-06-08T02:14:00.000Z' },
      { id: 'ds-sales',   type: 'dataset',  workspace: 'Finance', name: 'Sales',         status: 'OK' },
      { id: 'rpt-exec',   type: 'report',   workspace: 'Finance', name: 'Exec Dashboard', status: 'OK' },
    ],
    edges: [
      { from: 'pl-nightly', to: 'ds-sales' },
      { from: 'ds-sales',   to: 'rpt-exec' },
    ],
  },
};

test('detectAll on estate with lineage includes a lineage.blast-radius flag', () => {
  const flags = detectAll(estateWithLineage);
  assert.ok(flags.some(f => f.type === 'lineage.blast-radius'), 'missing lineage.blast-radius flag');
});

// Inc-8: security and cost domains
const estateWithSecurityAndCost = {
  ...estateWithLineage,
  access: {
    adminGrants: [
      { workspace: 'Finance', principal: 'ext-contractor@vendor.com', role: 'Admin', grantedAt: '2026-06-07T22:10:00.000Z', sensitive: true },
    ],
    externalShares: [
      { workspace: 'Finance', item: 'Exec Dashboard', sharedWith: 'partner@othercorp.com', at: '2026-06-06T15:00:00.000Z' },
    ],
    accessEvents: [
      { user: 'jdoe', workspace: 'Finance', count: 220, baselineCount: 20 },
    ],
  },
  usage: {
    reports: [
      { workspace: 'Ops', name: 'Old Quarterly', views30d: 0 },
    ],
    capacities: [
      { id: 'F64', sku: 'F64', avgCuPct: 3 },
    ],
  },
};

test('detectAll on full estate includes security.* and cost.* flags', () => {
  const flags = detectAll(estateWithSecurityAndCost);
  const types = new Set(flags.map(f => f.type.split('.')[0]));
  assert.ok(types.has('security'), 'missing security flags');
  assert.ok(types.has('cost'), 'missing cost flags');
  assert.ok(flags.some(f => f.type === 'security.admin-grant'), 'missing security.admin-grant');
  assert.ok(flags.some(f => f.type === 'security.external-share'), 'missing security.external-share');
  assert.ok(flags.some(f => f.type === 'security.unusual-access'), 'missing security.unusual-access');
  assert.ok(flags.some(f => f.type === 'cost.unused-report'), 'missing cost.unused-report');
  assert.ok(flags.some(f => f.type === 'cost.idle-capacity'), 'missing cost.idle-capacity');
});

// ---------------------------------------------------------------------------
// Inc-13: resilient detectAll
// ---------------------------------------------------------------------------

import { DEFAULT_CONFIG } from '../config.js';

test('detectAll with a throwing detector returns one meta.detector-error flag without throwing', () => {
  const throwingDetector = () => { throw new Error('boom'); };
  let flags;
  assert.doesNotThrow(() => {
    flags = detectAll({}, DEFAULT_CONFIG, [throwingDetector]);
  });
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'meta.detector-error');
  assert.match(flags[0].evidence.message, /boom/);
});

test('detectAll with a mix of a real detector and a throwing detector returns both capacity flags and the meta flag', () => {
  const throwingDetector = function badDetector() { throw new Error('oops'); };
  const capacityFacts = {
    capacity: { tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [] },
  };
  const flags = detectAll(capacityFacts, DEFAULT_CONFIG, [detectCapacity, throwingDetector]);
  assert.ok(flags.some(f => f.type === 'capacity.throttle'), 'expected capacity flags');
  assert.ok(flags.some(f => f.type === 'meta.detector-error'), 'expected meta.detector-error flag');
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectCapacity } from './capacity.js';

const facts = {
  capacity: {
    tenant: 'Contoso', capacityId: 'F64', sku: 'F64', memoryGB: 64,
    peakCuPct: 96, peakAt: '2026-06-08T06:05:00.000Z', throttleMinutes: 42,
    refreshes: [
      { workspace: 'Finance', dataset: 'Sales', scheduledAt: '06:00', durationMin: 47, sizeGB: 4.2 },
      { workspace: 'Finance', dataset: 'Forecast', scheduledAt: '06:00', durationMin: 31, sizeGB: 2.1 },
      { workspace: 'Ops', dataset: 'Logistics', scheduledAt: '06:00', durationMin: 22, sizeGB: 1.4 },
      { workspace: 'HR', dataset: 'Headcount', scheduledAt: '09:00', durationMin: 6, sizeGB: 0.3 },
    ],
  },
};

test('flags throttle, contention, and the oversized model', () => {
  const types = detectCapacity(facts).map(f => f.type).sort();
  assert.deepEqual(types, ['capacity.contention', 'capacity.oversized-model', 'capacity.throttle']);
});

test('contention flag lists the 3 colliding datasets at 06:00', () => {
  const c = detectCapacity(facts).find(f => f.type === 'capacity.contention');
  assert.deepEqual(c.evidence.datasets, ['Sales', 'Forecast', 'Logistics']);
});

test('returns no flags for a healthy capacity', () => {
  const healthy = { capacity: { tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 40, peakAt: '', throttleMinutes: 0, refreshes: [] } };
  assert.deepEqual(detectCapacity(healthy), []);
});

test('tolerates missing capacity facts', () => {
  assert.deepEqual(detectCapacity({}), []);
});

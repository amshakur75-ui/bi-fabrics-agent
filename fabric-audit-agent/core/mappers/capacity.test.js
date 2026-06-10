import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mapCapacity } from './capacity.js';

test('mapCapacity renames fields from raw API shape', () => {
  const raw = {
    capacity: {
      id: 'C-001',
      displayName: 'F64',
      sku: 'F64',
      memoryGb: 64,
      peakCuPercent: 96,
      peakTimestamp: '2026-06-08T06:05:00.000Z',
      throttledMinutes: 42,
      tenantName: 'Contoso',
    },
    refreshes: [],
  };
  const { capacity } = mapCapacity(raw);
  assert.equal(capacity.capacityId, 'F64');
  assert.equal(capacity.memoryGB, 64);
  assert.equal(capacity.peakCuPct, 96);
  assert.equal(capacity.tenant, 'Contoso');
  assert.equal(capacity.throttleMinutes, 42);
});

test('mapCapacity converts sizeBytes to sizeGB (4200000000 → 4.2)', () => {
  const raw = {
    capacity: { displayName: 'F64', tenantName: 'Contoso' },
    refreshes: [
      {
        groupName: 'Finance',
        datasetName: 'Sales',
        scheduleTime: '06:00',
        startTime: '2026-06-08T06:00:00.000Z',
        endTime: '2026-06-08T06:47:00.000Z',
        sizeBytes: 4200000000,
      },
    ],
  };
  const { capacity } = mapCapacity(raw);
  assert.equal(capacity.refreshes[0].sizeGB, 4.2);
});

test('mapCapacity computes durationMin from ISO timestamps (06:00→06:47 → 47)', () => {
  const raw = {
    capacity: { displayName: 'F64', tenantName: 'Contoso' },
    refreshes: [
      {
        groupName: 'Finance',
        datasetName: 'Sales',
        scheduleTime: '06:00',
        startTime: '2026-06-08T06:00:00.000Z',
        endTime: '2026-06-08T06:47:00.000Z',
        sizeBytes: 0,
      },
    ],
  };
  const { capacity } = mapCapacity(raw);
  assert.equal(capacity.refreshes[0].durationMin, 47);
});

test('mapCapacity maps refresh fields: workspace, dataset, scheduledAt', () => {
  const raw = {
    capacity: { displayName: 'F64', tenantName: 'Contoso' },
    refreshes: [
      {
        groupName: 'Finance',
        datasetName: 'Sales',
        scheduleTime: '06:00',
        startTime: '2026-06-08T06:00:00.000Z',
        endTime: '2026-06-08T06:31:00.000Z',
        sizeBytes: 1000000000,
      },
    ],
  };
  const { capacity } = mapCapacity(raw);
  const r = capacity.refreshes[0];
  assert.equal(r.workspace, 'Finance');
  assert.equal(r.dataset, 'Sales');
  assert.equal(r.scheduledAt, '06:00');
});

test('mapCapacity with empty input returns a capacity object with an empty refreshes array', () => {
  const { capacity } = mapCapacity();
  assert.ok(Array.isArray(capacity.refreshes), 'refreshes should be an array');
  assert.equal(capacity.refreshes.length, 0);
});

test('mapCapacity falls back to id when displayName is absent', () => {
  const raw = {
    capacity: { id: 'C-001', tenantName: 'Contoso' },
    refreshes: [],
  };
  const { capacity } = mapCapacity(raw);
  assert.equal(capacity.capacityId, 'C-001');
});

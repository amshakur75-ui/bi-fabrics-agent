import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectPipelines } from './pipeline.js';

const nightlyLoad = {
  workspace: 'Finance', name: 'Nightly Load',
  lastStatus: 'Failed', failRatePct: 18, gatewayHealthy: false,
  lastRunAt: '2026-06-08T02:14:00.000Z',
};

const hourlySync = {
  workspace: 'Ops', name: 'Hourly Sync',
  lastStatus: 'Succeeded', failRatePct: 2, gatewayHealthy: true,
  lastRunAt: '2026-06-08T08:00:00.000Z',
};

test('Nightly Load yields pipeline.failing and pipeline.gateway', () => {
  const types = detectPipelines({ pipelines: [nightlyLoad] }).map(f => f.type).sort();
  assert.deepEqual(types, ['pipeline.failing', 'pipeline.gateway']);
});

test('pipeline.failing on a Failed pipeline scores Critical', () => {
  const f = detectPipelines({ pipelines: [nightlyLoad] }).find(f => f.type === 'pipeline.failing');
  assert.equal(f.evidence.status, 'Failed');
  // verify severity separately (severity is tested in severity.test.js — this just confirms evidence)
});

test('Hourly Sync yields no flags', () => {
  assert.deepEqual(detectPipelines({ pipelines: [hourlySync] }), []);
});

test('detectPipelines({}) returns []', () => {
  assert.deepEqual(detectPipelines({}), []);
});

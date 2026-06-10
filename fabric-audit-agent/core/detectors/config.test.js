/**
 * Inc 12 — config-driven threshold tests (one representative per detector).
 * Each test verifies that a single key override changes the flag outcome, while
 * the default config still produces the flag (backward compat proof).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeConfig } from '../config.js';
import { detectCapacity } from './capacity.js';
import { detectModels } from './model.js';
import { detectReports } from './report.js';
import { detectPipelines } from './pipeline.js';
import { detectSecurity } from './security.js';
import { detectCost } from './cost.js';

// ---------------------------------------------------------------------------
// Capacity — throttleWarnPct override
// ---------------------------------------------------------------------------

const capacityFacts = {
  capacity: {
    tenant: 'Contoso', capacityId: 'F64', sku: 'F64', memoryGB: 64,
    peakCuPct: 96, peakAt: '2026-06-08T06:05:00.000Z', throttleMinutes: 42,
    refreshes: [],
  },
};

test('detectCapacity: default config flags throttle at 96% CU', () => {
  const flags = detectCapacity(capacityFacts);
  assert.ok(flags.some(f => f.type === 'capacity.throttle'), 'expected capacity.throttle flag with default config');
});

test('detectCapacity: throttleWarnPct=99 suppresses throttle flag for 96% CU', () => {
  const config = mergeConfig({ capacity: { throttleWarnPct: 99 } });
  const flags = detectCapacity(capacityFacts, config);
  assert.ok(!flags.some(f => f.type === 'capacity.throttle'), 'should NOT flag throttle when threshold is 99 and peak is 96');
});

test('detectCapacity: contentionMin=10 suppresses contention flag for 3 simultaneous refreshes', () => {
  const facts = {
    capacity: {
      tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64,
      peakCuPct: 40, peakAt: '', throttleMinutes: 0,
      refreshes: [
        { workspace: 'Finance', dataset: 'A', scheduledAt: '06:00', durationMin: 10, sizeGB: 0.5 },
        { workspace: 'Finance', dataset: 'B', scheduledAt: '06:00', durationMin: 10, sizeGB: 0.5 },
        { workspace: 'Finance', dataset: 'C', scheduledAt: '06:00', durationMin: 10, sizeGB: 0.5 },
      ],
    },
  };
  const config = mergeConfig({ capacity: { contentionMin: 10 } });
  const flags = detectCapacity(facts, config);
  assert.ok(!flags.some(f => f.type === 'capacity.contention'), 'should not flag contention when contentionMin=10 and only 3 overlap');
});

test('detectCapacity: oversizedGB=10 suppresses oversized flag for 4.2 GB model', () => {
  const facts = {
    capacity: {
      tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64,
      peakCuPct: 40, peakAt: '', throttleMinutes: 0,
      refreshes: [{ workspace: 'Finance', dataset: 'Sales', scheduledAt: '06:00', durationMin: 47, sizeGB: 4.2 }],
    },
  };
  const config = mergeConfig({ capacity: { oversizedGB: 10 } });
  const flags = detectCapacity(facts, config);
  assert.ok(!flags.some(f => f.type === 'capacity.oversized-model'), 'should not flag oversized when threshold is 10 and model is 4.2 GB');
});

// ---------------------------------------------------------------------------
// Model — bidirectionalMin override
// ---------------------------------------------------------------------------

const modelFacts = {
  models: [{ workspace: 'Finance', name: 'Sales', bidirectionalRels: 4, autoDateTime: false, refreshFailRatePct: 0, observedAt: '' }],
};

test('detectModels: default config flags bidirectional at count=4', () => {
  const flags = detectModels(modelFacts);
  assert.ok(flags.some(f => f.type === 'model.bidirectional'), 'expected model.bidirectional with default config');
});

test('detectModels: bidirectionalMin=10 suppresses bidirectional flag for count=4', () => {
  const config = mergeConfig({ model: { bidirectionalMin: 10 } });
  const flags = detectModels(modelFacts, config);
  assert.ok(!flags.some(f => f.type === 'model.bidirectional'), 'should not flag bidirectional when min=10 and count=4');
});

test('detectModels: refreshFailPct=50 suppresses refresh-failing for 10% fail rate', () => {
  const facts = {
    models: [{ workspace: 'Finance', name: 'Sales', bidirectionalRels: 0, autoDateTime: false, refreshFailRatePct: 10, observedAt: '' }],
  };
  const config = mergeConfig({ model: { refreshFailPct: 50 } });
  const flags = detectModels(facts, config);
  assert.ok(!flags.some(f => f.type === 'model.refresh-failing'), 'should not flag refresh-failing when threshold=50 and rate=10');
});

// ---------------------------------------------------------------------------
// Report — visualsMin override
// ---------------------------------------------------------------------------

const reportFacts = {
  reports: [{ workspace: 'Finance', name: 'Exec Dashboard', visuals: 25, mode: 'Import', slowestVisualMs: 1000 }],
};

test('detectReports: default config flags too-many-visuals at 25 visuals', () => {
  const flags = detectReports(reportFacts);
  assert.ok(flags.some(f => f.type === 'report.too-many-visuals'), 'expected report.too-many-visuals with default config');
});

test('detectReports: visualsMin=50 suppresses too-many-visuals flag for 25 visuals', () => {
  const config = mergeConfig({ report: { visualsMin: 50 } });
  const flags = detectReports(reportFacts, config);
  assert.ok(!flags.some(f => f.type === 'report.too-many-visuals'), 'should not flag too-many-visuals when min=50 and count=25');
});

test('detectReports: slowVisualMs=20000 suppresses slow-visual for 5000ms visual', () => {
  const facts = {
    reports: [{ workspace: 'Finance', name: 'Exec Dashboard', visuals: 0, mode: 'Import', slowestVisualMs: 5000 }],
  };
  const config = mergeConfig({ report: { slowVisualMs: 20000 } });
  const flags = detectReports(facts, config);
  assert.ok(!flags.some(f => f.type === 'report.slow-visual'), 'should not flag slow-visual when threshold=20000 and ms=5000');
});

// ---------------------------------------------------------------------------
// Pipeline — failRatePct override
// ---------------------------------------------------------------------------

const pipelineFacts = {
  pipelines: [{ workspace: 'Finance', name: 'Nightly', lastStatus: 'Succeeded', failRatePct: 12, gatewayHealthy: true, lastRunAt: '' }],
};

test('detectPipelines: default config flags pipeline.failing for failRatePct=12', () => {
  const flags = detectPipelines(pipelineFacts);
  assert.ok(flags.some(f => f.type === 'pipeline.failing'), 'expected pipeline.failing with default config');
});

test('detectPipelines: failRatePct=50 suppresses pipeline.failing for 12% fail rate', () => {
  const config = mergeConfig({ pipeline: { failRatePct: 50 } });
  const flags = detectPipelines(pipelineFacts, config);
  assert.ok(!flags.some(f => f.type === 'pipeline.failing'), 'should not flag pipeline.failing when threshold=50 and rate=12');
});

// ---------------------------------------------------------------------------
// Security — unusualRatio override
// ---------------------------------------------------------------------------

const securityFacts = {
  access: {
    adminGrants: [],
    externalShares: [],
    accessEvents: [{ user: 'jdoe', workspace: 'Finance', count: 50, baselineCount: 10 }],
  },
};

test('detectSecurity: default config flags unusual-access at ratio=5', () => {
  const flags = detectSecurity(securityFacts);
  assert.ok(flags.some(f => f.type === 'security.unusual-access'), 'expected security.unusual-access with default config');
});

test('detectSecurity: unusualRatio=10 suppresses unusual-access flag for ratio=5', () => {
  const config = mergeConfig({ security: { unusualRatio: 10 } });
  const flags = detectSecurity(securityFacts, config);
  assert.ok(!flags.some(f => f.type === 'security.unusual-access'), 'should not flag unusual-access when ratio threshold=10 and ratio=5');
});

// ---------------------------------------------------------------------------
// Cost — idleCuPct override
// ---------------------------------------------------------------------------

const costFacts = {
  usage: {
    reports: [],
    capacities: [{ id: 'F64', sku: 'F64', avgCuPct: 3 }],
  },
};

test('detectCost: default config flags idle-capacity at avgCuPct=3', () => {
  const flags = detectCost(costFacts);
  assert.ok(flags.some(f => f.type === 'cost.idle-capacity'), 'expected cost.idle-capacity with default config');
});

test('detectCost: idleCuPct=1 suppresses idle-capacity flag for avgCuPct=3', () => {
  const config = mergeConfig({ cost: { idleCuPct: 1 } });
  const flags = detectCost(costFacts, config);
  assert.ok(!flags.some(f => f.type === 'cost.idle-capacity'), 'should not flag idle-capacity when threshold=1 and avgCuPct=3');
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scoreSeverity } from './severity.js';

test('throttle >=90% CU and >30 min throttled is Critical', () => {
  const s = scoreSeverity({ type: 'capacity.throttle', evidence: { peakCuPct: 96, throttleMinutes: 42 } });
  assert.equal(s.level, 'Critical');
});

test('throttle at 82% CU is Warning', () => {
  const s = scoreSeverity({ type: 'capacity.throttle', evidence: { peakCuPct: 82, throttleMinutes: 5 } });
  assert.equal(s.level, 'Warning');
});

test('contention with 3 datasets is Warning, 4+ is Critical', () => {
  assert.equal(scoreSeverity({ type: 'capacity.contention', evidence: { time: '06:00', datasets: ['a', 'b', 'c'] } }).level, 'Warning');
  assert.equal(scoreSeverity({ type: 'capacity.contention', evidence: { time: '06:00', datasets: ['a', 'b', 'c', 'd'] } }).level, 'Critical');
});

test('oversized model >=25% of capacity memory is Critical', () => {
  assert.equal(scoreSeverity({ type: 'capacity.oversized-model', evidence: { sizeGB: 4.2, memoryGB: 64 } }).level, 'Warning');
  assert.equal(scoreSeverity({ type: 'capacity.oversized-model', evidence: { sizeGB: 20, memoryGB: 64 } }).level, 'Critical');
});

test('unknown flag type is Info', () => {
  assert.equal(scoreSeverity({ type: 'nope', evidence: {} }).level, 'Info');
});

// Inc-2 severity cases
test('model.bidirectional count 9 is Critical, count 4 is Warning', () => {
  assert.equal(scoreSeverity({ type: 'model.bidirectional', evidence: { count: 9 } }).level, 'Critical');
  assert.equal(scoreSeverity({ type: 'model.bidirectional', evidence: { count: 4 } }).level, 'Warning');
});

test('model.auto-datetime is Warning', () => {
  assert.equal(scoreSeverity({ type: 'model.auto-datetime', evidence: {} }).level, 'Warning');
});

test('model.refresh-failing >=25% is Critical, <25% is Warning', () => {
  assert.equal(scoreSeverity({ type: 'model.refresh-failing', evidence: { failRatePct: 30 } }).level, 'Critical');
  assert.equal(scoreSeverity({ type: 'model.refresh-failing', evidence: { failRatePct: 12 } }).level, 'Warning');
});

test('report.too-many-visuals 41 is Critical, 20 is Warning', () => {
  assert.equal(scoreSeverity({ type: 'report.too-many-visuals', evidence: { visuals: 41 } }).level, 'Critical');
  assert.equal(scoreSeverity({ type: 'report.too-many-visuals', evidence: { visuals: 20 } }).level, 'Warning');
});

test('report.directquery is Warning', () => {
  assert.equal(scoreSeverity({ type: 'report.directquery', evidence: {} }).level, 'Warning');
});

test('report.slow-visual >=10000ms is Critical, 5000ms is Warning', () => {
  assert.equal(scoreSeverity({ type: 'report.slow-visual', evidence: { ms: 12000 } }).level, 'Critical');
  assert.equal(scoreSeverity({ type: 'report.slow-visual', evidence: { ms: 5000 } }).level, 'Warning');
});

test('pipeline.failing with status Failed is Critical, failRatePct only is Warning', () => {
  assert.equal(scoreSeverity({ type: 'pipeline.failing', evidence: { status: 'Failed', failRatePct: 18 } }).level, 'Critical');
  assert.equal(scoreSeverity({ type: 'pipeline.failing', evidence: { status: 'Succeeded', failRatePct: 15 } }).level, 'Warning');
});

test('pipeline.gateway is Critical', () => {
  assert.equal(scoreSeverity({ type: 'pipeline.gateway', evidence: {} }).level, 'Critical');
});

// Inc-7 severity cases
test('lineage.blast-radius with affectedCount >= 1 is Critical', () => {
  assert.equal(scoreSeverity({ type: 'lineage.blast-radius', evidence: { affectedCount: 2 } }).level, 'Critical');
});

test('lineage.blast-radius with affectedCount === 0 is Warning', () => {
  assert.equal(scoreSeverity({ type: 'lineage.blast-radius', evidence: { affectedCount: 0 } }).level, 'Warning');
});

// Inc-8 severity cases
test('security.admin-grant is Critical', () => {
  assert.equal(scoreSeverity({ type: 'security.admin-grant', evidence: { principal: 'x', role: 'Admin', sensitive: true } }).level, 'Critical');
});

test('security.unusual-access with ratio 11 is Critical', () => {
  assert.equal(scoreSeverity({ type: 'security.unusual-access', evidence: { ratio: 11 } }).level, 'Critical');
});

test('security.unusual-access with ratio 6 is Warning', () => {
  assert.equal(scoreSeverity({ type: 'security.unusual-access', evidence: { ratio: 6 } }).level, 'Warning');
});

test('cost.unused-report is Info', () => {
  assert.equal(scoreSeverity({ type: 'cost.unused-report', evidence: { views30d: 0 } }).level, 'Info');
});

test('cost.idle-capacity is Warning', () => {
  assert.equal(scoreSeverity({ type: 'cost.idle-capacity', evidence: { sku: 'F64', avgCuPct: 3 } }).level, 'Warning');
});

// ---------------------------------------------------------------------------
// Inc-12: config override assertions
// ---------------------------------------------------------------------------
import { mergeConfig } from './config.js';

test('scoreSeverity: throttleCritPct=99 downgrades 96% CU throttle from Critical to Warning', () => {
  const config = mergeConfig({ capacity: { throttleCritPct: 99 } });
  const flag = { type: 'capacity.throttle', evidence: { peakCuPct: 96, throttleMinutes: 42 } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

test('scoreSeverity: default config still scores 96%/42min throttle as Critical', () => {
  const flag = { type: 'capacity.throttle', evidence: { peakCuPct: 96, throttleMinutes: 42 } };
  assert.equal(scoreSeverity(flag).level, 'Critical');
});

test('scoreSeverity: contentionCritCount=10 downgrades 4-dataset contention from Critical to Warning', () => {
  const config = mergeConfig({ capacity: { contentionCritCount: 10 } });
  const flag = { type: 'capacity.contention', evidence: { time: '06:00', datasets: ['a', 'b', 'c', 'd'] } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

test('scoreSeverity: bidirectionalCritMin=20 downgrades count=9 bidirectional from Critical to Warning', () => {
  const config = mergeConfig({ model: { bidirectionalCritMin: 20 } });
  const flag = { type: 'model.bidirectional', evidence: { count: 9 } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

test('scoreSeverity: refreshFailCritPct=50 downgrades 30% fail rate from Critical to Warning', () => {
  const config = mergeConfig({ model: { refreshFailCritPct: 50 } });
  const flag = { type: 'model.refresh-failing', evidence: { failRatePct: 30 } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

test('scoreSeverity: visualsCritMin=100 downgrades 41 visuals from Critical to Warning', () => {
  const config = mergeConfig({ report: { visualsCritMin: 100 } });
  const flag = { type: 'report.too-many-visuals', evidence: { visuals: 41 } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

test('scoreSeverity: slowVisualCritMs=99999 downgrades 12000ms slow visual from Critical to Warning', () => {
  const config = mergeConfig({ report: { slowVisualCritMs: 99999 } });
  const flag = { type: 'report.slow-visual', evidence: { ms: 12000 } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

test('scoreSeverity: unusualCritRatio=100 downgrades ratio=11 unusual-access from Critical to Warning', () => {
  const config = mergeConfig({ security: { unusualCritRatio: 100 } });
  const flag = { type: 'security.unusual-access', evidence: { ratio: 11 } };
  assert.equal(scoreSeverity(flag, config).level, 'Warning');
});

// ---------------------------------------------------------------------------
// Inc-13: meta.detector-error severity
// ---------------------------------------------------------------------------

test('meta.detector-error is Warning', () => {
  assert.equal(
    scoreSeverity({ type: 'meta.detector-error', evidence: { detector: 'unknown', message: 'boom' } }).level,
    'Warning',
  );
});

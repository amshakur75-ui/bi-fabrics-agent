import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildDigest } from './digest.js';

const makeRun = (keys) => ({ runAt: '2026-01-01T00:00:00Z', findings: keys.map(k => ({ key: k })) });

const baseFindings = [
  { key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'r' }, recurringRuns: 1 },
  { key: 'capacity.contention::CapA', score: { level: 'Warning', reason: 'r' }, recurringRuns: 1 },
  { key: 'model.bidirectional::DatasetA', score: { level: 'Warning', reason: 'r' }, recurringRuns: 1 },
  { key: 'report.too-many-visuals::ReportX', score: { level: 'Info', reason: 'r' }, recurringRuns: 1 },
  { key: 'pipeline.failing::PipeY', score: { level: 'Critical', reason: 'r' }, recurringRuns: 1 },
];

test('totals counts each level correctly', () => {
  const { totals } = buildDigest(baseFindings, []);
  assert.equal(totals.Critical, 2);
  assert.equal(totals.Warning, 2);
  assert.equal(totals.Info, 1);
});

test('byDomain groups by key prefix', () => {
  const { byDomain } = buildDigest(baseFindings, []);
  assert.equal(byDomain.capacity, 2);
  assert.equal(byDomain.model, 1);
  assert.equal(byDomain.report, 1);
  assert.equal(byDomain.pipeline, 1);
});

test('newCount counts findings not in the previous run', () => {
  // previous run had capacity.throttle and model.bidirectional
  const history = [makeRun(['capacity.throttle::CapA', 'model.bidirectional::DatasetA'])];
  const { newCount } = buildDigest(baseFindings, history);
  // capacity.contention, report.too-many-visuals, pipeline.failing are new (3)
  assert.equal(newCount, 3);
});

test('newCount is all findings when history is empty', () => {
  const { newCount } = buildDigest(baseFindings, []);
  // all 5 have keys and none were in prior run
  assert.equal(newCount, 5);
});

test('newCount is 0 when all keys appeared in the previous run', () => {
  const keys = baseFindings.map(f => f.key);
  const history = [makeRun(keys)];
  const { newCount } = buildDigest(baseFindings, history);
  assert.equal(newCount, 0);
});

test('recurring lists only findings with recurringRuns >= 3', () => {
  const findings = [
    { key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'r' }, recurringRuns: 3 },
    { key: 'model.bidirectional::DatasetA', score: { level: 'Warning', reason: 'r' }, recurringRuns: 2 },
    { key: 'pipeline.failing::PipeY', score: { level: 'Critical', reason: 'r' }, recurringRuns: 5 },
  ];
  const { recurring } = buildDigest(findings, []);
  assert.equal(recurring.length, 2);
  const keys = recurring.map(r => r.key);
  assert.ok(keys.includes('capacity.throttle::CapA'));
  assert.ok(keys.includes('pipeline.failing::PipeY'));
});

test('recurring entry has key, recurringRuns, and level', () => {
  const findings = [
    { key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'r' }, recurringRuns: 4 },
  ];
  const { recurring } = buildDigest(findings, []);
  assert.equal(recurring[0].key, 'capacity.throttle::CapA');
  assert.equal(recurring[0].recurringRuns, 4);
  assert.equal(recurring[0].level, 'Critical');
});

test('keyless findings go to byDomain["other"]', () => {
  const findings = [
    { score: { level: 'Warning', reason: 'r' }, recurringRuns: 1 },
  ];
  const { byDomain, newCount } = buildDigest(findings, []);
  assert.equal(byDomain.other, 1);
  // keyless findings are NOT counted as new
  assert.equal(newCount, 0);
});

test('dotted resource name does not corrupt domain — Finance stays under capacity', () => {
  const findings = [
    { key: 'capacity.throttle::Finance.EU / Sales', score: { level: 'Critical', reason: 'r' }, recurringRuns: 1 },
  ];
  const { byDomain } = buildDigest(findings, []);
  assert.equal(byDomain.capacity, 1, 'should count under capacity');
  assert.ok(!('Finance' in byDomain), 'Finance must not appear as a domain key');
});

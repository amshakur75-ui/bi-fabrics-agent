import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyEscalation } from './escalate.js';

const makeRun = (keys) => ({ runAt: '2026-01-01T00:00:00Z', findings: keys.map(k => ({ key: k, level: 'Warning' })) });

test('Warning present in last 2 history runs becomes Critical', () => {
  const findings = [
    { key: 'model.bidirectional::DatasetA', score: { level: 'Warning', reason: 'bidir' } },
  ];
  const history = [
    makeRun(['model.bidirectional::DatasetA']),
    makeRun(['model.bidirectional::DatasetA']),
  ];
  const result = applyEscalation(findings, history);
  assert.equal(result[0].score.level, 'Critical');
  assert.match(result[0].score.reason, /escalated: unresolved 3 consecutive runs/);
});

test('Warning present in only 1 prior run stays Warning', () => {
  const findings = [
    { key: 'model.bidirectional::DatasetA', score: { level: 'Warning', reason: 'bidir' } },
  ];
  const history = [
    makeRun([]),                                    // not present
    makeRun(['model.bidirectional::DatasetA']),     // only 1 hit
  ];
  const result = applyEscalation(findings, history);
  assert.equal(result[0].score.level, 'Warning');
});

test('fewer than 2 prior runs leaves findings unchanged', () => {
  const findings = [
    { key: 'model.bidirectional::DatasetA', score: { level: 'Warning', reason: 'bidir' } },
  ];
  const history = [makeRun(['model.bidirectional::DatasetA'])]; // only 1 prior run
  const result = applyEscalation(findings, history);
  assert.equal(result[0].score.level, 'Warning');
});

test('empty history leaves findings unchanged', () => {
  const findings = [
    { key: 'capacity.throttle::CapA', score: { level: 'Warning', reason: 'throttle' } },
  ];
  const result = applyEscalation(findings, []);
  assert.equal(result[0].score.level, 'Warning');
});

test('non-Warning findings are not escalated even if recurring', () => {
  const findings = [
    { key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'already critical' } },
  ];
  const history = [
    makeRun(['capacity.throttle::CapA']),
    makeRun(['capacity.throttle::CapA']),
  ];
  const result = applyEscalation(findings, history);
  assert.equal(result[0].score.level, 'Critical');
  assert.equal(result[0].score.reason, 'already critical'); // unchanged
});

test('does not mutate original findings array', () => {
  const findings = [
    { key: 'model.bidirectional::DatasetA', score: { level: 'Warning', reason: 'bidir' } },
  ];
  const history = [
    makeRun(['model.bidirectional::DatasetA']),
    makeRun(['model.bidirectional::DatasetA']),
  ];
  applyEscalation(findings, history);
  assert.equal(findings[0].score.level, 'Warning'); // original untouched
});

test('keyless finding is never escalated', () => {
  const findings = [
    { score: { level: 'Warning', reason: 'keyless' } },
  ];
  const history = [makeRun([]), makeRun([])];
  const result = applyEscalation(findings, history);
  assert.equal(result[0].score.level, 'Warning');
});

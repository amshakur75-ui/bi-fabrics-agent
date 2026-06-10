import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annotateRecurring } from './trend.js';

const makeRun = (keys) => ({ runAt: '2026-01-01T00:00:00Z', findings: keys.map(k => ({ key: k })) });

test('recurringRuns counts current run (1) when no history', () => {
  const findings = [{ key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'x' } }];
  const result = annotateRecurring(findings, []);
  assert.equal(result[0].recurringRuns, 1);
});

test('recurringRuns counts current + prior hits correctly', () => {
  const findings = [{ key: 'capacity.throttle::CapA', score: { level: 'Critical', reason: 'x' } }];
  const history = [
    makeRun(['capacity.throttle::CapA']),
    makeRun(['capacity.throttle::CapA']),
    makeRun([]), // run where it was absent
  ];
  const result = annotateRecurring(findings, history);
  // 2 prior hits + 1 (current) = 3
  assert.equal(result[0].recurringRuns, 3);
});

test('respects the window — only looks at last N runs', () => {
  const findings = [{ key: 'model.bidirectional::DatasetB', score: { level: 'Warning', reason: 'r' } }];
  // 5 runs before the window, 3 within window (window=3) — all have the key
  const old = Array.from({ length: 5 }, () => makeRun(['model.bidirectional::DatasetB']));
  const recent = Array.from({ length: 3 }, () => makeRun(['model.bidirectional::DatasetB']));
  const history = [...old, ...recent];
  const result = annotateRecurring(findings, history, 3);
  // window=3: 3 prior hits + 1 current = 4
  assert.equal(result[0].recurringRuns, 4);
});

test('keyless findings get recurringRuns = 1 regardless of history', () => {
  const findings = [{ score: { level: 'Warning', reason: 'keyless' } }];
  const history = [makeRun([]), makeRun([])];
  const result = annotateRecurring(findings, history);
  assert.equal(result[0].recurringRuns, 1);
});

test('does not mutate original findings', () => {
  const findings = [{ key: 'x::1', score: { level: 'Critical', reason: 'r' } }];
  const history = [makeRun(['x::1'])];
  annotateRecurring(findings, history);
  assert.equal(findings[0].recurringRuns, undefined);
});

test('finding absent from all history runs gets recurringRuns = 1', () => {
  const findings = [{ key: 'new.finding::Resource', score: { level: 'Warning', reason: 'new' } }];
  const history = [makeRun(['other.key::Other']), makeRun(['other.key::Other'])];
  const result = annotateRecurring(findings, history);
  assert.equal(result[0].recurringRuns, 1);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assessOutcomes, summarizeOutcomes } from './outcomes.js';

// ---------------------------------------------------------------------------
// assessOutcomes — resolved-since-last
// ---------------------------------------------------------------------------

test('assessOutcomes: prev active [A,B,C], current [A,C] → resolvedSinceLast [B]', () => {
  const history = [{ findings: [{ key: 'A' }, { key: 'B' }, { key: 'C' }] }];
  const current = [{ key: 'A' }, { key: 'C' }];
  const { resolvedSinceLast } = assessOutcomes(current, history, null);
  assert.deepStrictEqual(resolvedSinceLast, ['B']);
});

test('assessOutcomes: suppressed prev findings are NOT counted as active (absent now is not reported resolved)', () => {
  const history = [{
    findings: [
      { key: 'A' },
      { key: 'B' },
      { key: 'D', suppressed: true }, // suppressed — should not be reported as resolved
    ],
  }];
  const current = [{ key: 'A' }]; // B is gone (resolved), D was suppressed so not reported
  const { resolvedSinceLast } = assessOutcomes(current, history, null);
  assert.ok(resolvedSinceLast.includes('B'), 'B should be reported as resolved');
  assert.ok(!resolvedSinceLast.includes('D'), 'D was suppressed, should not be reported resolved');
});

// ---------------------------------------------------------------------------
// assessOutcomes — metricDelta
// ---------------------------------------------------------------------------

test('assessOutcomes: prev metrics.peakCuPct=96, current=78 → improved:true, change:-18', () => {
  const history = [{ metrics: { peakCuPct: 96 }, findings: [] }];
  const { metricDelta } = assessOutcomes([], history, 78);
  assert.ok(metricDelta, 'metricDelta should be present');
  assert.equal(metricDelta.from, 96);
  assert.equal(metricDelta.to, 78);
  assert.equal(metricDelta.change, -18);
  assert.equal(metricDelta.improved, true);
  assert.equal(metricDelta.metric, 'peakCuPct');
});

test('assessOutcomes: prev metrics.peakCuPct=96, current=99 → improved:false', () => {
  const history = [{ metrics: { peakCuPct: 96 }, findings: [] }];
  const { metricDelta } = assessOutcomes([], history, 99);
  assert.ok(metricDelta, 'metricDelta should be present');
  assert.equal(metricDelta.improved, false);
  assert.equal(metricDelta.change, 3);
});

test('assessOutcomes: no history → resolvedSinceLast empty, metricDelta null', () => {
  const result = assessOutcomes([{ key: 'A' }], [], 78);
  assert.deepStrictEqual(result.resolvedSinceLast, []);
  assert.equal(result.metricDelta, null);
});

test('assessOutcomes: prev run has no metrics → metricDelta null even with currentMetric', () => {
  const history = [{ findings: [{ key: 'A' }, { key: 'B' }] }]; // no metrics
  const { metricDelta } = assessOutcomes([{ key: 'A' }], history, 78);
  assert.equal(metricDelta, null);
});

test('assessOutcomes: currentMetric null → metricDelta null', () => {
  const history = [{ metrics: { peakCuPct: 96 }, findings: [] }];
  const { metricDelta } = assessOutcomes([], history, null);
  assert.equal(metricDelta, null);
});

test('assessOutcomes: same metric value → change 0, improved false', () => {
  const history = [{ metrics: { peakCuPct: 96 }, findings: [] }];
  const { metricDelta } = assessOutcomes([], history, 96);
  assert.equal(metricDelta.change, 0);
  assert.equal(metricDelta.improved, false);
});

// ---------------------------------------------------------------------------
// summarizeOutcomes
// ---------------------------------------------------------------------------

test('summarizeOutcomes: resolved-only → sentence about resolved findings', () => {
  const s = summarizeOutcomes({ resolvedSinceLast: ['A', 'B'], metricDelta: null });
  assert.match(s, /2 finding\(s\) resolved since the last run/);
});

test('summarizeOutcomes: metric-only improved → sentence about peak CU improved', () => {
  const s = summarizeOutcomes({
    resolvedSinceLast: [],
    metricDelta: { metric: 'peakCuPct', from: 96, to: 78, change: -18, improved: true },
  });
  assert.match(s, /peak CU improved 96% → 78%/);
});

test('summarizeOutcomes: metric-only not improved → sentence with "rose"', () => {
  const s = summarizeOutcomes({
    resolvedSinceLast: [],
    metricDelta: { metric: 'peakCuPct', from: 78, to: 96, change: 18, improved: false },
  });
  assert.match(s, /peak CU rose 78% → 96%/);
});

test('summarizeOutcomes: both resolved + metric → joined with semicolon', () => {
  const s = summarizeOutcomes({
    resolvedSinceLast: ['X'],
    metricDelta: { metric: 'peakCuPct', from: 96, to: 78, change: -18, improved: true },
  });
  assert.match(s, /1 finding\(s\) resolved since the last run/);
  assert.match(s, /peak CU improved/);
  assert.ok(s.includes('; '), 'parts should be joined with "; "');
});

test('summarizeOutcomes: nothing to report → empty string', () => {
  const s = summarizeOutcomes({ resolvedSinceLast: [], metricDelta: null });
  assert.equal(s, '');
});

test('summarizeOutcomes: default arg → empty string', () => {
  const s = summarizeOutcomes();
  assert.equal(s, '');
});

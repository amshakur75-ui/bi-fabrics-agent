import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annotateAccountability, summarizeAccountability } from './accountability.js';

// ---------------------------------------------------------------------------
// annotateAccountability
// ---------------------------------------------------------------------------

const HISTORY = [
  { runAt: '2026-01-01T00:00:00Z', findings: [{ key: 'model.bidirectional::DatasetX' }] },
  { runAt: '2026-01-02T00:00:00Z', findings: [{ key: 'model.bidirectional::DatasetX' }] },
];

test('annotateAccountability: recurringRuns >= threshold + open → gets accountability with correct fields', () => {
  const findings = [{
    key: 'model.bidirectional::DatasetX',
    recurringRuns: 3,
    lifecycle: { state: 'open' },
    score: { level: 'Warning', reason: 'bidir' },
  }];
  const result = annotateAccountability(findings, HISTORY);
  assert.equal(result.length, 1);
  assert.ok(result[0].accountability, 'accountability should be set');
  assert.equal(result[0].accountability.openRuns, 3);
  assert.equal(result[0].accountability.firstSeen, '2026-01-01T00:00:00Z');
  assert.match(result[0].accountability.message, /Open for 3 consecutive run\(s\) with no resolution/);
});

test('annotateAccountability: acknowledged lifecycle → NOT annotated', () => {
  const findings = [{
    key: 'model.bidirectional::DatasetX',
    recurringRuns: 3,
    lifecycle: { state: 'acknowledged' },
    score: { level: 'Warning', reason: 'bidir' },
  }];
  const result = annotateAccountability(findings, HISTORY);
  assert.equal(result[0].accountability, undefined, 'acknowledged finding should not be annotated');
});

test('annotateAccountability: recurringRuns < threshold → NOT annotated', () => {
  const findings = [{
    key: 'model.bidirectional::DatasetX',
    recurringRuns: 2,
    lifecycle: { state: 'open' },
    score: { level: 'Warning', reason: 'bidir' },
  }];
  const result = annotateAccountability(findings, HISTORY);
  assert.equal(result[0].accountability, undefined, 'below-threshold finding should not be annotated');
});

test('annotateAccountability: no lifecycle field defaults to open → annotated if recurring enough', () => {
  const findings = [{
    key: 'model.bidirectional::DatasetX',
    recurringRuns: 3,
    score: { level: 'Warning', reason: 'bidir' },
    // no lifecycle key
  }];
  const result = annotateAccountability(findings, HISTORY);
  assert.ok(result[0].accountability, 'missing lifecycle should default to open and be annotated');
  assert.equal(result[0].accountability.openRuns, 3);
});

test('annotateAccountability: firstSeen pulled from history; null when key not in history', () => {
  const findings = [{
    key: 'unknown::key',
    recurringRuns: 5,
    score: { level: 'Critical', reason: 'x' },
  }];
  const result = annotateAccountability(findings, HISTORY);
  assert.ok(result[0].accountability, 'should be annotated');
  assert.equal(result[0].accountability.firstSeen, null, 'firstSeen null when key not in history');
});

test('annotateAccountability: pure — inputs not mutated', () => {
  const original = {
    key: 'model.bidirectional::DatasetX',
    recurringRuns: 3,
    lifecycle: { state: 'open' },
    score: { level: 'Warning', reason: 'bidir' },
  };
  const findings = [original];
  const historyCopy = [...HISTORY];
  annotateAccountability(findings, HISTORY);
  assert.equal(original.accountability, undefined, 'original finding should not be mutated');
  assert.deepEqual(HISTORY, historyCopy, 'history should not be mutated');
});

// ---------------------------------------------------------------------------
// summarizeAccountability
// ---------------------------------------------------------------------------

test('summarizeAccountability: returns correct ignoredCount and items', () => {
  const findings = [
    {
      key: 'model.bidirectional::DatasetX',
      recurringRuns: 3,
      accountability: { openRuns: 3, firstSeen: '2026-01-01T00:00:00Z', message: 'Open for 3 consecutive run(s) with no resolution.' },
    },
    {
      key: 'capacity.throttle::F64',
      recurringRuns: 1,
      // no accountability
    },
  ];
  const summary = summarizeAccountability(findings);
  assert.equal(summary.ignoredCount, 1);
  assert.equal(summary.items.length, 1);
  assert.equal(summary.items[0].key, 'model.bidirectional::DatasetX');
  assert.equal(summary.items[0].openRuns, 3);
  assert.equal(summary.items[0].firstSeen, '2026-01-01T00:00:00Z');
});

test('summarizeAccountability: empty findings → ignoredCount 0 and empty items', () => {
  const summary = summarizeAccountability([]);
  assert.equal(summary.ignoredCount, 0);
  assert.deepEqual(summary.items, []);
});

test('summarizeAccountability: no accountable findings → ignoredCount 0', () => {
  const findings = [{ key: 'x', recurringRuns: 1 }, { key: 'y', recurringRuns: 2 }];
  const summary = summarizeAccountability(findings);
  assert.equal(summary.ignoredCount, 0);
  assert.deepEqual(summary.items, []);
});

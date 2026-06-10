import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assessSla, summarizeSla } from './sla.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DAY_MS = 86_400_000;

/** Build a history array with a single run that has the given key. */
function makeHistory(key, runAt) {
  return [{ runAt, findings: [{ key }] }];
}

// ---------------------------------------------------------------------------
// assessSla
// ---------------------------------------------------------------------------

test('assessSla: Critical finding 5 days old → sla.breached === true (target 1 day, age 5)', () => {
  const firstSeenAt = '2026-06-01T00:00:00Z';
  const nowMs = Date.parse('2026-06-06T00:00:00Z');
  const findings = [{ key: 'capacity.throttle::F64', score: { level: 'Critical', reason: 'x' } }];
  const history = makeHistory('capacity.throttle::F64', firstSeenAt);
  const result = assessSla(findings, history, nowMs);
  assert.equal(result.length, 1);
  assert.ok(result[0].sla, 'sla annotation should be present');
  assert.equal(result[0].sla.ageDays, 5);
  assert.equal(result[0].sla.targetDays, 1);
  assert.equal(result[0].sla.breached, true);
});

test('assessSla: Warning finding 2 days old → breached === false (target 7)', () => {
  const firstSeenAt = '2026-06-04T00:00:00Z';
  const nowMs = Date.parse('2026-06-06T00:00:00Z');
  const findings = [{ key: 'model.bidirectional::DS', score: { level: 'Warning', reason: 'x' } }];
  const history = makeHistory('model.bidirectional::DS', firstSeenAt);
  const result = assessSla(findings, history, nowMs);
  assert.ok(result[0].sla, 'sla annotation should be present');
  assert.equal(result[0].sla.ageDays, 2);
  assert.equal(result[0].sla.targetDays, 7);
  assert.equal(result[0].sla.breached, false);
});

test('assessSla: nowMs = 0 → no sla annotation', () => {
  const findings = [{ key: 'capacity.throttle::F64', score: { level: 'Critical', reason: 'x' } }];
  const history = makeHistory('capacity.throttle::F64', '2026-06-01T00:00:00Z');
  const result = assessSla(findings, history, 0);
  assert.equal(result[0].sla, undefined, 'nowMs=0 should disable sla annotation');
});

test('assessSla: finding key absent from history → no sla annotation', () => {
  const findings = [{ key: 'capacity.throttle::Unknown', score: { level: 'Critical', reason: 'x' } }];
  const history = makeHistory('model.bidirectional::OtherDS', '2026-06-01T00:00:00Z');
  const nowMs = Date.parse('2026-06-06T00:00:00Z');
  const result = assessSla(findings, history, nowMs);
  assert.equal(result[0].sla, undefined, 'key absent from history should produce no sla annotation');
});

test('assessSla: empty history → no sla annotation', () => {
  const findings = [{ key: 'capacity.throttle::F64', score: { level: 'Critical', reason: 'x' } }];
  const result = assessSla(findings, [], Date.parse('2026-06-06T00:00:00Z'));
  assert.equal(result[0].sla, undefined, 'empty history → no sla annotation');
});

test('assessSla: sanity check — key first-seen 9 days ago with Critical → breached (age 9 > target 1)', () => {
  // The brief's explicit sanity check
  const history = [{ runAt: '2026-05-30T00:00:00Z', findings: [{ key: 'k' }] }];
  const findings = [{ key: 'k', score: { level: 'Critical', reason: 'x' } }];
  const nowMs = Date.parse('2026-06-08T00:00:00Z');
  const result = assessSla(findings, history, nowMs);
  assert.equal(result[0].sla.breached, true, 'age ~9 days > target 1 should be breached');
});

test('assessSla: malformed runAt (non-date string) → finding returned unmodified, no sla annotation', () => {
  const findings = [{ key: 'capacity.throttle::F64', score: { level: 'Critical', reason: 'x' } }];
  const history = makeHistory('capacity.throttle::F64', 'not-a-date');
  const nowMs = Date.parse('2026-06-06T00:00:00Z');
  const result = assessSla(findings, history, nowMs);
  assert.equal(result.length, 1);
  assert.equal(result[0].sla, undefined, 'malformed runAt should produce no sla annotation');
  assert.deepEqual(result[0], findings[0], 'finding should be returned unmodified');
});

test('assessSla: pure — original finding not mutated', () => {
  const original = { key: 'capacity.throttle::F64', score: { level: 'Critical', reason: 'x' } };
  const findings = [original];
  const history = makeHistory('capacity.throttle::F64', '2026-06-01T00:00:00Z');
  assessSla(findings, history, Date.parse('2026-06-06T00:00:00Z'));
  assert.equal(original.sla, undefined, 'original finding should not be mutated');
});

// ---------------------------------------------------------------------------
// summarizeSla
// ---------------------------------------------------------------------------

test('summarizeSla: returns correct breachedCount and items', () => {
  const findings = [
    { key: 'capacity.throttle::F64', score: { level: 'Critical' }, sla: { ageDays: 5, targetDays: 1, breached: true } },
    { key: 'model.bidirectional::DS', score: { level: 'Warning' }, sla: { ageDays: 2, targetDays: 7, breached: false } },
    { key: 'report.slow::Sales', score: { level: 'Warning' } }, // no sla
  ];
  const summary = summarizeSla(findings);
  assert.equal(summary.breachedCount, 1);
  assert.equal(summary.items.length, 1);
  assert.equal(summary.items[0].key, 'capacity.throttle::F64');
  assert.equal(summary.items[0].level, 'Critical');
  assert.equal(summary.items[0].ageDays, 5);
  assert.equal(summary.items[0].targetDays, 1);
});

test('summarizeSla: empty findings → breachedCount 0 and empty items', () => {
  const summary = summarizeSla([]);
  assert.equal(summary.breachedCount, 0);
  assert.deepEqual(summary.items, []);
});

test('summarizeSla: no breached findings → breachedCount 0', () => {
  const findings = [
    { key: 'k1', score: { level: 'Warning' }, sla: { ageDays: 2, targetDays: 7, breached: false } },
  ];
  const summary = summarizeSla(findings);
  assert.equal(summary.breachedCount, 0);
  assert.deepEqual(summary.items, []);
});

test('summarizeSla: multiple breaches → correct count and all items present', () => {
  const findings = [
    { key: 'a', score: { level: 'Critical' }, sla: { ageDays: 5, targetDays: 1, breached: true } },
    { key: 'b', score: { level: 'Critical' }, sla: { ageDays: 3, targetDays: 1, breached: true } },
    { key: 'c', score: { level: 'Warning' }, sla: { ageDays: 2, targetDays: 7, breached: false } },
  ];
  const summary = summarizeSla(findings);
  assert.equal(summary.breachedCount, 2);
  assert.equal(summary.items.length, 2);
  assert.ok(summary.items.some(i => i.key === 'a'));
  assert.ok(summary.items.some(i => i.key === 'b'));
});

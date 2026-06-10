import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyLifecycle, setState, DEFAULT_LIFECYCLE } from './lifecycle.js';

// ---------------------------------------------------------------------------
// applyLifecycle
// ---------------------------------------------------------------------------

const makeFindings = (keys) => keys.map(key => ({
  key,
  what: `Finding ${key}`,
  where: 'Workspace',
  score: { level: 'Warning', reason: 'test' },
}));

test('applyLifecycle: no state entry → finding is active with lifecycle.state "open"', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const { active, suppressed } = applyLifecycle(findings, {}, 0);
  assert.equal(active.length, 1);
  assert.equal(suppressed.length, 0);
  assert.equal(active[0].lifecycle.state, 'open');
});

test('applyLifecycle: "acknowledged" state → active', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const states = { 'capacity.throttle::CapA': { state: 'acknowledged', since: '2026-01-01T00:00:00Z', snoozeUntil: null, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, 0);
  assert.equal(active.length, 1);
  assert.equal(suppressed.length, 0);
  assert.equal(active[0].lifecycle.state, 'acknowledged');
});

test('applyLifecycle: "resolved" state → suppressed', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const states = { 'capacity.throttle::CapA': { state: 'resolved', since: '2026-01-01T00:00:00Z', snoozeUntil: null, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, 0);
  assert.equal(active.length, 0);
  assert.equal(suppressed.length, 1);
  assert.equal(suppressed[0].lifecycle.state, 'resolved');
});

test('applyLifecycle: "wontfix" state → suppressed', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const states = { 'capacity.throttle::CapA': { state: 'wontfix', since: '2026-01-01T00:00:00Z', snoozeUntil: null, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, 0);
  assert.equal(active.length, 0);
  assert.equal(suppressed.length, 1);
  assert.equal(suppressed[0].lifecycle.state, 'wontfix');
});

test('applyLifecycle: "snoozed" with future snoozeUntil → suppressed', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const futureDate = '2099-01-01T00:00:00Z';
  const nowMs = Date.parse('2026-01-01T00:00:00Z');
  const states = { 'capacity.throttle::CapA': { state: 'snoozed', since: '2026-01-01T00:00:00Z', snoozeUntil: futureDate, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, nowMs);
  assert.equal(active.length, 0);
  assert.equal(suppressed.length, 1);
  assert.equal(suppressed[0].lifecycle.state, 'snoozed');
});

test('applyLifecycle: "snoozed" with past snoozeUntil → reactivated to active with state "open"', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const pastDate = '2020-01-01T00:00:00Z';
  const nowMs = Date.parse('2026-01-01T00:00:00Z');
  const states = { 'capacity.throttle::CapA': { state: 'snoozed', since: '2020-01-01T00:00:00Z', snoozeUntil: pastDate, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, nowMs);
  assert.equal(active.length, 1);
  assert.equal(suppressed.length, 0);
  assert.equal(active[0].lifecycle.state, 'open');
  assert.equal(active[0].lifecycle.snoozeUntil, null);
});

test('applyLifecycle: mixed states split correctly', () => {
  const findings = makeFindings([
    'capacity.throttle::CapA',
    'capacity.throttle::CapB',
    'model.bidirectional::DatasetX',
  ]);
  const states = {
    'capacity.throttle::CapA': { state: 'resolved', since: null, snoozeUntil: null, note: null },
    'model.bidirectional::DatasetX': { state: 'acknowledged', since: null, snoozeUntil: null, note: null },
  };
  const { active, suppressed } = applyLifecycle(findings, states, 0);
  // CapA is resolved → suppressed; CapB has no state → open → active; DatasetX acknowledged → active
  assert.equal(active.length, 2);
  assert.equal(suppressed.length, 1);
  const activeKeys = active.map(f => f.key);
  assert.ok(activeKeys.includes('capacity.throttle::CapB'));
  assert.ok(activeKeys.includes('model.bidirectional::DatasetX'));
  assert.equal(suppressed[0].key, 'capacity.throttle::CapA');
});

test('applyLifecycle: annotated finding carries lifecycle object from DEFAULT_LIFECYCLE when no state', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const { active } = applyLifecycle(findings, {}, 0);
  assert.deepEqual(active[0].lifecycle, { ...DEFAULT_LIFECYCLE, state: 'open' });
});

// ---------------------------------------------------------------------------
// setState
// ---------------------------------------------------------------------------

test('setState: returns new map with the record', () => {
  const states = {};
  const result = setState(states, 'capacity.throttle::CapA', 'resolved', { now: '2026-01-01T00:00:00Z', note: 'Fixed' });
  assert.ok('capacity.throttle::CapA' in result);
  assert.equal(result['capacity.throttle::CapA'].state, 'resolved');
  assert.equal(result['capacity.throttle::CapA'].since, '2026-01-01T00:00:00Z');
  assert.equal(result['capacity.throttle::CapA'].note, 'Fixed');
  assert.equal(result['capacity.throttle::CapA'].snoozeUntil, null);
});

test('setState: does NOT mutate the input map', () => {
  const states = { 'k1': { state: 'open', since: null, snoozeUntil: null, note: null } };
  const before = JSON.stringify(states);
  setState(states, 'k2', 'acknowledged', { now: '2026-01-01T00:00:00Z' });
  assert.equal(JSON.stringify(states), before, 'original states map must not be mutated');
});

test('setState: preserves existing keys in the returned map', () => {
  const states = { 'k1': { state: 'open', since: null, snoozeUntil: null, note: null } };
  const result = setState(states, 'k2', 'wontfix', {});
  assert.ok('k1' in result);
  assert.ok('k2' in result);
});

test('setState: snoozeUntil is set when provided', () => {
  const result = setState({}, 'k', 'snoozed', { snoozeUntil: '2099-01-01T00:00:00Z' });
  assert.equal(result['k'].snoozeUntil, '2099-01-01T00:00:00Z');
});

// ---------------------------------------------------------------------------
// Fix 1: snooze-expiry edge cases
// ---------------------------------------------------------------------------

test('applyLifecycle: snoozeUntil exactly equal to nowMs stays suppressed (< not <=)', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const exactDate = '2026-06-01T12:00:00Z';
  const nowMs = Date.parse(exactDate);
  const states = { 'capacity.throttle::CapA': { state: 'snoozed', since: '2026-01-01T00:00:00Z', snoozeUntil: exactDate, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, nowMs);
  assert.equal(active.length, 0, 'snoozeUntil == nowMs should remain suppressed');
  assert.equal(suppressed.length, 1);
  assert.equal(suppressed[0].lifecycle.state, 'snoozed');
});

test('applyLifecycle: nowMs=0 with epoch snoozeUntil stays suppressed (expiry disabled)', () => {
  const findings = makeFindings(['capacity.throttle::CapA']);
  const epochDate = '1970-01-01T00:00:00Z';
  const states = { 'capacity.throttle::CapA': { state: 'snoozed', since: '2026-01-01T00:00:00Z', snoozeUntil: epochDate, note: null } };
  const { active, suppressed } = applyLifecycle(findings, states, 0);
  assert.equal(active.length, 0, 'nowMs=0 disables expiry check so finding stays suppressed');
  assert.equal(suppressed.length, 1);
  assert.equal(suppressed[0].lifecycle.state, 'snoozed');
});

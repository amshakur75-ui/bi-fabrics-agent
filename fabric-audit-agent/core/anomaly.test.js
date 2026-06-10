import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectAnomalies } from './anomaly.js';

// Helper to build history records
function h(pct) { return { metrics: { peakCuPct: pct } }; }

test('detectAnomalies: stable baseline [70,72,71,69] + current 95 → one anomaly, direction above, sigma > 2', () => {
  const facts = { capacity: { peakCuPct: 95, capacityId: 'F64' } };
  const history = [h(70), h(72), h(71), h(69)];
  const result = detectAnomalies(facts, history);
  assert.equal(result.length, 1, 'expected exactly one anomaly');
  const a = result[0];
  assert.equal(a.direction, 'above', 'direction should be above');
  assert.ok(Math.abs(a.sigma) > 2, `sigma should be > 2, got ${a.sigma}`);
  assert.equal(a.metric, 'peakCuPct');
  assert.equal(a.current, 95);
  assert.ok(typeof a.message === 'string' && a.message.length > 0, 'message should be a non-empty string');
});

test('detectAnomalies: current within baseline (71) → no anomaly', () => {
  const facts = { capacity: { peakCuPct: 71, capacityId: 'F64' } };
  const history = [h(70), h(72), h(71), h(69)];
  const result = detectAnomalies(facts, history);
  assert.equal(result.length, 0, 'expected no anomalies for in-range value');
});

test('detectAnomalies: fewer than minPoints (4) history points → no anomaly', () => {
  const facts = { capacity: { peakCuPct: 95, capacityId: 'F64' } };
  // Only 3 history records (< minPoints default of 4)
  const result = detectAnomalies(facts, [h(70), h(72), h(71)]);
  assert.equal(result.length, 0, 'expected no anomaly with fewer than minPoints history records');
});

test('detectAnomalies: zero-variance baseline [80,80,80,80] + current 81 → no anomaly (stddev 0 guard)', () => {
  const facts = { capacity: { peakCuPct: 81, capacityId: 'F64' } };
  const history = [h(80), h(80), h(80), h(80)];
  const result = detectAnomalies(facts, history);
  assert.equal(result.length, 0, 'expected no anomaly when stddev is 0');
});

test('detectAnomalies: missing facts.capacity.peakCuPct → no anomaly, no throw', () => {
  // facts without peakCuPct
  const facts = { capacity: { capacityId: 'F64' } };
  const history = [h(70), h(72), h(71), h(69)];
  let result;
  assert.doesNotThrow(() => { result = detectAnomalies(facts, history); });
  assert.equal(result.length, 0, 'expected no anomaly when current is missing');
});

test('detectAnomalies: empty facts → no anomaly, no throw', () => {
  let result;
  assert.doesNotThrow(() => { result = detectAnomalies({}, [h(70), h(72), h(71), h(69)]); });
  assert.equal(result.length, 0);
});

test('detectAnomalies: sanity check from spec — [70,72,71,69] history + current 95 → direction above', () => {
  const result = detectAnomalies(
    { capacity: { peakCuPct: 95, capacityId: 'F64' } },
    [{ metrics: { peakCuPct: 70 } }, { metrics: { peakCuPct: 72 } }, { metrics: { peakCuPct: 71 } }, { metrics: { peakCuPct: 69 } }],
  );
  assert.equal(result.length, 1);
  assert.equal(result[0].direction, 'above');
});

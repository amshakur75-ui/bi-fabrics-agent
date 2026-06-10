import { test } from 'node:test';
import assert from 'node:assert/strict';
import { forecastCapacity } from './forecast.js';

// Helper to build history records
function h(pct) { return { metrics: { peakCuPct: pct } }; }

test('forecastCapacity: rising series → trend rising, runsToCeiling 2', () => {
  const result = forecastCapacity([h(80), h(85), h(90)]);
  assert.equal(result.trend, 'rising');
  assert.equal(result.current, 90);
  assert.equal(result.slopePerRun, 5);
  assert.equal(result.runsToCeiling, Math.ceil((100 - 90) / 5)); // 2
  assert.match(result.message, /~2/);
});

test('forecastCapacity: flat series [80,80,80] → trend flat, runsToCeiling null', () => {
  const result = forecastCapacity([h(80), h(80), h(80)]);
  assert.equal(result.trend, 'flat');
  assert.equal(result.runsToCeiling, null);
});

test('forecastCapacity: falling series [90,85,80] → trend falling, runsToCeiling null', () => {
  const result = forecastCapacity([h(90), h(85), h(80)]);
  assert.equal(result.trend, 'falling');
  assert.equal(result.runsToCeiling, null);
});

test('forecastCapacity: fewer than 3 points → insufficient-data', () => {
  const one = forecastCapacity([h(80)]);
  assert.equal(one.trend, 'insufficient-data');
  assert.equal(one.points, 1);

  const two = forecastCapacity([h(80), h(85)]);
  assert.equal(two.trend, 'insufficient-data');
  assert.equal(two.points, 2);

  const zero = forecastCapacity([]);
  assert.equal(zero.trend, 'insufficient-data');
  assert.equal(zero.points, 0);
});

test('forecastCapacity: records missing metrics/peakCuPct are skipped', () => {
  // Only 2 valid numeric points (80, 90) after filtering — insufficient-data
  const history = [
    {},
    { metrics: {} },
    { metrics: { peakCuPct: 80 } },
    { metrics: { peakCuPct: null } },
    { metrics: { peakCuPct: 90 } },
  ];
  const result = forecastCapacity(history);
  // 2 valid points → insufficient-data
  assert.equal(result.trend, 'insufficient-data');
  assert.equal(result.points, 2);
});

test('forecastCapacity: records missing metrics/peakCuPct are skipped (3 valid → calculates)', () => {
  const history = [
    {},
    { metrics: { peakCuPct: 80 } },
    { metrics: {} },
    { metrics: { peakCuPct: 85 } },
    { metrics: { peakCuPct: 90 } },
  ];
  const result = forecastCapacity(history);
  assert.equal(result.trend, 'rising');
  assert.equal(result.points, 3);
});

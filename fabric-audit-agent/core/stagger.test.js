import { test } from 'node:test';
import assert from 'node:assert/strict';
import { planStagger } from './stagger.js';

// Estate fixture refreshes (06:00 group: Sales 4.2 GB, Forecast 2.1 GB, Logistics 1.4 GB)
const estateRefreshes = [
  { workspace: 'Finance', dataset: 'Sales',     scheduledAt: '06:00', sizeGB: 4.2 },
  { workspace: 'Finance', dataset: 'Forecast',  scheduledAt: '06:00', sizeGB: 2.1 },
  { workspace: 'Ops',     dataset: 'Logistics', scheduledAt: '06:00', sizeGB: 1.4 },
  { workspace: 'HR',      dataset: 'Headcount', scheduledAt: '09:00', sizeGB: 0.3 },
];

test('estate 06:00 group → Forecast 06:00→06:15, Logistics 06:00→06:30; Sales (largest) not in plan', () => {
  const plan = planStagger({ capacity: { refreshes: estateRefreshes } });
  // Sales is largest, keeps its slot — not in the plan
  assert.ok(!plan.some(p => p.dataset === 'Sales'), 'Sales should not appear in plan (keeps its slot)');
  // Forecast pushed to 06:15
  const forecast = plan.find(p => p.dataset === 'Forecast');
  assert.ok(forecast, 'Forecast should be in the stagger plan');
  assert.equal(forecast.from, '06:00');
  assert.equal(forecast.to, '06:15');
  // Logistics pushed to 06:30
  const logistics = plan.find(p => p.dataset === 'Logistics');
  assert.ok(logistics, 'Logistics should be in the stagger plan');
  assert.equal(logistics.from, '06:00');
  assert.equal(logistics.to, '06:30');
  // 09:00 group has only 1 entry → no collision → not in plan
  assert.ok(!plan.some(p => p.dataset === 'Headcount'), 'Headcount (single at 09:00) should not appear');
  assert.equal(plan.length, 2, 'plan should have exactly 2 entries');
});

test('single refresh at a time (no collision) → empty plan', () => {
  const plan = planStagger({ capacity: { refreshes: [
    { workspace: 'HR', dataset: 'Headcount', scheduledAt: '09:00', sizeGB: 0.3 },
  ] } });
  assert.deepEqual(plan, []);
});

test('midnight wrap: a 2-entry group at 23:50 with spacingMin=20 → second entry moves to 00:10', () => {
  const plan = planStagger({ capacity: { refreshes: [
    { workspace: 'WS', dataset: 'BigModel',   scheduledAt: '23:50', sizeGB: 2.0 },
    { workspace: 'WS', dataset: 'SmallModel', scheduledAt: '23:50', sizeGB: 0.5 },
  ] } }, { spacingMin: 20 });
  // BigModel is larger, keeps its slot (23:50), not in plan
  assert.ok(!plan.some(p => p.dataset === 'BigModel'), 'BigModel keeps its slot, should not be in plan');
  const small = plan.find(p => p.dataset === 'SmallModel');
  assert.ok(small, 'SmallModel should be in plan');
  assert.equal(small.to, '00:10', 'SmallModel should wrap to 00:10');
});

test('minGroup option honored: minGroup=4 → estate 06:00 group (3 members) produces empty plan', () => {
  const plan = planStagger({ capacity: { refreshes: estateRefreshes } }, { minGroup: 4 });
  assert.deepEqual(plan, []);
});

test('spacingMin=30 → 3-entry 06:00 group offsets at 06:00, 06:30, 07:00', () => {
  const plan = planStagger({ capacity: { refreshes: estateRefreshes } }, { spacingMin: 30 });
  // Sales (largest, 4.2 GB) keeps 06:00 → not in plan
  assert.ok(!plan.some(p => p.dataset === 'Sales'), 'Sales keeps 06:00');
  const forecast = plan.find(p => p.dataset === 'Forecast');
  assert.ok(forecast);
  assert.equal(forecast.to, '06:30');
  const logistics = plan.find(p => p.dataset === 'Logistics');
  assert.ok(logistics);
  assert.equal(logistics.to, '07:00');
});

test('empty facts → empty plan', () => {
  assert.deepEqual(planStagger(), []);
  assert.deepEqual(planStagger({}), []);
  assert.deepEqual(planStagger({ capacity: {} }), []);
  assert.deepEqual(planStagger({ capacity: { refreshes: [] } }), []);
});

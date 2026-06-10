import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectCost } from './cost.js';

test('0-view report produces cost.unused-report flag', () => {
  const facts = {
    usage: {
      reports: [
        { workspace: 'Ops', name: 'Old Quarterly', views30d: 0 },
      ],
    },
  };
  const flags = detectCost(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'cost.unused-report');
  assert.equal(flags[0].resource, 'Ops / Old Quarterly');
  assert.equal(flags[0].evidence.views30d, 0);
});

test('viewed report (views30d > 0) produces no flag', () => {
  const facts = {
    usage: {
      reports: [
        { workspace: 'Finance', name: 'Exec Dashboard', views30d: 540 },
      ],
    },
  };
  assert.deepEqual(detectCost(facts), []);
});

test('capacity with avgCuPct 3 produces cost.idle-capacity flag', () => {
  const facts = {
    usage: {
      capacities: [
        { id: 'F64', sku: 'F64', avgCuPct: 3 },
      ],
    },
  };
  const flags = detectCost(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'cost.idle-capacity');
  assert.equal(flags[0].resource, 'capacity F64');
  assert.equal(flags[0].evidence.avgCuPct, 3);
});

test('busy capacity (avgCuPct >= 5) produces no flag', () => {
  const facts = {
    usage: {
      capacities: [
        { id: 'F64', sku: 'F64', avgCuPct: 60 },
      ],
    },
  };
  assert.deepEqual(detectCost(facts), []);
});

test('detectCost({}) returns []', () => {
  assert.deepEqual(detectCost({}), []);
});

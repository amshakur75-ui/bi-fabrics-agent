import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectModels } from './model.js';

const salesModel = {
  workspace: 'Finance', name: 'Sales', sizeGB: 4.2,
  bidirectionalRels: 9, autoDateTime: true, refreshFailRatePct: 12,
  observedAt: '2026-06-08T06:00:00.000Z',
};

const headcountModel = {
  workspace: 'HR', name: 'Headcount', sizeGB: 0.3,
  bidirectionalRels: 1, autoDateTime: false, refreshFailRatePct: 0,
  observedAt: '2026-06-08T09:00:00.000Z',
};

test('Sales model yields model.bidirectional, model.auto-datetime, model.refresh-failing', () => {
  const types = detectModels({ models: [salesModel] }).map(f => f.type).sort();
  assert.deepEqual(types, ['model.auto-datetime', 'model.bidirectional', 'model.refresh-failing']);
});

test('model.bidirectional flag carries correct evidence', () => {
  const f = detectModels({ models: [salesModel] }).find(f => f.type === 'model.bidirectional');
  assert.equal(f.evidence.count, 9);
  assert.equal(f.resource, 'Finance / Sales');
});

test('model.refresh-failing flag carries failRatePct', () => {
  const f = detectModels({ models: [salesModel] }).find(f => f.type === 'model.refresh-failing');
  assert.equal(f.evidence.failRatePct, 12);
});

test('Headcount model yields no flags', () => {
  assert.deepEqual(detectModels({ models: [headcountModel] }), []);
});

test('detectModels({}) returns []', () => {
  assert.deepEqual(detectModels({}), []);
});

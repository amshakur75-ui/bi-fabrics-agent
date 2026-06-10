import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getUserTip } from './coaching.js';

test('getUserTip: report.too-many-visuals returns a non-empty string', () => {
  const tip = getUserTip('report.too-many-visuals');
  assert.ok(typeof tip === 'string' && tip.length > 0);
});

test('getUserTip: model.bidirectional returns a non-empty string', () => {
  const tip = getUserTip('model.bidirectional');
  assert.ok(typeof tip === 'string' && tip.length > 0);
});

test('getUserTip: model.auto-datetime returns a non-empty string', () => {
  const tip = getUserTip('model.auto-datetime');
  assert.ok(typeof tip === 'string' && tip.length > 0);
});

test('getUserTip: report.directquery returns a non-empty string', () => {
  const tip = getUserTip('report.directquery');
  assert.ok(typeof tip === 'string' && tip.length > 0);
});

test('getUserTip: report.slow-visual returns a non-empty string', () => {
  const tip = getUserTip('report.slow-visual');
  assert.ok(typeof tip === 'string' && tip.length > 0);
});

test('getUserTip: capacity.throttle returns null (infra/team owned)', () => {
  assert.equal(getUserTip('capacity.throttle'), null);
});

test('getUserTip: pipeline.failing returns null (infra/team owned)', () => {
  assert.equal(getUserTip('pipeline.failing'), null);
});

test('getUserTip: unknown flag type returns null', () => {
  assert.equal(getUserTip('nonexistent.flag'), null);
});

// Inc-8: coaching tips
test('getUserTip: cost.unused-report returns a non-empty string', () => {
  const tip = getUserTip('cost.unused-report');
  assert.ok(typeof tip === 'string' && tip.length > 0);
});

test('getUserTip: security.admin-grant returns null (admin/security team owned)', () => {
  assert.equal(getUserTip('security.admin-grant'), null);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_CONFIG, mergeConfig } from './config.js';

test('mergeConfig() with no args deep-equals DEFAULT_CONFIG', () => {
  const merged = mergeConfig();
  assert.deepEqual(merged, DEFAULT_CONFIG);
});

test('mergeConfig overrides only the specified key, keeps rest of domain', () => {
  const merged = mergeConfig({ capacity: { throttleWarnPct: 99 } });
  assert.equal(merged.capacity.throttleWarnPct, 99);
  // other capacity keys preserved
  assert.equal(merged.capacity.throttleCritPct, DEFAULT_CONFIG.capacity.throttleCritPct);
  assert.equal(merged.capacity.contentionMin, DEFAULT_CONFIG.capacity.contentionMin);
  assert.equal(merged.capacity.oversizedGB, DEFAULT_CONFIG.capacity.oversizedGB);
});

test('mergeConfig override in one domain does not affect other domains', () => {
  const merged = mergeConfig({ capacity: { throttleWarnPct: 99 } });
  assert.deepEqual(merged.model, DEFAULT_CONFIG.model);
  assert.deepEqual(merged.report, DEFAULT_CONFIG.report);
  assert.deepEqual(merged.pipeline, DEFAULT_CONFIG.pipeline);
  assert.deepEqual(merged.security, DEFAULT_CONFIG.security);
  assert.deepEqual(merged.cost, DEFAULT_CONFIG.cost);
});

test('mergeConfig carries through unknown domains', () => {
  const merged = mergeConfig({ custom: { foo: 42 } });
  assert.equal(merged.custom.foo, 42);
  // default domains still present
  assert.deepEqual(merged.capacity, DEFAULT_CONFIG.capacity);
});

test('DEFAULT_CONFIG has all expected domains and keys', () => {
  assert.ok('capacity' in DEFAULT_CONFIG);
  assert.ok('model' in DEFAULT_CONFIG);
  assert.ok('report' in DEFAULT_CONFIG);
  assert.ok('pipeline' in DEFAULT_CONFIG);
  assert.ok('security' in DEFAULT_CONFIG);
  assert.ok('cost' in DEFAULT_CONFIG);
  assert.equal(DEFAULT_CONFIG.capacity.throttleWarnPct, 80);
  assert.equal(DEFAULT_CONFIG.capacity.throttleCritPct, 90);
  assert.equal(DEFAULT_CONFIG.cost.idleCuPct, 5);
});

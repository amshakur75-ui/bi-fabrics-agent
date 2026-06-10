import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRunLog } from './run-log.js';

test('buildRunLog: collectedDomains lists exactly the domains present in facts', () => {
  const facts = { capacity: {}, models: {}, access: {} };
  const envelope = { data: { findings: [], suppressed: [] } };
  const result = buildRunLog(facts, envelope, '2026-06-09T00:00:00Z');
  assert.deepEqual(result.collectedDomains.sort(), ['access', 'capacity', 'models'].sort());
});

test('buildRunLog: findingCount comes from envelope.data.findings.length', () => {
  const facts = { capacity: {} };
  const envelope = { data: { findings: [{ key: 'a' }, { key: 'b' }], suppressed: [] } };
  const result = buildRunLog(facts, envelope, '');
  assert.equal(result.findingCount, 2);
});

test('buildRunLog: suppressedCount comes from envelope.data.suppressed.length', () => {
  const facts = {};
  const envelope = { data: { findings: [], suppressed: [{ key: 'x' }, { key: 'y' }, { key: 'z' }] } };
  const result = buildRunLog(facts, envelope, '');
  assert.equal(result.suppressedCount, 3);
});

test('buildRunLog: readOnly is always true', () => {
  const result = buildRunLog({}, { data: {} }, '');
  assert.equal(result.readOnly, true);
});

test('buildRunLog: at is passed through', () => {
  const at = '2026-06-09T12:34:56.000Z';
  const result = buildRunLog({}, { data: {} }, at);
  assert.equal(result.at, at);
});

test('buildRunLog: domains not present in facts are excluded', () => {
  const facts = { reports: {}, lineage: {} };
  const result = buildRunLog(facts, { data: {} }, '');
  assert.ok(result.collectedDomains.includes('reports'), 'reports should be included');
  assert.ok(result.collectedDomains.includes('lineage'), 'lineage should be included');
  assert.ok(!result.collectedDomains.includes('capacity'), 'capacity should NOT be included');
  assert.ok(!result.collectedDomains.includes('models'), 'models should NOT be included');
});

test('buildRunLog: empty facts → collectedDomains is []', () => {
  const result = buildRunLog({}, { data: {} }, '');
  assert.deepEqual(result.collectedDomains, []);
});

test('buildRunLog: missing envelope.data.findings defaults to 0', () => {
  const result = buildRunLog({}, {}, '');
  assert.equal(result.findingCount, 0);
  assert.equal(result.suppressedCount, 0);
});

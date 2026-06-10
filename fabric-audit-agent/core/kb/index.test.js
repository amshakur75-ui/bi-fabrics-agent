import { test } from 'node:test';
import assert from 'node:assert/strict';
import { getRemediation } from './index.js';

test('getRemediation returns a real playbook for model.bidirectional', () => {
  const p = getRemediation('model.bidirectional');
  assert.match(p.rootCause, /bidirectional/i);
  assert.ok(p.fixes.length >= 2);
  assert.equal(typeof p.owner, 'string');
});

test('getRemediation returns a real playbook for capacity.throttle', () => {
  const p = getRemediation('capacity.throttle');
  assert.match(p.rootCause, /throttl|CU/i);
  assert.ok(p.fixes.length >= 1);
});

test('getRemediation returns a real playbook for report.directquery', () => {
  const p = getRemediation('report.directquery');
  assert.match(p.rootCause, /query/i);
  assert.ok(p.fixes.length >= 1);
});

test('getRemediation returns a real playbook for pipeline.gateway', () => {
  const p = getRemediation('pipeline.gateway');
  assert.match(p.rootCause, /gateway/i);
  assert.ok(p.fixes.length >= 1);
});

test('getRemediation returns the default for an unknown type', () => {
  const p = getRemediation('totally.unknown');
  assert.match(p.rootCause, /knowledge base/i);
  assert.ok(Array.isArray(p.fixes));
  assert.ok(p.fixes.length >= 1);
});

// Inc-7: lineage playbook
test('getRemediation returns a real playbook for lineage.blast-radius', () => {
  const p = getRemediation('lineage.blast-radius');
  assert.match(p.rootCause, /upstream|cascade/i);
  assert.ok(p.fixes.length >= 2);
  assert.equal(typeof p.owner, 'string');
});

// Inc-8: security and cost playbooks
test('getRemediation returns a real playbook for security.admin-grant', () => {
  const p = getRemediation('security.admin-grant');
  assert.match(p.rootCause, /admin/i);
  assert.ok(p.fixes.length >= 2);
  assert.equal(typeof p.owner, 'string');
});

test('getRemediation returns a real playbook for cost.idle-capacity', () => {
  const p = getRemediation('cost.idle-capacity');
  assert.match(p.rootCause, /idle|capacity/i);
  assert.ok(p.fixes.length >= 2);
  assert.equal(typeof p.owner, 'string');
});

// Inc-13: meta playbook
test('getRemediation returns a real playbook for meta.detector-error', () => {
  const p = getRemediation('meta.detector-error');
  assert.match(p.rootCause, /detector|skipped/i);
  assert.ok(p.fixes.length >= 2);
  assert.equal(typeof p.owner, 'string');
});

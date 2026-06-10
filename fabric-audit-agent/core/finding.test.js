import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createFinding, wrapEnvelope } from './finding.js';

const sample = {
  what: 'Capacity F64 reached 96% CU.',
  where: 'Contoso / capacity F64',
  when: '2026-06-08T06:05:00.000Z',
  why: 'CU demand exceeds the SKU.',
  impact: 'Reports slow during the peak window.',
  fix: ['Stagger refreshes.'],
  score: { level: 'Critical', reason: 'CU peaked 96%' },
};

test('createFinding returns all 7 fields', () => {
  const f = createFinding(sample);
  assert.deepEqual(Object.keys(f).sort(),
    ['fix', 'impact', 'score', 'what', 'when', 'where', 'why']);
});

test('createFinding throws on a missing field', () => {
  const { why, ...missing } = sample;
  assert.throws(() => createFinding(missing), /missing required field "why"/);
});

test('createFinding requires fix to be an array', () => {
  assert.throws(() => createFinding({ ...sample, fix: 'nope' }), /"fix" must be an array/);
});

test('wrapEnvelope matches the OS standard envelope', () => {
  const env = wrapEnvelope({ agentId: 'fabric-audit-agent', findings: [createFinding(sample)], summary: 'ok' });
  assert.equal(env.success, true);
  assert.equal(env.agent_id, 'fabric-audit-agent');
  assert.equal(env.data.findings.length, 1);
  assert.equal(env.summary, 'ok');
  assert.match(env.timestamp, /^\d{4}-\d{2}-\d{2}T.*Z$/);
});

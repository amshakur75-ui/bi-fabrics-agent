import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execNarrative } from './narrative.js';

test('execNarrative: optimize verdict contains "6/100", "9 critical", and optimize phrasing', () => {
  const s = execNarrative({ health: 6, critical: 9, warning: 7, verdict: 'optimize' });
  assert.ok(s.includes('6/100'), `expected "6/100" in: ${s}`);
  assert.ok(s.includes('9 critical'), `expected "9 critical" in: ${s}`);
  assert.ok(s.includes('7 warning'), `expected "7 warning" in: ${s}`);
  assert.ok(s.includes('optimization opportunities'), `expected optimize phrasing in: ${s}`);
});

test('execNarrative: size-up verdict contains "capacity increase"', () => {
  const s = execNarrative({ health: 10, critical: 3, warning: 1, verdict: 'size-up' });
  assert.ok(s.includes('capacity increase'), `expected "capacity increase" in: ${s}`);
});

test('execNarrative: healthy verdict contains "capacity is healthy"', () => {
  const s = execNarrative({ health: 95, critical: 0, warning: 1, verdict: 'healthy' });
  assert.ok(s.includes('capacity is healthy'), `expected "capacity is healthy" in: ${s}`);
});

test('execNarrative: unknown verdict contains "capacity status is unknown"', () => {
  const s = execNarrative({ health: 50, critical: 0, warning: 0, verdict: 'unknown' });
  assert.ok(s.includes('capacity status is unknown'), `expected "capacity status is unknown" in: ${s}`);
});

test('execNarrative: accountability > 0 contains "flagged repeatedly"', () => {
  const s = execNarrative({ health: 6, critical: 2, warning: 1, verdict: 'optimize', accountability: 3 });
  assert.ok(s.includes('flagged repeatedly'), `expected "flagged repeatedly" in: ${s}`);
  assert.ok(s.includes('3'), `expected "3" in: ${s}`);
});

test('execNarrative: accountability 0 does not add flagged-repeatedly sentence', () => {
  const s = execNarrative({ health: 6, critical: 1, warning: 0, verdict: 'optimize', accountability: 0 });
  assert.ok(!s.includes('flagged repeatedly'), `should NOT contain "flagged repeatedly": ${s}`);
});

test('execNarrative: topFindings first entry added as Top priority', () => {
  const s = execNarrative({
    health: 6, critical: 1, warning: 0, verdict: 'optimize',
    topFindings: [{ what: 'Throttle on F64', level: 'Critical' }],
  });
  assert.ok(s.includes('Top priority: Throttle on F64'), `expected top-priority sentence in: ${s}`);
});

test('execNarrative: empty exec view → no throw, returns a string', () => {
  const s = execNarrative({});
  assert.equal(typeof s, 'string', 'must return a string');
  assert.ok(s.length > 0, 'must return a non-empty string');
});

test('execNarrative: called with no args → no throw, returns a string', () => {
  const s = execNarrative();
  assert.equal(typeof s, 'string');
  assert.ok(s.includes('—/100'), `expected "—/100" placeholder in: ${s}`);
});

test('execNarrative: unknown verdict key → "status is unclear"', () => {
  const s = execNarrative({ health: 50, critical: 0, warning: 0, verdict: 'other-value' });
  assert.ok(s.includes('status is unclear'), `expected "status is unclear" in: ${s}`);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectConcentration } from './concentration.js';
import { scoreSeverity } from '../severity.js';

test('flags an item at or over 30% of capacity CU (default threshold)', () => {
  const facts = {
    items: [
      { workspace: 'Finance', name: 'GL Model', kind: 'SemanticModel', cuSeconds: 700000, sharePct: 70, users: 12 },
      { workspace: 'Sales', name: 'Exec', kind: 'Report', cuSeconds: 100000, sharePct: 10, users: 80 },
    ],
  };
  const flags = detectConcentration(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'capacity.concentration');
  assert.equal(flags[0].evidence.sharePct, 70);
  assert.match(flags[0].what, /GL Model/);
  assert.match(flags[0].what, /70%/);
});

test('leads with named users (top-3 + count) when correlation is present', () => {
  const facts = {
    items: [
      {
        workspace: 'Finance', name: 'GL Model', sharePct: 40, userCount: 5,
        topUsers: [{ user: 'jdoe@contoso.com' }, { user: 'asmith@contoso.com' }],
      },
    ],
  };
  const [flag] = detectConcentration(facts);
  assert.match(flag.what, /jdoe@contoso\.com/);          // user-first
  assert.match(flag.what, /asmith@contoso\.com/);
  assert.match(flag.what, /\+ 3 more/);                  // userCount 5 - 2 named
});

test('background-dominated: names the owner, not an interactive consumer', () => {
  const facts = {
    items: [
      {
        workspace: 'Finance', name: 'GL Model', sharePct: 60, background: true, owner: 'owner@contoso.com',
        topUsers: [{ user: 'svc@contoso.com' }], userCount: 1,
      },
    ],
  };
  const [flag] = detectConcentration(facts);
  assert.match(flag.what, /background/);
  assert.match(flag.what, /owner@contoso\.com/);
  assert.equal(flag.evidence.background, true);
});

test('without named users, says specific users are pending correlation', () => {
  const [flag] = detectConcentration({ items: [{ workspace: 'Ops', name: 'Inv', sharePct: 33, users: 5 }] });
  assert.match(flag.what, /pending/);
  assert.match(flag.what, /5 user/);
});

test('no items, or all below threshold -> no flags', () => {
  assert.deepEqual(detectConcentration({}), []);
  assert.deepEqual(detectConcentration({ items: [{ name: 'x', sharePct: 12 }] }), []);
});

test('respects a custom threshold', () => {
  const cfg = { capacity: { concentrationPct: 50, concentrationCritPct: 80 } };
  const facts = { items: [{ name: 'x', workspace: 'w', sharePct: 40, users: 1 }] };
  assert.equal(detectConcentration(facts, cfg).length, 0);
});

test('severity: >=50% is Critical, below is Warning', () => {
  assert.equal(scoreSeverity({ type: 'capacity.concentration', evidence: { sharePct: 65 } }).level, 'Critical');
  assert.equal(scoreSeverity({ type: 'capacity.concentration', evidence: { sharePct: 35 } }).level, 'Warning');
});

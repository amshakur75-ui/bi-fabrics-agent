import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attributeUsers, enrichItems } from './attribution.js';

test('cost mode: ranks by CPU/duration when present; top 3 + count', () => {
  const events = [
    { user: 'a@x.com', cpuMs: 5000, interactive: true },
    { user: 'b@x.com', cpuMs: 9000, interactive: true },
    { user: 'c@x.com', durationMs: 1000, interactive: true },
    { user: 'd@x.com', cpuMs: 100, interactive: true },
    { user: 'b@x.com', cpuMs: 3000, interactive: true },
  ];
  const r = attributeUsers(events, { topN: 3 });
  assert.equal(r.mode, 'cost');
  assert.equal(r.userCount, 4);
  assert.equal(r.topUsers[0].user, 'b@x.com');   // 12000 = highest summed cost
  assert.equal(r.topUsers.length, 3);
  assert.equal(r.background, false);
});

test('frequency mode when no cost fields: ranks by op count', () => {
  const r = attributeUsers([
    { user: 'a@x.com', interactive: true },
    { user: 'a@x.com', interactive: true },
    { user: 'b@x.com', interactive: true },
  ]);
  assert.equal(r.mode, 'frequency');
  assert.equal(r.topUsers[0].user, 'a@x.com');
  assert.equal(r.topUsers[0].ops, 2);
});

test('flags background-dominated consumption and carries the owner', () => {
  const r = attributeUsers([
    { user: 'svc@x.com', interactive: false, cpuMs: 100000 },
    { user: 'svc@x.com', interactive: false, cpuMs: 50000 },
    { user: 'viewer@x.com', interactive: true, cpuMs: 10 },
  ], { owner: 'owner@x.com' });
  assert.equal(r.background, true);          // 1 of 3 interactive < 50%
  assert.equal(r.owner, 'owner@x.com');
});

test('enrichItems attaches attribution to matching items by name, leaves others untouched', () => {
  const items = [{ name: 'GL Model', workspace: 'Fin', sharePct: 70 }, { name: 'Other', sharePct: 5 }];
  const eventsByItem = { 'GL Model': [{ user: 'a@x.com', cpuMs: 10, interactive: true }, { user: 'b@x.com', cpuMs: 5, interactive: true }] };
  const out = enrichItems(items, eventsByItem);
  const gl = out.find(i => i.name === 'GL Model');
  assert.equal(gl.userCount, 2);
  assert.equal(gl.topUsers[0].user, 'a@x.com');
  assert.equal(gl.attributionMode, 'cost');
  assert.equal(out.find(i => i.name === 'Other').topUsers, undefined);
});

test('cost-weighted: one heavy background refresh outweighs many cheap interactive ops', () => {
  const r = attributeUsers([
    { user: 'svc@x.com', interactive: false, cpuMs: 500000 },
    { user: 'v1@x.com', interactive: true, cpuMs: 50 },
    { user: 'v2@x.com', interactive: true, cpuMs: 50 },
    { user: 'v3@x.com', interactive: true, cpuMs: 50 },
  ]);
  assert.equal(r.background, true);   // 3 of 4 ops are interactive, but background dominates COST
});

test('ignores events with no user', () => {
  const r = attributeUsers([{ cpuMs: 100, interactive: true }, { user: '', interactive: true }]);
  assert.equal(r.userCount, 0);
  assert.equal(r.topUsers.length, 0);
});

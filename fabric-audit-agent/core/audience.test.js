import { test } from 'node:test';
import assert from 'node:assert/strict';
import { viewFor } from './audience.js';

// ---------------------------------------------------------------------------
// Shared fixture
// ---------------------------------------------------------------------------

const critFinding = { key: 'capacity.throttle::T/F64', what: 'Throttle detected', where: 'T/F64', when: '', score: { level: 'Critical', reason: 'x' }, fix: [], userTip: 'Reduce peak refreshes' };
const warnFinding = { key: 'model.bidirectional::DatasetX', what: 'Bidirectional relationship', where: 'DatasetX', when: '', score: { level: 'Warning', reason: 'x' }, fix: [], userTip: 'Switch to single-direction' };
const infoFinding = { key: 'cost.idle-capacity::Cap1', what: 'Idle capacity', where: 'Cap1', when: '', score: { level: 'Info', reason: 'x' }, fix: [] };

const roadmap = [
  { rank: 1, key: 'capacity.throttle::T/F64', level: 'Critical', what: 'Throttle detected', fix: null, recurringRuns: 1 },
  { rank: 2, key: 'model.bidirectional::DatasetX', level: 'Warning', what: 'Bidirectional relationship', fix: null, recurringRuns: 1 },
  { rank: 3, key: 'cost.idle-capacity::Cap1', level: 'Info', what: 'Idle capacity', fix: null, recurringRuns: 1 },
  { rank: 4, key: 'lineage.broken::DS/Rep', level: 'Info', what: 'Broken lineage', fix: null, recurringRuns: 1 },
];

const envelope = {
  data: {
    findings: [critFinding, warnFinding, infoFinding],
    healthScore: { overall: 6, byDomain: { capacity: 0 } },
    verdict: { decision: 'optimize', reason: 'oversized models' },
    roadmap,
    accountability: { ignoredCount: 2, items: [] },
    sla: { breachedCount: 1, items: [] },
    correlations: [{ theme: 'capacity-pressure', findingKeys: [], narrative: 'n' }],
    routing: { 'security-team': [], 'powerbi-team': [] },
  },
};

// ---------------------------------------------------------------------------
// exec view
// ---------------------------------------------------------------------------

test('viewFor exec: health, verdict, critical/warning counts', () => {
  const v = viewFor(envelope, 'exec');
  assert.equal(v.audience, 'exec');
  assert.equal(v.health, 6);
  assert.equal(v.verdict, 'optimize');
  assert.equal(v.critical, 1);
  assert.equal(v.warning, 1);
});

test('viewFor exec: topFindings length <= 3', () => {
  const v = viewFor(envelope, 'exec');
  assert.ok(Array.isArray(v.topFindings), 'topFindings must be an array');
  assert.ok(v.topFindings.length <= 3, `topFindings.length must be <= 3, got ${v.topFindings.length}`);
  assert.equal(v.topFindings[0].what, 'Throttle detected');
  assert.equal(v.topFindings[0].level, 'Critical');
});

test('viewFor exec: accountability count from envelope.data.accountability.ignoredCount', () => {
  const v = viewFor(envelope, 'exec');
  assert.equal(v.accountability, 2);
});

test('viewFor exec: empty envelope → no throw, health is null', () => {
  const v = viewFor({}, 'exec');
  assert.equal(v.audience, 'exec');
  assert.equal(v.health, null);
  assert.equal(v.verdict, null);
  assert.equal(v.critical, 0);
  assert.equal(v.warning, 0);
  assert.deepEqual(v.topFindings, []);
  assert.equal(v.accountability, 0);
});

// ---------------------------------------------------------------------------
// author view
// ---------------------------------------------------------------------------

test('viewFor author: only findings with a userTip, mapped to {what, tip}', () => {
  const v = viewFor(envelope, 'author');
  assert.equal(v.audience, 'author');
  assert.ok(Array.isArray(v.items), 'items must be an array');
  // infoFinding has no userTip → excluded
  assert.equal(v.items.length, 2);
  assert.equal(v.items[0].what, critFinding.what);
  assert.equal(v.items[0].tip, critFinding.userTip);
  assert.equal(v.items[1].what, warnFinding.what);
  assert.equal(v.items[1].tip, warnFinding.userTip);
});

test('viewFor author: no findings with userTip → items is empty array', () => {
  const env2 = { data: { findings: [infoFinding] } };
  const v = viewFor(env2, 'author');
  assert.equal(v.audience, 'author');
  assert.deepEqual(v.items, []);
});

test('viewFor author: empty envelope → items is empty array', () => {
  const v = viewFor({}, 'author');
  assert.deepEqual(v.items, []);
});

// ---------------------------------------------------------------------------
// team view (default)
// ---------------------------------------------------------------------------

test('viewFor team: includes findings, roadmap, routing, sla, correlations', () => {
  const v = viewFor(envelope, 'team');
  assert.equal(v.audience, 'team');
  assert.equal(v.findings, envelope.data.findings);
  assert.equal(v.roadmap, envelope.data.roadmap);
  assert.equal(v.routing, envelope.data.routing);
  assert.equal(v.sla, envelope.data.sla);
  assert.equal(v.correlations, envelope.data.correlations);
});

test('viewFor default (no audience arg): returns team view', () => {
  const v = viewFor(envelope);
  assert.equal(v.audience, 'team');
  assert.ok(Array.isArray(v.findings));
});

test('viewFor team: missing data fields default to empty/null', () => {
  const v = viewFor({ data: {} }, 'team');
  assert.deepEqual(v.findings, []);
  assert.deepEqual(v.roadmap, []);
  assert.deepEqual(v.routing, {});
  assert.equal(v.sla, null);
  assert.deepEqual(v.correlations, []);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runAudit } from './pipeline.js';
import { mergeConfig } from './config.js';

const fakeFacts = {
  capacity: { tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [] },
};

test('runAudit wires collector -> detect -> reason -> deliver', async () => {
  let delivered = null;
  const ports = {
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: { reason: async (_facts, flags) => flags.map(f => ({ ...f, score: { level: 'Critical', reason: 'x' } })) },
    delivery: { deliver: async (env) => { delivered = env; return 'ok'; } },
  };
  const env = await runAudit(ports);
  assert.equal(env.success, true);
  assert.equal(env.agent_id, 'fabric-audit-agent');
  assert.ok(env.data.findings.length >= 1);
  assert.match(env.summary, /1 critical/);
  assert.equal(delivered.agent_id, 'fabric-audit-agent'); // delivery actually received the envelope
});

// ---------------------------------------------------------------------------
// Inc 3: store-wired path
// ---------------------------------------------------------------------------

// Fake reasoner that returns a Warning with a key, so escalation can trigger
const warningKey = 'model.bidirectional::DatasetX';
const fakeWarningReasoner = {
  reason: async (_facts, _flags) => [{
    key: warningKey,
    what: 'Bidirectional relationship detected',
    where: 'DatasetX',
    when: '2026-01-01T00:00:00Z',
    score: { level: 'Warning', reason: 'bidir' },
    fix: [],
  }],
};

// Fake store seeded so the warning key appears in the 2 most recent runs
function makeFakeStore(priorKeys = []) {
  const priorRuns = priorKeys.length
    ? [
        { runAt: '2026-01-01T00:00:00Z', findings: priorKeys.map(k => ({ key: k, level: 'Warning' })) },
        { runAt: '2026-01-02T00:00:00Z', findings: priorKeys.map(k => ({ key: k, level: 'Warning' })) },
      ]
    : [];
  const appended = [];
  return {
    store: {
      async history() { return [...priorRuns]; },
      async append(run) { appended.push(run); return appended.length; },
    },
    appended,
  };
}

test('runAudit with store escalates a Warning present in last 2 history runs', async () => {
  const { store } = makeFakeStore([warningKey]);
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  const f = env.data.findings[0];
  assert.equal(f.score.level, 'Critical');
  assert.match(f.score.reason, /escalated/);
});

test('runAudit with store attaches envelope.data.digest', async () => {
  const { store } = makeFakeStore([]);
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(env.data.digest, 'digest should be attached');
  assert.ok('totals' in env.data.digest);
  assert.ok('newCount' in env.data.digest);
  assert.ok('byDomain' in env.data.digest);
  assert.ok('recurring' in env.data.digest);
});

test('runAudit with store uses the provided now timestamp', async () => {
  const { store, appended } = makeFakeStore([]);
  await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T12:00:00Z',
  });
  assert.equal(appended[0].runAt, '2026-01-03T12:00:00Z');
});

test('runAudit without store has no digest on envelope', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.digest, undefined);
});

// ---------------------------------------------------------------------------
// Inc 5: verdict + coaching
// ---------------------------------------------------------------------------

test('runAudit attaches envelope.data.verdict with a decision', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(env.data.verdict, 'verdict should be attached');
  assert.ok(
    ['optimize', 'size-up', 'healthy', 'unknown'].includes(env.data.verdict.decision),
    `unexpected decision: ${env.data.verdict.decision}`,
  );
});

test('runAudit attaches userTip to findings that have an author-actionable flag type', async () => {
  // fakeWarningReasoner returns key 'model.bidirectional::DatasetX' → tip applies
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  const withTip = env.data.findings.filter(f => f.userTip);
  assert.ok(withTip.length >= 1, 'expected at least one finding to carry a userTip');
  assert.ok(typeof withTip[0].userTip === 'string' && withTip[0].userTip.length > 0);
});

// ---------------------------------------------------------------------------
// Inc 11: lifecycle integration
// ---------------------------------------------------------------------------

test('runAudit with lifecycleStore: resolved finding is absent from findings and present in suppressed', async () => {
  const resolvedKey = warningKey; // 'model.bidirectional::DatasetX'
  const fakeLifecycleStore = {
    async load() {
      return { [resolvedKey]: { state: 'resolved', since: '2026-01-01T00:00:00Z', snoozeUntil: null, note: null } };
    },
  };
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    lifecycleStore: fakeLifecycleStore,
    now: '2026-01-03T00:00:00Z',
  });
  // The resolved key must NOT appear in active findings
  assert.ok(!env.data.findings.some(f => f.key === resolvedKey),
    'resolved finding should not be in active findings');
  // It must appear in suppressed
  assert.ok(Array.isArray(env.data.suppressed), 'suppressed should be an array');
  assert.ok(env.data.suppressed.some(f => f.key === resolvedKey),
    'resolved finding should be in suppressed');
  assert.equal(env.data.suppressed.find(f => f.key === resolvedKey).state, 'resolved');
});

test('runAudit without lifecycleStore: existing behavior unchanged (no suppressed key)', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    now: '2026-01-03T00:00:00Z',
  });
  assert.equal(env.data.suppressed, undefined, 'no lifecycleStore → no suppressed key');
  assert.ok(env.data.findings.some(f => f.key === warningKey), 'finding should still be active');
});

// ---------------------------------------------------------------------------
// Inc 12: config + tenant
// ---------------------------------------------------------------------------

test('runAudit sets envelope.data.tenant from facts.capacity.tenant when not passed explicitly', async () => {
  const factsWithTenant = {
    capacity: { tenant: 'Contoso', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [] },
  };
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => factsWithTenant },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.tenant, 'Contoso');
});

test('runAudit sets envelope.data.tenant to explicit tenant when provided', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    tenant: 'AcmeCorp',
  });
  assert.equal(env.data.tenant, 'AcmeCorp');
});

test('runAudit sets envelope.data.tenant to "default" when neither passed nor in facts', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => ({}) },
    reasoner: { reason: async () => [] },
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.tenant, 'default');
});

test('runAudit includes tenant in store.append record', async () => {
  const { store, appended } = makeFakeStore([]);
  await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    tenant: 'TenantX',
    now: '2026-01-03T00:00:00Z',
  });
  assert.equal(appended[0].tenant, 'TenantX');
});

// ---------------------------------------------------------------------------
// Inc 13: dataQuality
// ---------------------------------------------------------------------------

test('runAudit attaches envelope.data.dataQuality when facts have missing required capacity fields', async () => {
  const malformedFacts = { capacity: { peakCuPct: 96 } }; // missing capacityId, sku, memoryGB
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => malformedFacts },
    reasoner: { reason: async (_facts, flags) => flags.map(f => ({ ...f, score: { level: 'Warning', reason: 'x' } })) },
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(Array.isArray(env.data.dataQuality), 'dataQuality should be an array');
  assert.ok(env.data.dataQuality.length > 0, 'dataQuality should be non-empty');
  assert.ok(env.data.dataQuality.some(i => i.domain === 'capacity'), 'dataQuality should mention capacity domain');
});

test('runAudit does NOT attach dataQuality for well-formed facts', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.dataQuality, undefined, 'well-formed facts should produce no dataQuality key');
});

// ---------------------------------------------------------------------------
// Fix 2: suppressed findings included in store.append
// ---------------------------------------------------------------------------

test('runAudit with lifecycleStore (snoozed) + store: suppressed finding appears in store record with suppressed:true', async () => {
  const snoozedKey = warningKey; // 'model.bidirectional::DatasetX'
  const futureDate = '2099-01-01T00:00:00Z';
  const fakeLifecycleStore = {
    async load() {
      return { [snoozedKey]: { state: 'snoozed', since: '2026-01-01T00:00:00Z', snoozeUntil: futureDate, note: null } };
    },
  };
  const { store, appended } = makeFakeStore([]);
  await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    lifecycleStore: fakeLifecycleStore,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(appended.length === 1, 'store.append should have been called once');
  const record = appended[0];
  assert.ok(Array.isArray(record.findings), 'record.findings should be an array');
  const snoozedEntry = record.findings.find(f => f.key === snoozedKey);
  assert.ok(snoozedEntry, `snoozed key "${snoozedKey}" should be in record.findings`);
  assert.equal(snoozedEntry.suppressed, true, 'snoozed finding should have suppressed:true');
});

test('runAudit with store (no lifecycle): active findings have suppressed:false in store record', async () => {
  const { store, appended } = makeFakeStore([]);
  await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(appended.length === 1);
  const activeEntry = appended[0].findings.find(f => f.key === warningKey);
  assert.ok(activeEntry, 'active finding should be in record');
  assert.equal(activeEntry.suppressed, false, 'active finding should have suppressed:false');
});

test('runAudit with config override: throttleWarnPct=99 removes throttle finding for 96% CU', async () => {
  const factsWithCapacity = {
    capacity: { tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64, peakCuPct: 96, peakAt: 'x', throttleMinutes: 42, refreshes: [] },
  };
  // Use the stub reasoner wired with the same config override
  const config = mergeConfig({ capacity: { throttleWarnPct: 99 } });
  const { createStubReasoner } = await import('../adapters/reasoner.stub.js');
  const reasoner = createStubReasoner({ config });
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => factsWithCapacity },
    reasoner,
    delivery: { deliver: async () => 'ok' },
    config,
  });
  const throttleFindings = env.data.findings.filter(f => f.key && f.key.startsWith('capacity.throttle'));
  assert.equal(throttleFindings.length, 0, 'no throttle findings when throttleWarnPct=99 and peak=96%');
});

// ---------------------------------------------------------------------------
// Inc 14: health score + roadmap
// ---------------------------------------------------------------------------

test('runAudit attaches envelope.data.healthScore with overall 0-100 and byDomain object', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: { reason: async (_facts, flags) => flags.map(f => ({ ...f, score: { level: 'Critical', reason: 'x' } })) },
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(env.data.healthScore, 'healthScore should be attached to envelope');
  const { overall, byDomain } = env.data.healthScore;
  assert.equal(typeof overall, 'number', 'overall must be a number');
  assert.ok(overall >= 0 && overall <= 100, `overall must be 0-100, got ${overall}`);
  assert.equal(typeof byDomain, 'object', 'byDomain must be an object');
  assert.ok(byDomain !== null && !Array.isArray(byDomain), 'byDomain must be a plain object');
});

test('runAudit attaches envelope.data.roadmap as a non-empty array with first entry level Critical (given criticals in fixture)', async () => {
  // The fakeFacts + Critical reasoner produces Critical findings, so roadmap should have Criticals first.
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: { reason: async (_facts, flags) => flags.map(f => ({ ...f, score: { level: 'Critical', reason: 'x' } })) },
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(Array.isArray(env.data.roadmap), 'roadmap must be an array');
  assert.ok(env.data.roadmap.length >= 1, 'roadmap should be non-empty when there are findings');
  assert.equal(env.data.roadmap[0].level, 'Critical', 'first roadmap entry must be Critical when criticals exist');
});

test('runAudit roadmap length equals findings length', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.roadmap.length, env.data.findings.length,
    'roadmap length must equal active findings count');
});

test('runAudit roadmap entries have rank starting at 1', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  if (env.data.roadmap.length > 0) {
    assert.equal(env.data.roadmap[0].rank, 1, 'first roadmap entry must have rank 1');
  }
});

// ---------------------------------------------------------------------------
// Inc 15: accountability
// ---------------------------------------------------------------------------

// Fake store seeded with the warning key in the last 2 runs so recurringRuns = 3
function makeAccountabilityStore() {
  const priorRuns = [
    { runAt: '2026-01-01T00:00:00Z', findings: [{ key: warningKey, level: 'Warning' }] },
    { runAt: '2026-01-02T00:00:00Z', findings: [{ key: warningKey, level: 'Warning' }] },
  ];
  return {
    async history() { return [...priorRuns]; },
    async append() { return 1; },
  };
}

test('runAudit with store + 2 prior runs + open lifecycle → accountability.ignoredCount >= 1', async () => {
  const store = makeAccountabilityStore();
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(env.data.accountability, 'accountability should be attached when recurring >= threshold');
  assert.ok(env.data.accountability.ignoredCount >= 1,
    `expected ignoredCount >= 1, got ${env.data.accountability?.ignoredCount}`);
  assert.ok(Array.isArray(env.data.accountability.items), 'accountability.items should be an array');
});

test('runAudit with store + empty history → no accountability key on envelope', async () => {
  const { store } = makeFakeStore([]); // empty history
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.equal(env.data.accountability, undefined, 'no accountability key when history is empty (recurringRuns = 1)');
});

// ---------------------------------------------------------------------------
// Inc 16: capacity forecasting
// ---------------------------------------------------------------------------

// Fake store whose history returns 2 prior runs with rising metrics.peakCuPct (86, 90)
// so that with the current fixture's peakCuPct=96 the series is [86,90,96] → rising
function makeRisingMetricsStore() {
  const priorRuns = [
    { runAt: '2026-01-01T00:00:00Z', metrics: { peakCuPct: 86 }, findings: [] },
    { runAt: '2026-01-02T00:00:00Z', metrics: { peakCuPct: 90 }, findings: [] },
  ];
  const appended = [];
  return {
    store: {
      async history() { return [...priorRuns]; },
      async append(run) { appended.push(run); return appended.length; },
    },
    appended,
  };
}

test('runAudit with store + rising metrics history → envelope.data.forecast.runsToCeiling is a positive number', async () => {
  const { store } = makeRisingMetricsStore();
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts }, // fakeFacts.capacity.peakCuPct = 96
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(env.data.forecast, 'forecast should be attached when breach is projected');
  assert.ok(typeof env.data.forecast.runsToCeiling === 'number' && env.data.forecast.runsToCeiling > 0,
    `expected positive runsToCeiling, got ${env.data.forecast.runsToCeiling}`);
  assert.equal(env.data.forecast.trend, 'rising');
});

test('runAudit with store + empty history → no forecast key (only 1 data point)', async () => {
  const { store } = makeFakeStore([]); // empty history → series will have only 1 point (current)
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.equal(env.data.forecast, undefined, 'no forecast key when history is empty (< 3 data points)');
});

test('runAudit store.append record includes metrics.peakCuPct', async () => {
  const { store, appended } = makeFakeStore([]);
  await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts }, // peakCuPct: 96
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(appended.length === 1, 'store.append should have been called once');
  assert.ok(appended[0].metrics, 'appended record should have metrics');
  assert.equal(appended[0].metrics.peakCuPct, 96, 'metrics.peakCuPct should match fixture value');
});

// ---------------------------------------------------------------------------
// Inc 17: outcome tracking
// ---------------------------------------------------------------------------

// Store whose last run has an active key NOT in the current findings + metrics.peakCuPct: 96
// (current fixture also has peakCuPct: 96, so metricDelta.change = 0, improved = false, but metricDelta IS present)
function makeOutcomesStore(extraKey = 'model.orphaned::OldDataset') {
  const priorRuns = [
    {
      runAt: '2026-01-01T00:00:00Z',
      metrics: { peakCuPct: 96 },
      findings: [
        { key: extraKey, level: 'Warning', suppressed: false },
        { key: warningKey, level: 'Warning', suppressed: false },
      ],
    },
  ];
  const appended = [];
  return {
    store: {
      async history() { return [...priorRuns]; },
      async append(run) { appended.push(run); return appended.length; },
    },
    appended,
    extraKey,
  };
}

test('runAudit with store + prev run has key absent in current → envelope.data.outcomes.resolvedSinceLast includes that key', async () => {
  const { store, extraKey } = makeOutcomesStore();
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    // fakeWarningReasoner only returns warningKey, so extraKey is absent from current findings
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.ok(env.data.outcomes, 'outcomes should be attached when there are resolved findings');
  assert.ok(Array.isArray(env.data.outcomes.resolvedSinceLast), 'resolvedSinceLast must be an array');
  assert.ok(
    env.data.outcomes.resolvedSinceLast.includes(extraKey),
    `expected resolvedSinceLast to include "${extraKey}", got ${JSON.stringify(env.data.outcomes.resolvedSinceLast)}`,
  );
});

test('runAudit with store + prev metrics.peakCuPct=96 and current=96 → metricDelta present (change 0, improved false)', async () => {
  const { store } = makeOutcomesStore();
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts }, // fakeFacts.capacity.peakCuPct = 96
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  // outcomes is present (resolvedSinceLast has the extraKey), metricDelta should also be present
  assert.ok(env.data.outcomes, 'outcomes should be attached');
  assert.ok(env.data.outcomes.metricDelta, 'metricDelta should be present when prev has metrics.peakCuPct');
  assert.equal(env.data.outcomes.metricDelta.change, 0);
  assert.equal(env.data.outcomes.metricDelta.improved, false);
});

test('runAudit with store + empty history → no outcomes key on envelope', async () => {
  const { store } = makeFakeStore([]); // empty history
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-03T00:00:00Z',
  });
  assert.equal(env.data.outcomes, undefined, 'empty history → no outcomes key');
});

test('runAudit without store → no outcomes key on envelope', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.outcomes, undefined, 'no store → no outcomes key');
});

// ---------------------------------------------------------------------------
// Inc 18: anomaly detection
// ---------------------------------------------------------------------------

// Fake store whose history has 4 runs with metrics.peakCuPct around 70
// fakeFacts.capacity.peakCuPct = 96 → well above baseline → anomaly expected
function makeAnomalyStore() {
  const priorRuns = [
    { runAt: '2026-01-01T00:00:00Z', metrics: { peakCuPct: 70 }, findings: [] },
    { runAt: '2026-01-02T00:00:00Z', metrics: { peakCuPct: 72 }, findings: [] },
    { runAt: '2026-01-03T00:00:00Z', metrics: { peakCuPct: 71 }, findings: [] },
    { runAt: '2026-01-04T00:00:00Z', metrics: { peakCuPct: 69 }, findings: [] },
  ];
  const appended = [];
  return {
    store: {
      async history() { return [...priorRuns]; },
      async append(run) { appended.push(run); return appended.length; },
    },
    appended,
  };
}

test('runAudit with store + 4 baseline-70 runs + current 96 → envelope.data.anomalies.length >= 1', async () => {
  const { store } = makeAnomalyStore();
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts }, // peakCuPct: 96
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-05T00:00:00Z',
  });
  assert.ok(Array.isArray(env.data.anomalies), 'anomalies should be an array');
  assert.ok(env.data.anomalies.length >= 1, `expected at least 1 anomaly, got ${env.data.anomalies?.length}`);
  assert.equal(env.data.anomalies[0].direction, 'above');
});

test('runAudit with store + empty history → no anomalies key on envelope', async () => {
  const { store } = makeFakeStore([]); // empty history → fewer than minPoints
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-01-05T00:00:00Z',
  });
  assert.equal(env.data.anomalies, undefined, 'empty history → no anomalies key (insufficient data)');
});

test('runAudit without store → no anomalies key on envelope', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.anomalies, undefined, 'no store → no anomalies key');
});

// ---------------------------------------------------------------------------
// Inc 19: cross-domain correlation
// ---------------------------------------------------------------------------

// Full-spectrum reasoner: returns findings that will trigger all 3 correlations.
// Keys must match the prefixes correlate() uses:
//   capacity.throttle, capacity.contention, capacity.oversized-model
//   model.refresh-failing, pipeline.failing
//   security.admin-grant, security.external-share, security.unusual-access
const fullSpectrumReasoner = {
  reason: async (_facts, _flags) => [
    { key: 'capacity.throttle::T/F64',           what: 'throttle',        where: 'T/F64',           when: '', score: { level: 'Critical', reason: 'x' }, fix: [] },
    { key: 'capacity.contention::T/F64',         what: 'contention',      where: 'T/F64',           when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
    { key: 'capacity.oversized-model::T/WS/DS',  what: 'oversized',       where: 'T/WS/DS',         when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
    { key: 'model.refresh-failing::Finance/Sales', what: 'model fail',    where: 'Finance/Sales',   when: '', score: { level: 'Critical', reason: 'x' }, fix: [] },
    { key: 'pipeline.failing::Finance/Nightly',  what: 'pipe fail',       where: 'Finance/Nightly', when: '', score: { level: 'Critical', reason: 'x' }, fix: [] },
    { key: 'security.admin-grant::Finance',      what: 'admin grant',     where: 'Finance',         when: '', score: { level: 'Critical', reason: 'x' }, fix: [] },
    { key: 'security.external-share::Finance',   what: 'ext share',       where: 'Finance',         when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
    { key: 'security.unusual-access::Finance',   what: 'unusual access',  where: 'Finance',         when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
  ],
};

test('runAudit with full-spectrum findings → data.correlations has all three themes', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fullSpectrumReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(Array.isArray(env.data.correlations), 'correlations should be an array');
  const themes = env.data.correlations.map(c => c.theme);
  assert.ok(themes.includes('capacity-pressure'), 'expected capacity-pressure theme');
  assert.ok(themes.includes('refresh-chain'),     'expected refresh-chain theme');
  assert.ok(themes.includes('security-cluster'),  'expected security-cluster theme');
});

test('runAudit with only warning finding → no data.correlations (empty → not attached)', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner, // returns model.bidirectional key → no clusters
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.correlations, undefined,
    'no correlations key when no clusters qualify');
});

// ---------------------------------------------------------------------------
// Inc 20: refresh-stagger planner
// ---------------------------------------------------------------------------

// Estate-like facts with the 3-way 06:00 collision
const staggerFacts = {
  capacity: {
    tenant: 'C', capacityId: 'F64', sku: 'F64', memoryGB: 64,
    peakCuPct: 96, peakAt: 'x', throttleMinutes: 42,
    refreshes: [
      { workspace: 'Finance', dataset: 'Sales',     scheduledAt: '06:00', sizeGB: 4.2 },
      { workspace: 'Finance', dataset: 'Forecast',  scheduledAt: '06:00', sizeGB: 2.1 },
      { workspace: 'Ops',     dataset: 'Logistics', scheduledAt: '06:00', sizeGB: 1.4 },
      { workspace: 'HR',      dataset: 'Headcount', scheduledAt: '09:00', sizeGB: 0.3 },
    ],
  },
};

test('runAudit with colliding refreshes → data.staggerPlan has 2 entries (Forecast + Logistics)', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => staggerFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(Array.isArray(env.data.staggerPlan), 'staggerPlan should be an array');
  assert.equal(env.data.staggerPlan.length, 2, 'expected 2 stagger entries');
  const forecast = env.data.staggerPlan.find(s => s.dataset === 'Forecast');
  assert.ok(forecast, 'Forecast should be in staggerPlan');
  assert.equal(forecast.from, '06:00');
  assert.equal(forecast.to, '06:15');
  const logistics = env.data.staggerPlan.find(s => s.dataset === 'Logistics');
  assert.ok(logistics, 'Logistics should be in staggerPlan');
  assert.equal(logistics.from, '06:00');
  assert.equal(logistics.to, '06:30');
});

test('runAudit with no colliding refreshes → no data.staggerPlan key', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts }, // fakeFacts.capacity.refreshes = []
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.staggerPlan, undefined,
    'no staggerPlan key when there are no colliding refreshes');
});

// ---------------------------------------------------------------------------
// Inc 23: SLA / time-to-resolve
// ---------------------------------------------------------------------------

// Fake store whose history places the warning key ~10 days before nowMs
const SLA_NOW_MS = Date.parse('2026-06-08T00:00:00Z');
const SLA_FIRST_SEEN = '2026-05-29T00:00:00Z'; // 10 days before nowMs (> 7 day Warning target)

function makeSlaStore() {
  const priorRuns = [
    {
      runAt: SLA_FIRST_SEEN,
      findings: [{ key: warningKey, level: 'Warning' }],
    },
  ];
  return {
    async history() { return [...priorRuns]; },
    async append() { return 1; },
  };
}

test('runAudit with store + finding first-seen 10 days ago → envelope.data.sla.breachedCount > 0', async () => {
  const store = makeSlaStore();
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-06-08T00:00:00Z',
  });
  assert.ok(env.data.sla, 'data.sla should be attached when breaches exist');
  assert.ok(env.data.sla.breachedCount > 0,
    `expected breachedCount > 0, got ${env.data.sla?.breachedCount}`);
  assert.ok(Array.isArray(env.data.sla.items), 'sla.items must be an array');
  assert.equal(env.data.sla.items[0].key, warningKey);
});

test('runAudit with store + empty history → no data.sla key', async () => {
  const { store } = makeFakeStore([]); // empty history → no first-seen
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
    store,
    now: '2026-06-08T00:00:00Z',
  });
  assert.equal(env.data.sla, undefined, 'no sla key when empty history (no first-seen dates)');
});

test('runAudit without store → no data.sla key', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.sla, undefined, 'no sla key when no store');
});

// ---------------------------------------------------------------------------
// Inc 24: routing rules
// ---------------------------------------------------------------------------

// Reasoner that returns a cross-domain set: security, cost, and powerbi-team domains
const routingReasoner = {
  reason: async (_facts, _flags) => [
    { key: 'security.admin-grant::WS1',      what: 's1', where: 'WS1', when: '', score: { level: 'Critical', reason: 'x' }, fix: [] },
    { key: 'security.external-share::WS2',   what: 's2', where: 'WS2', when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
    { key: 'cost.idle-capacity::Cap1',        what: 'c1', where: 'Cap1', when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
    { key: 'model.bidirectional::DatasetX',  what: 'm1', where: 'DatasetX', when: '', score: { level: 'Warning',  reason: 'x' }, fix: [] },
  ],
};

test('runAudit with multi-domain findings → data.routing has security-team, finops, powerbi-team', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: routingReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(env.data.routing, 'data.routing should be attached');
  assert.ok(Array.isArray(env.data.routing['security-team']), 'security-team should be an array');
  assert.equal(env.data.routing['security-team'].length, 2, 'expected 2 security findings');
  assert.ok(Array.isArray(env.data.routing['finops']), 'finops should be an array');
  assert.equal(env.data.routing['finops'].length, 1, 'expected 1 cost finding');
  assert.ok(Array.isArray(env.data.routing['powerbi-team']), 'powerbi-team should be an array');
  assert.equal(env.data.routing['powerbi-team'].length, 1, 'expected 1 powerbi-team finding');
});

test('runAudit with no active findings → no data.routing key', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: { reason: async () => [] },
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.routing, undefined, 'no routing key when there are no findings');
});

test('runAudit routing keys match the finding keys in findings array', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: routingReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  const allRoutedKeys = Object.values(env.data.routing).flat();
  const findingKeys = env.data.findings.map(f => f.key);
  assert.equal(allRoutedKeys.length, findingKeys.length,
    'total routed keys count must equal total findings count');
  for (const k of findingKeys) {
    assert.ok(allRoutedKeys.includes(k), `finding key ${k} should be in routing`);
  }
});

// Estate fixture routing: the full-spectrum estate (17 findings) yields the expected routing
test('runAudit with estate fixture → data.routing has security-team(3), finops(2), powerbi-team(12)', async () => {
  const { createMockCollector } = await import('../adapters/collector.mock.js');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const __dir = dirname(fileURLToPath(import.meta.url));
  const collector = createMockCollector(join(__dir, '..', 'fixtures', 'estate.json'));
  const { createStubReasoner } = await import('../adapters/reasoner.stub.js');
  const { DEFAULT_CONFIG } = await import('./config.js');
  const reasoner = createStubReasoner({ config: DEFAULT_CONFIG });
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector,
    reasoner,
    delivery: { deliver: async () => 'ok' },
    config: DEFAULT_CONFIG,
  });
  assert.ok(env.data.routing, 'data.routing should be attached for estate fixture');
  assert.equal(env.data.routing['security-team']?.length, 3, 'expected security-team(3)');
  assert.equal(env.data.routing['finops']?.length, 2, 'expected finops(2)');
  assert.equal(env.data.routing['powerbi-team']?.length, 12, 'expected powerbi-team(12)');
});

// ---------------------------------------------------------------------------
// Inc 31: confidence + runLog
// ---------------------------------------------------------------------------

test('runAudit: every finding in data.findings has a confidence field', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: { reason: async (_facts, flags) => flags.map(f => ({ ...f, score: { level: 'Critical', reason: 'x' } })) },
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(env.data.findings.length >= 1, 'expected at least one finding');
  for (const f of env.data.findings) {
    assert.ok(['high', 'medium', 'low'].includes(f.confidence),
      `finding ${f.key} must have confidence high/medium/low, got ${f.confidence}`);
  }
});

test('runAudit: data.runLog exists with readOnly:true and a collectedDomains array', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(env.data.runLog, 'data.runLog should be attached to envelope');
  assert.equal(env.data.runLog.readOnly, true, 'runLog.readOnly must be true');
  assert.ok(Array.isArray(env.data.runLog.collectedDomains),
    'runLog.collectedDomains must be an array');
});

test('runAudit: runLog.collectedDomains contains capacity when facts has capacity', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts }, // fakeFacts has capacity
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.ok(env.data.runLog.collectedDomains.includes('capacity'),
    'collectedDomains should include capacity');
});

test('runAudit: runLog.findingCount equals data.findings.length', async () => {
  const env = await runAudit({
    agentId: 'fabric-audit-agent',
    collector: { collect: async () => fakeFacts },
    reasoner: fakeWarningReasoner,
    delivery: { deliver: async () => 'ok' },
  });
  assert.equal(env.data.runLog.findingCount, env.data.findings.length,
    'runLog.findingCount must equal findings.length');
});

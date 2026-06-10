import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { main } from './audit.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const out = join(__dirname, 'runs', 'latest.json');
const historyFile = join(__dirname, 'runs', 'history.json');
const lifecycleFile = join(__dirname, 'runs', 'lifecycle.json');
const reportFile = join(__dirname, 'runs', 'report.md');

test('audit.js runs end-to-end on the fixture and writes a valid envelope', async () => {
  // Clean slate: remove generated files so first run is clean (no prior history)
  await rm(out, { force: true });
  await rm(historyFile, { force: true });
  await rm(lifecycleFile, { force: true });
  await rm(reportFile, { force: true });

  const env = await main();
  assert.equal(env.success, true);
  assert.equal(env.agent_id, 'fabric-audit-agent');
  // Inc-2: estate fixture covers 4 domains; Inc-7 adds lineage; Inc-8 adds security+cost; expect at least 12 findings
  assert.ok(env.data.findings.length >= 12, `expected >= 12 findings, got ${env.data.findings.length}`);
  const onDisk = JSON.parse(await readFile(out, 'utf-8'));
  assert.ok(onDisk.data.findings.some(f => f.score.level === 'Critical'), 'no Critical findings');
  // domains represented — derive from key prefix (e.g. 'capacity.throttle::...' → 'capacity')
  const domainPrefixes = new Set(
    onDisk.data.findings.map(f => f.key.split('::')[0].split('.')[0])
  );
  assert.ok(domainPrefixes.has('capacity'), 'no capacity findings');
  assert.ok(domainPrefixes.has('model'), 'no model findings');
  assert.ok(domainPrefixes.has('report'), 'no report findings');
  assert.ok(domainPrefixes.has('pipeline'), 'no pipeline findings');
  // Inc-7: lineage domain
  assert.ok(domainPrefixes.has('lineage'), 'no lineage findings');
  assert.ok(onDisk.data.findings.some(f => f.key.startsWith('lineage')), 'no finding with lineage key prefix');

  // Inc-8: security and cost domains
  assert.ok(domainPrefixes.has('security'), 'no security findings');
  assert.ok(domainPrefixes.has('cost'), 'no cost findings');

  // Inc-3: digest should be present on clean first run
  assert.ok(env.data.digest, 'digest should be attached to envelope');
  assert.ok('totals' in env.data.digest, 'digest missing totals');
  assert.ok('byDomain' in env.data.digest, 'digest missing byDomain');
  assert.ok('newCount' in env.data.digest, 'digest missing newCount');
  assert.ok('recurring' in env.data.digest, 'digest missing recurring');

  // On clean first run all findings are new (no prior history)
  assert.equal(env.data.digest.newCount, env.data.findings.length,
    'on first clean run every finding should be new');

  // Inc-5: verdict — estate fixture has throttle + contention + oversized → optimize
  assert.ok(env.data.verdict, 'verdict should be attached to envelope');
  assert.equal(env.data.verdict.decision, 'optimize',
    `expected 'optimize' verdict on estate fixture, got '${env.data.verdict.decision}'`);

  // Inc-5: coaching — at least one finding carries a userTip
  assert.ok(
    env.data.findings.some(f => f.userTip),
    'expected at least one finding with a userTip on the estate fixture',
  );

  // Inc-11: lifecycle — with empty lifecycle store all 17 findings are active
  assert.equal(env.data.findings.length, 17, `expected exactly 17 findings, got ${env.data.findings.length}`);
  assert.equal(env.data.suppressed, undefined, 'no suppressed findings expected on clean run');

  // Inc-12: tenant tagging — fixture has capacity.tenant = 'Contoso'
  assert.equal(env.data.tenant, 'Contoso', `expected data.tenant to be 'Contoso', got '${env.data.tenant}'`);
  // Every finding should have a lifecycle object with state 'open'
  for (const f of env.data.findings) {
    assert.ok(f.lifecycle, `finding ${f.key} missing lifecycle object`);
    assert.equal(f.lifecycle.state, 'open', `finding ${f.key} should have state 'open', got '${f.lifecycle.state}'`);
  }

  // Inc-13: clean estate produces no dataQuality (facts are well-formed)
  assert.equal(env.data.dataQuality, undefined,
    'well-formed estate should produce no dataQuality key');

  // Inc-14: health score
  assert.ok(env.data.healthScore, 'healthScore should be attached to envelope');
  assert.equal(typeof env.data.healthScore.overall, 'number', 'healthScore.overall must be a number');
  assert.ok(env.data.healthScore.overall >= 0 && env.data.healthScore.overall <= 100,
    `healthScore.overall must be 0-100, got ${env.data.healthScore.overall}`);
  assert.ok(typeof env.data.healthScore.byDomain === 'object' && env.data.healthScore.byDomain !== null,
    'healthScore.byDomain must be an object');

  // Inc-14: roadmap
  assert.ok(Array.isArray(env.data.roadmap), 'roadmap must be an array');
  assert.equal(env.data.roadmap.length, env.data.findings.length,
    'roadmap length must equal total active findings');
  assert.equal(env.data.roadmap[0].rank, 1, 'roadmap first entry must have rank 1');

  // Inc-15: accountability — clean first run must NOT surface accountability
  assert.equal(env.data.accountability, undefined,
    'clean first run (empty history) must not have data.accountability');

  // Inc-16: clean single run (empty history → only 1 metric point) → NO data.forecast
  assert.equal(env.data.forecast, undefined,
    'clean first run (empty history) must not have data.forecast (insufficient history for forecast)');

  // Inc-17: clean single run (no history) → NO data.outcomes
  assert.equal(env.data.outcomes, undefined,
    'clean first run (empty history) must not have data.outcomes');

  // Inc-18: clean single run (no history) → NO data.anomalies
  assert.equal(env.data.anomalies, undefined,
    'clean first run (empty history) must not have data.anomalies (insufficient history for baseline)');

  // Inc-19: cross-domain correlations — estate has throttle+contention+oversized,
  // model.refresh-failing, pipeline.failing, and 3 security findings → all 3 themes present.
  assert.ok(Array.isArray(env.data.correlations), 'correlations should be an array');
  const themes = env.data.correlations.map(c => c.theme);
  assert.ok(themes.includes('capacity-pressure'), 'expected capacity-pressure theme');
  assert.ok(themes.includes('refresh-chain'),     'expected refresh-chain theme');
  assert.ok(themes.includes('security-cluster'),  'expected security-cluster theme');
  // Each correlation has the required shape
  for (const c of env.data.correlations) {
    assert.ok(typeof c.theme === 'string', 'correlation theme must be a string');
    assert.ok(Array.isArray(c.findingKeys) && c.findingKeys.length > 0, 'correlation findingKeys must be non-empty');
    assert.ok(typeof c.narrative === 'string' && c.narrative.length > 0, 'correlation narrative must be non-empty');
  }

  // Inc-20: stagger plan — estate fixture has 3 datasets colliding at 06:00;
  // Sales (4.2 GB, largest) keeps its slot; Forecast and Logistics are staggered.
  assert.ok(Array.isArray(env.data.staggerPlan), 'staggerPlan should be an array');
  assert.equal(env.data.staggerPlan.length, 2, 'expected 2 stagger entries (Forecast + Logistics)');
  const staggerForecast = env.data.staggerPlan.find(s => s.dataset === 'Forecast');
  assert.ok(staggerForecast, 'Forecast should be in staggerPlan');
  assert.equal(staggerForecast.from, '06:00');
  assert.equal(staggerForecast.to, '06:15');
  const staggerLogistics = env.data.staggerPlan.find(s => s.dataset === 'Logistics');
  assert.ok(staggerLogistics, 'Logistics should be in staggerPlan');
  assert.equal(staggerLogistics.from, '06:00');
  assert.equal(staggerLogistics.to, '06:30');

  // Inc-23: clean single run (empty history → age 0) → NO data.sla
  assert.equal(env.data.sla, undefined,
    'clean first run (empty history → age 0) must not have data.sla');

  // Inc-24: routing rules — estate has security(3), cost(2), and powerbi-team domains(12)
  assert.ok(env.data.routing, 'data.routing should be attached on the estate fixture');
  assert.equal(env.data.routing['security-team']?.length, 3,
    `expected security-team(3), got ${env.data.routing['security-team']?.length}`);
  assert.equal(env.data.routing['finops']?.length, 2,
    `expected finops(2), got ${env.data.routing['finops']?.length}`);
  assert.equal(env.data.routing['powerbi-team']?.length, 12,
    `expected powerbi-team(12), got ${env.data.routing['powerbi-team']?.length}`);
  // All 17 active finding keys should appear in routing
  const allRoutedKeys = Object.values(env.data.routing).flat();
  assert.equal(allRoutedKeys.length, env.data.findings.length,
    'total routed keys must equal total active findings');

  // Inc-28: markdown report — runs/report.md is written and contains required sections
  const reportMd = await readFile(reportFile, 'utf-8');
  assert.ok(reportMd.includes('# Fabric Audit Report'), 'report.md must contain title');
  assert.ok(reportMd.includes('## Findings (17)'), 'report.md must contain Findings (17)');

  // Inc-31: every finding has a confidence field
  for (const f of env.data.findings) {
    assert.ok(['high', 'medium', 'low'].includes(f.confidence),
      `finding ${f.key} must have confidence high/medium/low, got ${f.confidence}`);
  }

  // Inc-31: runLog exists with readOnly:true and a collectedDomains array
  assert.ok(env.data.runLog, 'data.runLog should be attached to envelope');
  assert.equal(env.data.runLog.readOnly, true, 'runLog.readOnly must be true');
  assert.ok(Array.isArray(env.data.runLog.collectedDomains),
    'runLog.collectedDomains must be an array');
  // estate.json has capacity, so at least that domain must appear
  assert.ok(env.data.runLog.collectedDomains.includes('capacity'),
    'runLog.collectedDomains must include capacity for this fixture');

  // Clean up so this test is repeatable
  await rm(historyFile, { force: true });
  await rm(lifecycleFile, { force: true });
});

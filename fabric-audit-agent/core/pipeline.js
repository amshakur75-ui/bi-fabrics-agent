import { detectAll } from './detectors/index.js';
import { validateFacts } from './validate.js';
import { wrapEnvelope } from './finding.js';
import { dedupe } from './automation/dedupe.js';
import { applyEscalation } from './automation/escalate.js';
import { annotateRecurring } from './automation/trend.js';
import { buildDigest } from './automation/digest.js';
import { buildCapacityVerdict } from './verdict.js';
import { getUserTip } from './coaching.js';
import { applyLifecycle } from './lifecycle.js';
import { DEFAULT_CONFIG } from './config.js';
import { buildHealthScore } from './health-score.js';
import { buildRoadmap } from './roadmap.js';
import { annotateAccountability, summarizeAccountability } from './accountability.js';
import { assessSla, summarizeSla } from './sla.js';
import { forecastCapacity } from './forecast.js';
import { assessOutcomes } from './outcomes.js';
import { detectAnomalies } from './anomaly.js';
import { correlate } from './correlate.js';
import { planStagger } from './stagger.js';
import { routeFindings } from './routing.js';
import { viewFor } from './audience.js';
import { execNarrative } from './narrative.js';
import { scoreConfidence } from './confidence.js';
import { buildRunLog } from './run-log.js';

function summarize(findings) {
  const crit = findings.filter(f => f.score.level === 'Critical').length;
  const warn = findings.filter(f => f.score.level === 'Warning').length;
  return `Audit complete: ${findings.length} findings (${crit} critical, ${warn} warning).`;
}

/**
 * Run a read-only audit. All I/O is injected via ports, so the same core runs
 * against mock or real adapters (transfer).
 * @param {{
 *   collector: { collect: () => Promise<object> },
 *   reasoner:  { reason: (facts:object, flags:object[]) => Promise<object[]> },
 *   delivery:  { deliver: (envelope:object) => Promise<any> },
 *   store?:    { history: () => Promise<object[]>, append: (run:object) => Promise<any> },
 *   lifecycleStore?: { load: () => Promise<object>, save: (states:object) => Promise<object> },
 *   agentId: string,
 *   now?: string,   // ISO timestamp for the run; defaults to new Date().toISOString()
 *   config?: object,
 *   tenant?: string
 * }} ports
 */
export async function runAudit({ collector, reasoner, delivery, store, lifecycleStore, agentId, now, config = DEFAULT_CONFIG, tenant }) {
  const runAt = now ?? new Date().toISOString();
  const nowMs = Date.parse(runAt);

  const facts = await collector.collect();
  const validation = validateFacts(facts);
  const resolvedTenant = tenant ?? facts?.capacity?.tenant ?? 'default';
  const flags = detectAll(facts, config);
  let findings = dedupe(await reasoner.reason(facts, flags));

  let suppressed = [];
  if (lifecycleStore) {
    const states = await lifecycleStore.load();
    const split = applyLifecycle(findings, states, nowMs);
    findings = split.active;
    suppressed = split.suppressed;
  }

  let digest = null;

  let forecast = null;

  let outcomes = null;

  let anomalies = [];

  if (store) {
    const history = await store.history();
    findings = applyEscalation(findings, history);
    findings = annotateRecurring(findings, history);
    findings = annotateAccountability(findings, history);
    findings = assessSla(findings, history, nowMs);
    digest = buildDigest(findings, history);
    forecast = forecastCapacity([...history, { metrics: { peakCuPct: facts?.capacity?.peakCuPct ?? null } }]);
    outcomes = assessOutcomes(findings, history, facts?.capacity?.peakCuPct ?? null);
    anomalies = detectAnomalies(facts, history);
    await store.append({
      runAt,
      tenant: resolvedTenant,
      metrics: { peakCuPct: facts?.capacity?.peakCuPct ?? null },
      findings: [
        ...findings.map(f => ({ key: f.key, level: f.score.level, where: f.where, what: f.what, suppressed: false })),
        ...suppressed.map(f => ({ key: f.key, level: f.score.level, where: f.where, what: f.what, suppressed: true })),
      ],
    });
  }

  // User coaching — attach an author-facing tip where one applies.
  findings = findings.map(f => {
    const type = typeof f.key === 'string' ? f.key.split('::')[0] : undefined;
    const tip = type ? getUserTip(type) : null;
    return tip ? { ...f, userTip: tip } : f;
  });

  // Confidence — deterministic detections = high; Claude-enriched = medium; meta/errors = low.
  findings = findings.map(f => ({ ...f, confidence: scoreConfidence(f) }));

  // Capacity verdict — optimize vs size-up, with CU evidence.
  const verdict = buildCapacityVerdict(facts, flags);

  // Phase 4: estate health score + remediation roadmap from active findings.
  const healthScore = buildHealthScore(findings);
  const roadmap = buildRoadmap(findings);
  const correlations = correlate(findings);

  const envelope = wrapEnvelope({ agentId, findings, summary: summarize(findings) });
  envelope.data.tenant = resolvedTenant;
  envelope.data.verdict = verdict;
  if (digest) envelope.data.digest = digest;
  const accountability = summarizeAccountability(findings);
  if (accountability.ignoredCount > 0) envelope.data.accountability = accountability;
  const sla = summarizeSla(findings);
  if (sla.breachedCount > 0) envelope.data.sla = sla;
  envelope.data.healthScore = healthScore;
  envelope.data.roadmap = roadmap;
  if (correlations.length) envelope.data.correlations = correlations;
  const staggerPlan = planStagger(facts);
  if (staggerPlan.length) envelope.data.staggerPlan = staggerPlan;
  const routing = routeFindings(findings);
  if (Object.keys(routing).length) envelope.data.routing = routing;
  if (forecast?.runsToCeiling != null) envelope.data.forecast = forecast;
  if (outcomes && (outcomes.resolvedSinceLast.length || outcomes.metricDelta)) envelope.data.outcomes = outcomes;
  if (anomalies.length) envelope.data.anomalies = anomalies;
  if (suppressed.length) envelope.data.suppressed = suppressed.map(f => ({ key: f.key, state: f.lifecycle.state, what: f.what }));
  if (validation.issues.length) envelope.data.dataQuality = validation.issues;
  envelope.data.narrative = execNarrative(viewFor(envelope, 'exec'));
  envelope.data.runLog = buildRunLog(facts, envelope, runAt);
  await delivery.deliver(envelope);
  return envelope;
}

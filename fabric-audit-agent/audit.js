import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { createMockCollector } from './adapters/collector.mock.js';
import { createFileDelivery } from './adapters/delivery.file.js';
import { createStubReasoner } from './adapters/reasoner.stub.js';
import { createLocalStore } from './adapters/store.local.js';
import { createLifecycleStore } from './adapters/lifecycle.store.js';
import { runAudit } from './core/pipeline.js';
import { DEFAULT_CONFIG, mergeConfig } from './core/config.js';
import { summarizeOutcomes } from './core/outcomes.js';
import { buildMarkdownReport } from './core/report-md.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const AGENT_ID = 'fabric-audit-agent';

/** Wire the mock adapters and run one audit. */
export async function main() {
  const collector = createMockCollector(join(__dirname, 'fixtures', 'estate.json'));
  // optional: const config = process.env.FABRIC_AUDIT_CONFIG ? mergeConfig(JSON.parse(process.env.FABRIC_AUDIT_CONFIG)) : DEFAULT_CONFIG;
  const config = DEFAULT_CONFIG;
  let reasoner = createStubReasoner({ config });
  if (process.env.FABRIC_AUDIT_REASONER === 'claude' && process.env.ANTHROPIC_API_KEY) {
    const { default: Anthropic } = await import('@anthropic-ai/sdk');
    const { createClaudeReasoner } = await import('./adapters/reasoner.claude.js');
    reasoner = createClaudeReasoner({ client: new Anthropic(), config });
    console.log('Reasoner: Claude');
  }
  const outPath = join(__dirname, 'runs', 'latest.json');
  const delivery = createFileDelivery(outPath);
  const store = createLocalStore(join(__dirname, 'runs', 'history.json'));
  const lifecycleStore = createLifecycleStore(join(__dirname, 'runs', 'lifecycle.json'));

  const envelope = await runAudit({ collector, reasoner, delivery, store, lifecycleStore, config, agentId: AGENT_ID });
  console.log(envelope.summary);
  if (envelope.data.digest) {
    const d = envelope.data.digest;
    console.log(`Digest — new: ${d.newCount}, recurring: ${d.recurring.length}, by domain: ${JSON.stringify(d.byDomain)}`);
  }
  const v = envelope.data.verdict;
  console.log(`Verdict: ${v.decision.toUpperCase()} — ${v.reason}`);
  if (envelope.data.suppressed?.length) console.log(`Suppressed (handled): ${envelope.data.suppressed.length}`);
  const hs = envelope.data.healthScore;
  console.log(`Health: ${hs.overall}/100  ${JSON.stringify(hs.byDomain)}`);
  const top = envelope.data.roadmap.slice(0, 3).map(r => `#${r.rank} [${r.level}] ${r.what}`).join('  |  ');
  if (top) console.log(`Top fixes: ${top}`);
  if (envelope.data.correlations?.length) {
    console.log(`Correlations: ${envelope.data.correlations.map(c => c.theme).join(', ')}`);
  }
  if (envelope.data.forecast) console.log(`Forecast: ${envelope.data.forecast.message}`);
  if (envelope.data.accountability?.ignoredCount) {
    console.log(`Accountability: ${envelope.data.accountability.ignoredCount} finding(s) advised 3+ runs and still unresolved.`);
  }
  if (envelope.data.outcomes) {
    const s = summarizeOutcomes(envelope.data.outcomes);
    if (s) console.log(`Outcomes: ${s}.`);
  }
  if (envelope.data.anomalies?.length) {
    console.log(`Anomalies: ${envelope.data.anomalies.map(a => a.message).join('  |  ')}`);
  }
  if (envelope.data.staggerPlan?.length) {
    console.log(`Stagger plan: ${envelope.data.staggerPlan.map(s => `${s.dataset} ${s.from}→${s.to}`).join(', ')}`);
  }
  if (envelope.data.sla?.breachedCount) {
    console.log(`SLA: ${envelope.data.sla.breachedCount} finding(s) past their resolution target.`);
  }
  if (envelope.data.routing) {
    const r = Object.entries(envelope.data.routing).map(([dest, keys]) => `${dest}(${keys.length})`).join(', ');
    console.log(`Routing: ${r}`);
  }
  if (envelope.data.runLog) console.log(`Run log: read ${envelope.data.runLog.collectedDomains.length} domain(s), ${envelope.data.runLog.findingCount} findings (read-only).`);
  if (envelope.data.narrative) console.log(`\nSummary: ${envelope.data.narrative}`);
  console.log(`Findings written to ${outPath}`);
  const reportPath = join(__dirname, 'runs', 'report.md');
  await writeFile(reportPath, buildMarkdownReport(envelope), 'utf-8');
  console.log(`Report written to ${reportPath}`);
  return envelope;
}

// Run when invoked directly: node .../audit.js  (cross-platform check)
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}

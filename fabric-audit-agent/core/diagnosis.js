import { detectAll } from './detectors/index.js';
import { createStubReasoner } from '../adapters/reasoner.stub.js';
import { buildHealthScore } from './health-score.js';
import { buildRoadmap } from './roadmap.js';
import { buildCapacityVerdict } from './verdict.js';

/**
 * Run the full offline diagnosis over a facts object: detectors -> stub reasoner
 * -> health score, capacity verdict, prioritized roadmap. No network, no API key.
 * @param {object} facts
 */
export async function diagnose(facts) {
  const flags = detectAll(facts);
  const findings = await createStubReasoner().reason(facts, flags);
  return {
    flags,
    findings,
    health: buildHealthScore(findings),
    verdict: buildCapacityVerdict(facts, flags),
    roadmap: buildRoadmap(findings),
  };
}

/**
 * Render a diagnosis result as a human-readable block of text.
 * @param {{findings:any[], health:any, verdict:any, roadmap:any[]}} result
 * @returns {string}
 */
export function formatDiagnosis({ findings, health, verdict, roadmap }) {
  const L = ['', '================  YOUR ESTATE — DIAGNOSIS  ================', ''];
  if (!findings.length) {
    L.push('No issues detected from the data provided.');
    L.push('(If you expected findings, re-check the values / column mapping above.)');
  } else {
    for (const f of findings) {
      L.push(`[${f.score.level}] ${f.what}`);
      L.push(`    Why:    ${f.why}`);
      L.push(`    Impact: ${f.impact}`);
      L.push(`    Fix:    ${(f.fix ?? [])[0] ?? ''}`);
      L.push('');
    }
    L.push('-----------------------------------------------------------');
    L.push(`Findings: ${findings.length}   Health: ${health.overall}/100`);
    L.push(`By domain: ${JSON.stringify(health.byDomain)}`);
    L.push(`Capacity verdict: ${verdict.decision.toUpperCase()} — ${verdict.reason}`);
    L.push('', 'Do these first:');
    roadmap.slice(0, 5).forEach(r => L.push(`   #${r.rank} [${r.level}] ${r.what}`));
  }
  L.push('', '===========================================================', '');
  return L.join('\n');
}

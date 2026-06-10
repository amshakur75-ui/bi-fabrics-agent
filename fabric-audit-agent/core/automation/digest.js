import { domainOf } from '../key-utils.js';

/**
 * Build a digest rollup for a run. Pure.
 * @param {object[]} findings current (escalated/annotated) findings
 * @param {Array<{findings:Array<{key:string}>}>} history chronological (prior runs only)
 * @returns {{totals:object, byDomain:object, newCount:number, recurring:object[]}}
 */
export function buildDigest(findings, history) {
  const totals = { Critical: 0, Warning: 0, Info: 0 };
  const byDomain = {};
  for (const f of findings) {
    const lvl = f.score?.level ?? 'Info';
    totals[lvl] = (totals[lvl] ?? 0) + 1;
    const d = domainOf(f.key);
    byDomain[d] = (byDomain[d] ?? 0) + 1;
  }
  const prev = history.length ? history[history.length - 1].findings : [];
  const prevKeys = new Set(prev.map(r => r.key));
  const newCount = findings.filter(f => f.key && !prevKeys.has(f.key)).length;
  const recurring = findings
    .filter(f => (f.recurringRuns ?? 1) >= 3)
    .map(f => ({ key: f.key, recurringRuns: f.recurringRuns, level: f.score?.level }));
  return { totals, byDomain, newCount, recurring };
}

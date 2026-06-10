import { domainOf } from './key-utils.js';

const WEIGHT = { Critical: 8, Warning: 3, Info: 1 };

const penalty = (findings) => findings.reduce((s, f) => s + (WEIGHT[f.score?.level] ?? 0), 0);

/**
 * Compute an estate health score from active findings. 100 = clean; each finding
 * subtracts by severity weight, floored at 0. Overall + per-domain. Pure.
 * @param {object[]} findings
 * @returns {{ overall: number, byDomain: Record<string, number> }}
 */
export function buildHealthScore(findings = []) {
  const overall = Math.max(0, 100 - penalty(findings));
  const groups = {};
  for (const f of findings) (groups[domainOf(f.key)] ??= []).push(f);
  const byDomain = {};
  for (const [d, fs] of Object.entries(groups)) byDomain[d] = Math.max(0, 100 - penalty(fs));
  return { overall, byDomain };
}

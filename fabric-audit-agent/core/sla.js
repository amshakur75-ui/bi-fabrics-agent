import { firstSeenMap } from './accountability.js';

const SLA_DAYS = { Critical: 1, Warning: 7, Info: 30 };
const DAY_MS = 86_400_000;

/**
 * Annotate findings with SLA age vs target (from first-seen in history). Pure.
 * @param {object[]} findings  active findings (with .key, .score)
 * @param {Array<object>} history  chronological run records
 * @param {number} nowMs  current time in ms (0 disables)
 * @param {Record<string,number>} [slaDays]
 * @returns {object[]}
 */
export function assessSla(findings = [], history = [], nowMs = 0, slaDays = SLA_DAYS) {
  const firstSeen = firstSeenMap(history);
  return findings.map(f => {
    const fs = firstSeen[f.key];
    const target = slaDays[f.score?.level];
    if (!fs || target == null || nowMs <= 0) return f;
    const firstSeenMs = Date.parse(fs);
    if (!Number.isFinite(firstSeenMs)) return f;
    const ageDays = Math.floor((nowMs - firstSeenMs) / DAY_MS);
    return { ...f, sla: { ageDays, targetDays: target, breached: ageDays > target } };
  });
}

/** Summarize SLA breaches. Pure. */
export function summarizeSla(findings = []) {
  const breached = findings.filter(f => f.sla?.breached);
  return {
    breachedCount: breached.length,
    items: breached.map(f => ({ key: f.key, level: f.score?.level, ageDays: f.sla.ageDays, targetDays: f.sla.targetDays })),
  };
}

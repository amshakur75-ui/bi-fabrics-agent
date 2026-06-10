/**
 * Earliest run timestamp per key from chronological history (oldest first).
 * @param {Array<{runAt:string, findings:Array<{key:string}>}>} history
 */
export function firstSeenMap(history = []) {
  const seen = {};
  for (const run of history) {
    for (const rf of run.findings ?? []) {
      if (rf.key && !(rf.key in seen)) seen[rf.key] = run.runAt;
    }
  }
  return seen;
}

/**
 * Annotate findings that have recurred >= `threshold` runs AND are still 'open'
 * (not acknowledged/snoozed/resolved) with an accountability note. Pure.
 * Relies on `recurringRuns` (set by annotateRecurring) and `lifecycle.state` being present.
 * @param {object[]} findings
 * @param {Array<object>} history  chronological run records
 * @param {number} threshold  default 3
 * @returns {object[]}
 */
export function annotateAccountability(findings, history = [], threshold = 3) {
  const firstSeen = firstSeenMap(history);
  return findings.map(f => {
    const runs = f.recurringRuns ?? 1;
    const open = (f.lifecycle?.state ?? 'open') === 'open';
    if (runs >= threshold && open) {
      return {
        ...f,
        accountability: {
          openRuns: runs,
          firstSeen: firstSeen[f.key] ?? null,
          message: `Open for ${runs} consecutive run(s) with no resolution.`,
        },
      };
    }
    return f;
  });
}

/** Summarize the ignored (stale-advice) findings. Pure. */
export function summarizeAccountability(findings = []) {
  const ignored = findings.filter(f => f.accountability);
  return {
    ignoredCount: ignored.length,
    items: ignored.map(f => ({ key: f.key, openRuns: f.accountability.openRuns, firstSeen: f.accountability.firstSeen })),
  };
}

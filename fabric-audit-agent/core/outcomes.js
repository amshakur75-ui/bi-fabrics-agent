/**
 * Compare the current run to the previous one: which active findings disappeared
 * (resolved/fixed), and how the peak-CU metric moved. Pure.
 * @param {object[]} currentFindings  current active findings (with .key)
 * @param {Array<{metrics?:{peakCuPct?:number}, findings?:Array<{key:string, suppressed?:boolean}>}>} history  chronological
 * @param {number|null} currentMetric  current peakCuPct
 * @returns {{ resolvedSinceLast: string[], metricDelta: object|null }}
 */
export function assessOutcomes(currentFindings = [], history = [], currentMetric = null) {
  if (!history.length) return { resolvedSinceLast: [], metricDelta: null };
  const prev = history[history.length - 1];
  const prevActive = new Set((prev.findings ?? []).filter(f => !f.suppressed && f.key).map(f => f.key));
  const cur = new Set(currentFindings.map(f => f.key));
  const resolvedSinceLast = [...prevActive].filter(k => !cur.has(k));

  let metricDelta = null;
  const from = prev.metrics?.peakCuPct;
  if (typeof from === 'number' && typeof currentMetric === 'number') {
    metricDelta = {
      metric: 'peakCuPct',
      from,
      to: currentMetric,
      change: Math.round((currentMetric - from) * 10) / 10,
      improved: currentMetric < from,
    };
  }
  return { resolvedSinceLast, metricDelta };
}

/** One-line human summary, or '' if nothing to report. Pure. */
export function summarizeOutcomes(outcomes = { resolvedSinceLast: [], metricDelta: null }) {
  const parts = [];
  if (outcomes.resolvedSinceLast.length) {
    parts.push(`${outcomes.resolvedSinceLast.length} finding(s) resolved since the last run`);
  }
  if (outcomes.metricDelta) {
    const d = outcomes.metricDelta;
    parts.push(`peak CU ${d.improved ? 'improved' : 'rose'} ${d.from}% → ${d.to}%`);
  }
  return parts.join('; ');
}

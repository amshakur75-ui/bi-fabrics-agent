/**
 * Annotate each finding with how many of the recent runs (window) contained its key.
 * Adds `recurringRuns` (integer, current run counts as 1). Pure.
 * @param {object[]} findings
 * @param {Array<{findings:Array<{key:string}>}>} history chronological
 * @param {number} window default 7
 * @returns {object[]}
 */
export function annotateRecurring(findings, history, window = 7) {
  const recent = history.slice(-window);
  return findings.map(f => {
    const priorHits = f.key ? recent.filter(run => run.findings.some(rf => rf.key === f.key)).length : 0;
    return { ...f, recurringRuns: priorHits + 1 };
  });
}

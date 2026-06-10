/**
 * Escalate a current Warning to Critical when the same key was present in BOTH
 * of the two most recent prior runs (i.e., unresolved across 3 consecutive runs).
 * Presence-based (not level-based) so a recurring issue keeps escalating.
 * Pure: returns a new array, does not mutate inputs.
 * @param {object[]} findings  current findings (with .key and .score)
 * @param {Array<{runAt:string, findings:Array<{key:string}>}>} history chronological
 * @returns {object[]}
 */
export function applyEscalation(findings, history) {
  const lastTwo = history.slice(-2);
  if (lastTwo.length < 2) return findings.map(f => ({ ...f }));
  const presentInAll = (key) => lastTwo.every(run => run.findings.some(rf => rf.key === key));
  return findings.map(f => {
    if (f.score?.level === 'Warning' && f.key && presentInAll(f.key)) {
      return {
        ...f,
        score: { level: 'Critical', reason: `${f.score.reason} (escalated: unresolved 3 consecutive runs)` },
      };
    }
    return { ...f };
  });
}

const SEV_RANK = { Critical: 0, Warning: 1, Info: 2 };

/**
 * Order active findings into a sequenced remediation plan: severity first, then
 * longest-standing (recurringRuns) first. Pure — returns a new ranked array.
 * @param {object[]} findings
 * @returns {Array<{rank:number, key:string, level:string, what:string, fix:string|null, recurringRuns:number}>}
 */
export function buildRoadmap(findings = []) {
  return [...findings]
    .sort((a, b) => {
      const s = (SEV_RANK[a.score?.level] ?? 9) - (SEV_RANK[b.score?.level] ?? 9);
      if (s !== 0) return s;
      return (b.recurringRuns ?? 1) - (a.recurringRuns ?? 1);
    })
    .map((f, i) => ({
      rank: i + 1,
      key: f.key,
      level: f.score?.level ?? 'Info',
      what: f.what,
      fix: f.fix?.[0] ?? null,
      recurringRuns: f.recurringRuns ?? 1,
    }));
}

const REQUIRED_CAPACITY = ['capacityId', 'sku', 'memoryGB', 'peakCuPct'];
const ARRAY_DOMAINS = ['models', 'reports', 'pipelines'];

/**
 * Non-fatal facts validation. Missing domains are fine (just not audited);
 * present-but-malformed shapes are reported. Pure.
 * @param {object} facts
 * @returns {{ ok: boolean, issues: Array<{domain:string, issue:string}> }}
 */
export function validateFacts(facts = {}) {
  const issues = [];
  if (facts.capacity) {
    for (const k of REQUIRED_CAPACITY) {
      if (facts.capacity[k] === undefined) issues.push({ domain: 'capacity', issue: `missing ${k}` });
    }
    if (facts.capacity.refreshes !== undefined && !Array.isArray(facts.capacity.refreshes)) {
      issues.push({ domain: 'capacity', issue: 'refreshes must be an array' });
    }
  }
  for (const d of ARRAY_DOMAINS) {
    if (facts[d] !== undefined && !Array.isArray(facts[d])) issues.push({ domain: d, issue: 'expected an array' });
  }
  if (facts.lineage && !Array.isArray(facts.lineage.nodes)) {
    issues.push({ domain: 'lineage', issue: 'nodes must be an array' });
  }
  return { ok: issues.length === 0, issues };
}

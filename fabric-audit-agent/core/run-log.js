const DOMAINS = ['capacity', 'models', 'reports', 'pipelines', 'lineage', 'access', 'usage'];

/**
 * Build the agent's own audit trail for a run: what it read + produced. Pure (time injected).
 * @param {object} facts
 * @param {object} envelope
 * @param {string} at  ISO timestamp
 */
export function buildRunLog(facts = {}, envelope = {}, at = '') {
  const d = envelope.data ?? {};
  return {
    at,
    collectedDomains: DOMAINS.filter(dom => facts[dom] != null),
    findingCount: (d.findings ?? []).length,
    suppressedCount: (d.suppressed ?? []).length,
    readOnly: true,
    note: 'Agent is read-only; only outward action is delivering findings.',
  };
}

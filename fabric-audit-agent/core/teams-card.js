/**
 * Format an audit envelope into a Teams-style card payload (representative shape;
 * the exact Adaptive Card schema is finalized against Azure Bot Service at transfer).
 * Pure: envelope in, card object out.
 * @param {object} envelope
 */
export function buildTeamsCard(envelope) {
  const d = envelope?.data ?? {};
  const findings = d.findings ?? [];
  const criticals = findings.filter(f => f.score?.level === 'Critical');
  const verdict = d.verdict;
  const sections = [
    { heading: 'Summary', text: envelope?.summary ?? '' },
  ];
  if (verdict) {
    sections.push({ heading: 'Capacity verdict', text: `${String(verdict.decision).toUpperCase()} — ${verdict.reason}` });
  }
  sections.push({
    heading: `Critical findings (${criticals.length})`,
    items: criticals.slice(0, 10).map(f => `${f.what} — Fix: ${f.fix?.[0] ?? 'see report'}`),
  });
  return { type: 'message', summary: envelope?.summary ?? 'Fabric audit', sections };
}

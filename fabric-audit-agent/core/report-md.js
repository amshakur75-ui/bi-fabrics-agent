/**
 * Render a finished audit envelope as a Markdown report. Pure: envelope in, string out.
 * @param {object} envelope
 * @returns {string}
 */
export function buildMarkdownReport(envelope = {}) {
  const d = envelope.data ?? {};
  const findings = d.findings ?? [];
  const L = [];

  L.push('# Fabric Audit Report');
  L.push('');
  if (envelope.summary) L.push(`_${envelope.summary}_`);
  if (d.tenant) L.push(`Tenant: **${d.tenant}**`);
  if (d.narrative) { L.push(''); L.push(d.narrative); }

  if (d.healthScore) {
    L.push('', `## Health: ${d.healthScore.overall}/100`, '', '| Domain | Score |', '|---|---|');
    for (const [dom, s] of Object.entries(d.healthScore.byDomain ?? {})) L.push(`| ${dom} | ${s} |`);
  }

  if (d.verdict) {
    L.push('', `## Capacity verdict: ${String(d.verdict.decision).toUpperCase()}`, '', d.verdict.reason ?? '');
  }

  if (d.roadmap?.length) {
    L.push('', '## Remediation roadmap');
    for (const r of d.roadmap) L.push(`${r.rank}. **[${r.level}]** ${r.what}${r.fix ? ` — _Fix:_ ${r.fix}` : ''}`);
  }

  L.push('', `## Findings (${findings.length})`);
  for (const f of findings) {
    L.push('', `### [${f.score?.level ?? 'Info'}] ${f.what ?? ''}`);
    L.push(`- **Where:** ${f.where ?? ''}`);
    L.push(`- **Why:** ${f.why ?? ''}`);
    L.push(`- **Impact:** ${f.impact ?? ''}`);
    L.push(`- **Fix:** ${(f.fix ?? []).join('; ')}`);
  }

  if (d.correlations?.length) {
    L.push('', '## Correlations');
    for (const c of d.correlations) L.push(`- **${c.theme}:** ${c.narrative}`);
  }

  return L.join('\n');
}

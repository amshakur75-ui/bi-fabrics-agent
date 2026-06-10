/**
 * Produce an audience-tailored view from a finished audit envelope. Pure.
 * @param {object} envelope
 * @param {'exec'|'team'|'author'} audience
 */
export function viewFor(envelope = {}, audience = 'team') {
  const d = envelope.data ?? {};
  const findings = d.findings ?? [];

  if (audience === 'exec') {
    return {
      audience: 'exec',
      health: d.healthScore?.overall ?? null,
      verdict: d.verdict?.decision ?? null,
      critical: findings.filter(f => f.score?.level === 'Critical').length,
      warning: findings.filter(f => f.score?.level === 'Warning').length,
      topFindings: (d.roadmap ?? []).slice(0, 3).map(r => ({ what: r.what, level: r.level })),
      accountability: d.accountability?.ignoredCount ?? 0,
    };
  }
  if (audience === 'author') {
    return {
      audience: 'author',
      items: findings.filter(f => f.userTip).map(f => ({ what: f.what, tip: f.userTip })),
    };
  }
  // team (default) — full working view
  return {
    audience: 'team',
    findings,
    roadmap: d.roadmap ?? [],
    routing: d.routing ?? {},
    sla: d.sla ?? null,
    correlations: d.correlations ?? [],
  };
}

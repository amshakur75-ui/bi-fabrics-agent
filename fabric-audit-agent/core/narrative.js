const VERDICT_TEXT = {
  'size-up': 'a capacity increase is warranted',
  'optimize': 'optimization opportunities remain before any capacity increase',
  'healthy': 'capacity is healthy',
  'unknown': 'capacity status is unknown',
};

/**
 * Deterministic leadership narrative from an exec view. Pure.
 * (At transfer, the Claude reasoner can rewrite this from the same sanitized inputs.)
 * @param {object} execView  output of viewFor(envelope, 'exec')
 * @returns {string}
 */
export function execNarrative(execView = {}) {
  const v = execView;
  const parts = [
    `Estate health is ${v.health ?? '—'}/100 with ${v.critical ?? 0} critical and ${v.warning ?? 0} warning finding(s).`,
    `On capacity, ${VERDICT_TEXT[v.verdict] ?? 'status is unclear'}.`,
  ];
  if (v.accountability) parts.push(`${v.accountability} issue(s) have been flagged repeatedly without resolution.`);
  if (v.topFindings?.length) parts.push(`Top priority: ${v.topFindings[0].what}`);
  return parts.join(' ');
}

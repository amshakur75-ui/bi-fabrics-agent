/** @typedef {{level:'Critical'|'Warning'|'Info', reason:string}} Score */
/** @typedef {{what:string, where:string, when:string, why:string, impact:string, fix:string[], score:Score}} Finding */

const REQUIRED = ['what', 'where', 'when', 'why', 'impact', 'fix', 'score'];

/**
 * Build a validated 7-field finding.
 * @param {Finding} parts
 * @returns {Finding}
 */
export function createFinding(parts) {
  for (const k of REQUIRED) {
    if (parts[k] === undefined || parts[k] === null) {
      throw new Error(`createFinding: missing required field "${k}"`);
    }
  }
  if (!Array.isArray(parts.fix)) {
    throw new Error('createFinding: "fix" must be an array');
  }
  return {
    what: parts.what,
    where: parts.where,
    when: parts.when,
    why: parts.why,
    impact: parts.impact,
    fix: parts.fix,
    score: parts.score,
  };
}

/**
 * Wrap findings in the OS standard output envelope.
 * @param {{agentId:string, findings:Finding[], summary:string}} args
 */
export function wrapEnvelope({ agentId, findings, summary }) {
  return {
    success: true,
    agent_id: agentId,
    data: { findings },
    summary,
    timestamp: new Date().toISOString(),
  };
}

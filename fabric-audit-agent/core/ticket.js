import { domainOf } from './key-utils.js';

/**
 * Build a tracker-agnostic work item from a finding. Pure.
 * @param {object} finding
 * @returns {{ title:string, body:string, severity:string, labels:string[], externalKey:string|undefined }}
 */
export function buildTicket(finding = {}) {
  const level = finding.score?.level ?? 'Info';
  const fixes = (finding.fix ?? []).map(x => `- ${x}`).join('\n');
  return {
    title: `[${level}] ${finding.what ?? 'Fabric audit finding'}`,
    body: [
      `Where: ${finding.where ?? ''}`,
      `Why: ${finding.why ?? ''}`,
      `Impact: ${finding.impact ?? ''}`,
      `Fix:\n${fixes}`,
    ].join('\n\n'),
    severity: level,
    labels: ['fabric-audit', domainOf(finding.key)],
    externalKey: finding.key,
  };
}

import { buildTicket } from '../core/ticket.js';

const LEVEL_RANK = { Critical: 0, Warning: 1, Info: 2 };

/**
 * Open tracked work items for findings via an injected client. Severity-gated + deduped.
 * The injected `client` implements `createIssue(ticket) => Promise`. At transfer this is a
 * Jira / Azure DevOps / ServiceNow client; in tests it's a fake that captures calls.
 * @param {{ client: { createIssue: Function }, minLevel?: string }} deps
 */
export function createTicketingDelivery({ client, minLevel = 'Critical' }) {
  const floor = LEVEL_RANK[minLevel];
  if (floor == null) throw new Error(`createTicketingDelivery: unknown minLevel "${minLevel}". Valid: ${Object.keys(LEVEL_RANK).join(', ')}`);
  return {
    /**
     * @param {object[]} findings
     * @param {Set<string>} [alreadyTicketed]  keys that already have a ticket (dedupe)
     * @returns {Promise<{ created: string[] }>}
     */
    async open(findings = [], alreadyTicketed = new Set()) {
      const created = [];
      for (const f of findings) {
        if ((LEVEL_RANK[f.score?.level] ?? 9) > floor) continue;   // below severity floor
        if (f.key && alreadyTicketed.has(f.key)) continue;          // dedupe
        await client.createIssue(buildTicket(f));
        if (f.key) created.push(f.key);
      }
      return { created };
    },
  };
}

import { DEFAULT_CONFIG } from './config.js';

/**
 * Project the capacity impact of a proposed asset against current facts. Pure.
 * @param {object} facts  current estate facts (reads facts.capacity)
 * @param {{ kind?:string, sizeGB?:number, refreshAt?:string, name?:string }} proposed
 * @param {object} [config]
 * @returns {{ proposed:object, impacts:string[], riskScore:number, verdict:'safe'|'risky'|'blocked' }}
 */
export function assessWhatIf(facts = {}, proposed = {}, config = DEFAULT_CONFIG) {
  const c = facts?.capacity ?? {};
  const impacts = [];
  let risk = 0;

  if (proposed.refreshAt) {
    const sameWindow = (c.refreshes ?? []).filter(r => r.scheduledAt === proposed.refreshAt);
    if (sameWindow.length >= 1) {
      impacts.push(`Refreshing at ${proposed.refreshAt} joins ${sameWindow.length} existing refresh(es) — worsens contention.`);
      risk += (sameWindow.length >= (config.capacity.contentionMin - 1)) ? 2 : 1;
    }
  }
  if (proposed.kind === 'model' && (proposed.sizeGB ?? 0) >= config.capacity.oversizedGB) {
    impacts.push(`Proposed model is ${proposed.sizeGB} GB (>= ${config.capacity.oversizedGB} GB oversized threshold).`);
    risk += 1;
  }
  if ((c.peakCuPct ?? 0) >= config.capacity.throttleWarnPct) {
    impacts.push(`Capacity ${c.capacityId ?? ''} already peaks at ${c.peakCuPct}% CU — little headroom for new load.`.trim());
    risk += 2;
  }

  const verdict = risk >= 4 ? 'blocked' : risk >= 2 ? 'risky' : 'safe';
  return { proposed, impacts, riskScore: risk, verdict };
}

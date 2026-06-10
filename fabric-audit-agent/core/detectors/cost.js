import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Cost / unused-resource detectors. Pure: facts in, flags out.
 * @param {{usage?:object}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectCost(facts, config = DEFAULT_CONFIG) {
  const u = facts?.usage ?? {};
  const flags = [];

  for (const r of u.reports ?? []) {
    if ((r.views30d ?? 0) === 0) {
      flags.push({
        type: 'cost.unused-report',
        resource: `${r.workspace} / ${r.name}`,
        when: '',
        evidence: { views30d: 0 },
        what: `Report "${r.name}" has had 0 views in 30 days.`,
      });
    }
  }

  for (const c of u.capacities ?? []) {
    if ((c.avgCuPct ?? 100) < config.cost.idleCuPct) {
      flags.push({
        type: 'cost.idle-capacity',
        resource: `capacity ${c.id}`,
        when: '',
        evidence: { sku: c.sku, avgCuPct: c.avgCuPct },
        what: `Capacity ${c.id} (${c.sku}) averaged ${c.avgCuPct}% CU — largely idle.`,
      });
    }
  }

  return flags;
}

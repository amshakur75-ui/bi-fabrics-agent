import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Noisy-neighbor detector: flag any single item consuming >= threshold% of the
 * capacity's CU. Reads `facts.items` (per-item CU share, populated by the
 * Capacity Metrics items importer). Pure: facts in, flags out.
 *
 * The flag is intentionally USER-FIRST: if activity-log correlation has attached
 * `topUsers` (see the user-attribution increment), the message leads with who is
 * driving the consumption so the team can reach out. Until then it reports the
 * distinct-user count and notes that named users are pending correlation.
 *
 * @param {{items?:object[]}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectConcentration(facts, config = DEFAULT_CONFIG) {
  const items = facts?.items ?? [];
  const min = config.capacity.concentrationPct;
  const flags = [];

  for (const it of items) {
    const share = Number(it.sharePct);
    if (!Number.isFinite(share) || share < min) continue;

    const ws = it.workspace || 'unknown workspace';
    const named = Array.isArray(it.topUsers) && it.topUsers.length ? it.topUsers : null;
    const who = named
      ? `${named[0].user}${named.length > 1 ? ` + ${named.length - 1} more` : ''}`
      : (it.users ? `${it.users} user(s)` : 'unknown users');

    const what = named
      ? `${who} are driving ${share}% of capacity CU via "${it.name}" (${ws}).`
      : `"${it.name}" (${ws}) is using ${share}% of capacity CU across ${who} — specific users pending activity-log correlation.`;

    flags.push({
      type: 'capacity.concentration',
      resource: `${it.workspace || '(unknown ws)'} / ${it.name}`,
      when: it.observedAt ?? '',
      evidence: {
        sharePct: share,
        cuSeconds: it.cuSeconds ?? null,
        kind: it.kind ?? null,
        users: it.users ?? null,
        topUsers: named,
        owner: it.owner ?? null,
      },
      what,
    });
  }
  return flags;
}

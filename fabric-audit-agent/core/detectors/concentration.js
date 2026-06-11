import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Noisy-neighbor detector: flag any single item consuming >= threshold% of the
 * capacity's CU. Reads `facts.items` (per-item CU share from the Capacity Metrics
 * items importer); when activity-log attribution has been attached (see
 * core/attribution.js) the flag is USER-FIRST. Pure: facts in, flags out.
 *
 * Message degrades honestly:
 *   - named interactive users present  -> "U1, U2 + N more are driving X% via <item>"
 *   - background-dominated load         -> "<item> is using X% — background ops, owner: O (not a consumer)"
 *   - no attribution yet                -> "<item> is using X% across N users — specific users pending correlation"
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
    const totalUsers = it.userCount ?? it.users ?? (named ? named.length : null);

    let what;
    if (named && it.background) {
      const owner = it.owner || named[0].user;
      what = `"${it.name}" (${ws}) is using ${share}% of capacity CU — driven mainly by background operations (owner/initiator: ${owner}), not interactive users.`;
    } else if (named) {
      const names = named.map(u => u.user).join(', ');
      const more = totalUsers != null ? Math.max(0, totalUsers - named.length) : 0;
      what = `${names}${more > 0 ? ` + ${more} more` : ''} are driving ${share}% of capacity CU via "${it.name}" (${ws}).`;
    } else {
      const who = it.users ? `${it.users} user(s)` : 'unknown users';
      what = `"${it.name}" (${ws}) is using ${share}% of capacity CU across ${who} — specific users pending activity-log correlation.`;
    }

    flags.push({
      type: 'capacity.concentration',
      resource: `${it.workspace || '(unknown ws)'} / ${it.name}`,
      when: it.observedAt ?? '',
      evidence: {
        sharePct: share,
        cuSeconds: it.cuSeconds ?? null,
        kind: it.kind ?? null,
        users: it.users ?? null,
        userCount: it.userCount ?? null,
        topUsers: named,
        background: it.background ?? false,
        owner: it.owner ?? null,
        attributionMode: it.attributionMode ?? null,
      },
      what,
    });
  }
  return flags;
}

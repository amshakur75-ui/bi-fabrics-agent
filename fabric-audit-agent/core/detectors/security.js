import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Security / access detectors. Pure: facts in, flags out.
 * @param {{access?:object}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectSecurity(facts, config = DEFAULT_CONFIG) {
  const a = facts?.access ?? {};
  const flags = [];

  for (const g of a.adminGrants ?? []) {
    if (/admin/i.test(g.role) && g.sensitive) {
      flags.push({
        type: 'security.admin-grant',
        resource: `${g.workspace}`,
        when: g.grantedAt ?? '',
        evidence: { principal: g.principal, role: g.role, sensitive: true },
        what: `Admin role granted to ${g.principal} on sensitive workspace "${g.workspace}".`,
      });
    }
  }

  for (const s of a.externalShares ?? []) {
    flags.push({
      type: 'security.external-share',
      resource: `${s.workspace} / ${s.item}`,
      when: s.at ?? '',
      evidence: { sharedWith: s.sharedWith },
      what: `"${s.item}" shared externally with ${s.sharedWith}.`,
    });
  }

  for (const e of a.accessEvents ?? []) {
    const base = e.baselineCount ?? 0;
    const ratio = base > 0 ? e.count / base : (e.count > 0 ? Infinity : 0);
    if (ratio >= config.security.unusualRatio) {
      flags.push({
        type: 'security.unusual-access',
        resource: `${e.workspace}`,
        when: '',
        evidence: { user: e.user, count: e.count, baselineCount: base, ratio: Number.isFinite(ratio) ? Math.round(ratio) : 999 },
        what: `${e.user} accessed "${e.workspace}" ${e.count} times vs a baseline of ${base}.`,
      });
    }
  }

  return flags;
}

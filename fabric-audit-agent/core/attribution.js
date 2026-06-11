const DEFAULT_TOP_N = 3;

/**
 * Decide which user(s) are driving an item's CU consumption, from that item's
 * activity records in the window. Pure: no I/O.
 *
 * Ranking — "both if available": if ANY event carries a cost proxy (cpuMs or
 * durationMs from Log Analytics), rank users by summed cost ('cost' mode);
 * otherwise rank by operation count ('frequency' mode, e.g. Activity Events only).
 *
 * Interactive vs background: each event says whether it was interactive. If the
 * item's load is mostly background (scheduled refresh, etc.), `background` is true
 * so callers name the owner/initiator — NOT an interactive "consumer."
 *
 * @param {Array<{user?:string, at?:string, interactive?:boolean, cpuMs?:number, durationMs?:number, op?:string}>} events  events for ONE item, already windowed
 * @param {{topN?:number, owner?:string}} [opts]
 * @returns {{mode:'cost'|'frequency', userCount:number, background:boolean, owner:string|null, topUsers:Array<{user:string, ops:number, cpuMs:number, interactive:boolean, score:number}>}}
 */
export function attributeUsers(events = [], opts = {}) {
  const topN = opts.topN ?? DEFAULT_TOP_N;
  const hasCost = events.some(e => Number.isFinite(e.cpuMs) || Number.isFinite(e.durationMs));
  const mode = hasCost ? 'cost' : 'frequency';

  const by = new Map();
  let bgContribution = 0;
  let totalContribution = 0;
  for (const e of events) {
    const user = String(e.user ?? '').trim();
    if (!user) continue;
    const cost = Number.isFinite(e.cpuMs) ? e.cpuMs : (Number.isFinite(e.durationMs) ? e.durationMs : 0);
    const contribution = mode === 'cost' ? cost : 1;   // weight "background" by COST in cost mode
    totalContribution += contribution;
    if (!e.interactive) bgContribution += contribution;
    const cur = by.get(user) ?? { user, ops: 0, cpuMs: 0, interactiveOps: 0 };
    cur.ops += 1;
    cur.cpuMs += cost;
    if (e.interactive) cur.interactiveOps += 1;
    by.set(user, cur);
  }

  const users = [...by.values()].map(u => ({
    user: u.user,
    ops: u.ops,
    cpuMs: Math.round(u.cpuMs),
    interactive: u.interactiveOps > 0,
    score: mode === 'cost' ? u.cpuMs : u.ops,
  }));
  // rank by score, then ops, then name (stable + deterministic)
  users.sort((a, b) => b.score - a.score || b.ops - a.ops || a.user.localeCompare(b.user));

  const background = totalContribution > 0 && bgContribution / totalContribution >= 0.5;

  return {
    mode,
    userCount: by.size,
    background,
    owner: opts.owner ?? null,
    topUsers: users.slice(0, topN),
  };
}

/**
 * Attach attribution to each item: sets topUsers / userCount / background / owner /
 * attributionMode on items that have events. Items without events are returned
 * unchanged (the detector then says "pending correlation").
 * @param {object[]} items
 * @param {Record<string, object[]>} eventsByItem  keyed by item name (or id)
 * @param {{topN?:number, owner?:string}} [opts]
 */
export function enrichItems(items = [], eventsByItem = {}, opts = {}) {
  return items.map(it => {
    const events = eventsByItem[it.name] ?? eventsByItem[it.id] ?? [];
    if (!events.length) return it;
    const a = attributeUsers(events, { topN: opts.topN, owner: it.owner ?? opts.owner });
    return {
      ...it,
      topUsers: a.topUsers,
      userCount: a.userCount,
      background: a.background,
      owner: a.owner ?? it.owner ?? null,
      attributionMode: a.mode,
    };
  });
}

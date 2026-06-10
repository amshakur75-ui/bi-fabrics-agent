/** Add minutes to an "HH:MM" string, wrapping at 24h. Pure. */
function addMinutes(hhmm, mins) {
  const [h, m] = String(hhmm).split(':').map(Number);
  const total = ((h * 60 + m + mins) % 1440 + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * Propose a staggered refresh schedule for colliding refresh times. The largest model
 * keeps its slot; the rest are pushed out in `spacingMin` increments. Pure.
 * @param {object} facts  reads facts.capacity.refreshes [{ workspace, dataset, scheduledAt, sizeGB }]
 * @param {{ spacingMin?:number, minGroup?:number }} [opts]
 * @returns {Array<{ dataset:string, workspace:string, from:string, to:string }>}
 */
export function planStagger(facts = {}, { spacingMin = 15, minGroup = 2 } = {}) {
  const refreshes = facts?.capacity?.refreshes ?? [];
  const byTime = {};
  for (const r of refreshes) (byTime[r.scheduledAt] ??= []).push(r);

  const plan = [];
  for (const [time, group] of Object.entries(byTime)) {
    if (group.length < minGroup) continue;
    const sorted = [...group].sort((a, b) => (b.sizeGB ?? 0) - (a.sizeGB ?? 0)); // largest first keeps slot
    sorted.forEach((r, i) => {
      const to = addMinutes(time, i * spacingMin);
      if (to !== r.scheduledAt) plan.push({ dataset: r.dataset, workspace: r.workspace, from: r.scheduledAt, to });
    });
  }
  return plan;
}

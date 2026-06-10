import { DEFAULT_CONFIG } from '../config.js';

/**
 * @typedef {{type:string, resource:string, when:string, evidence:object, what:string}} Flag
 */

/**
 * Deterministic capacity detectors. Pure: facts in, flags out.
 * @param {{capacity?:object}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectCapacity(facts, config = DEFAULT_CONFIG) {
  const c = facts?.capacity;
  if (!c) return [];
  const flags = [];

  // 1. Throttle risk
  if (c.peakCuPct >= config.capacity.throttleWarnPct) {
    flags.push({
      type: 'capacity.throttle',
      resource: `${c.tenant} / capacity ${c.capacityId}`,
      when: c.peakAt,
      evidence: { peakCuPct: c.peakCuPct, throttleMinutes: c.throttleMinutes, sku: c.sku },
      what: `Capacity ${c.capacityId} reached ${c.peakCuPct}% CU (${c.throttleMinutes} min throttled).`,
    });
  }

  // 2. Refresh contention (>=3 refreshes share a start time)
  const byTime = {};
  for (const r of c.refreshes ?? []) (byTime[r.scheduledAt] ??= []).push(r);
  for (const [time, group] of Object.entries(byTime)) {
    if (group.length >= config.capacity.contentionMin) {
      flags.push({
        type: 'capacity.contention',
        resource: `${c.tenant} / capacity ${c.capacityId}`,
        when: time,
        evidence: { time, datasets: group.map(r => r.dataset) },
        what: `${group.length} datasets refresh simultaneously at ${time}.`,
      });
    }
  }

  // 3. Oversized model (>= 4 GB)
  for (const r of c.refreshes ?? []) {
    if (r.sizeGB >= config.capacity.oversizedGB) {
      flags.push({
        type: 'capacity.oversized-model',
        resource: `${c.tenant} / ${r.workspace} / ${r.dataset}`,
        when: r.scheduledAt,
        evidence: { sizeGB: r.sizeGB, memoryGB: c.memoryGB, durationMin: r.durationMin },
        what: `Model "${r.dataset}" is ${r.sizeGB} GB and refreshes in ${r.durationMin} min.`,
      });
    }
  }

  return flags;
}

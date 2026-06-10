const NEXT_SKU = { F2:'F4', F4:'F8', F8:'F16', F16:'F32', F32:'F64', F64:'F128', F128:'F256', F256:'F512' };

/**
 * Decide whether the capacity needs optimization first or a genuine size-up.
 * Pure: derives from capacity facts + the capacity-domain flags.
 * @param {{capacity?:object}} facts
 * @param {Array<{type:string}>} flags
 * @returns {{decision:'optimize'|'size-up'|'healthy'|'unknown', reason:string, evidence:object}}
 */
export function buildCapacityVerdict(facts, flags) {
  const c = facts?.capacity;
  if (!c) return { decision: 'unknown', reason: 'No capacity telemetry available.', evidence: {} };

  const capFlags = (flags ?? []).filter(f => f.type.startsWith('capacity.'));
  const throttling = capFlags.some(f => f.type === 'capacity.throttle');
  if (!throttling) {
    return {
      decision: 'healthy',
      reason: `Capacity ${c.capacityId} peaked at ${c.peakCuPct}% CU — within limits.`,
      evidence: { peakCuPct: c.peakCuPct },
    };
  }

  const optimizations = capFlags
    .filter(f => f.type === 'capacity.contention' || f.type === 'capacity.oversized-model')
    .map(f => f.type);

  if (optimizations.length) {
    return {
      decision: 'optimize',
      reason: `Capacity is throttling, but ${optimizations.length} optimization(s) remain — fix these before paying for a bigger SKU.`,
      evidence: { peakCuPct: c.peakCuPct, throttleMinutes: c.throttleMinutes, optimizations },
    };
  }

  return {
    decision: 'size-up',
    reason: `Capacity ${c.capacityId} is throttling with no remaining optimizations — the honest answer is a larger SKU.`,
    evidence: {
      peakCuPct: c.peakCuPct,
      throttleMinutes: c.throttleMinutes,
      currentSku: c.sku,
      recommendedSku: NEXT_SKU[c.sku] ?? 'next tier up',
    },
  };
}

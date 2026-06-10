function durationMin(startIso, endIso) {
  const s = Date.parse(startIso), e = Date.parse(endIso);
  return (Number.isFinite(s) && Number.isFinite(e)) ? Math.round((e - s) / 60000) : 0;
}

/**
 * Map raw Power BI/Fabric capacity telemetry into our `capacity` facts shape.
 * Pure: raw API JSON in, facts out. (Field names/units differ from the API on purpose —
 * this is the real transformation that the transfer step verifies against live responses.)
 * @param {{capacity:object, refreshes:object[]}} raw
 * @returns {{capacity:object}}
 */
export function mapCapacity(raw = {}) {
  const c = raw.capacity ?? {};
  const refreshes = (raw.refreshes ?? []).map(r => ({
    workspace: r.groupName,
    dataset: r.datasetName,
    scheduledAt: r.scheduleTime,
    durationMin: durationMin(r.startTime, r.endTime),
    sizeGB: Math.round(((r.sizeBytes ?? 0) / 1e9) * 10) / 10,
  }));
  return {
    capacity: {
      tenant: c.tenantName,
      capacityId: c.displayName ?? c.id,
      sku: c.sku,
      memoryGB: c.memoryGb,
      peakCuPct: c.peakCuPercent,
      peakAt: c.peakTimestamp,
      throttleMinutes: c.throttledMinutes,
      refreshes,
    },
  };
}

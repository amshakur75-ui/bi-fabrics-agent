import { mapCapacity } from './capacity.js';

const toGB = (bytes) => Math.round(((bytes ?? 0) / 1e9) * 10) / 10;

export function mapModels(raw = []) {
  return raw.map(m => ({
    workspace: m.groupName, name: m.name,
    sizeGB: toGB(m.sizeBytes), bidirectionalRels: m.relationshipsBidi ?? 0,
    autoDateTime: !!m.autoTimeIntelligence, refreshFailRatePct: m.refreshFailureRatePct ?? 0,
    observedAt: m.observedAt ?? '',
  }));
}

export function mapReports(raw = []) {
  return raw.map(r => ({
    workspace: r.groupName, name: r.name,
    visuals: r.visualCount ?? 0, mode: r.storageMode ?? 'Import',
    slowestVisualMs: r.slowestVisualMs ?? 0, source: r.datasourceType ?? 'unknown',
  }));
}

export function mapPipelines(raw = []) {
  return raw.map(p => ({
    workspace: p.groupName, name: p.name,
    lastStatus: p.lastRunStatus ?? 'Succeeded', failRatePct: p.failurePct ?? 0,
    gatewayHealthy: p.gatewayHealthy !== false, lastRunAt: p.lastRunTime ?? '',
  }));
}

export function mapLineage(raw = {}) {
  return {
    nodes: (raw.items ?? []).map(i => ({ id: i.id, type: i.itemType, workspace: i.groupName, name: i.displayName, status: i.status ?? 'OK', failedAt: i.failedAt })),
    edges: (raw.links ?? []).map(l => ({ from: l.source, to: l.target })),
  };
}

export function mapAccess(raw = {}) {
  return {
    adminGrants: raw.adminGrants ?? [],
    externalShares: raw.externalShares ?? [],
    accessEvents: raw.accessEvents ?? [],
  };
}

export function mapUsage(raw = {}) {
  return {
    reports: (raw.reportViews ?? []).map(r => ({ workspace: r.groupName, name: r.name, views30d: r.views30d ?? 0 })),
    capacities: (raw.capacityUtil ?? []).map(c => ({ id: c.id, sku: c.sku, avgCuPct: c.avgCuPercent ?? 0 })),
  };
}

/**
 * Map a full raw API bundle into the complete facts shape. Pure.
 * @param {object} raw  { capacity, refreshes, datasets, reports, pipelines, lineage, access, usage }
 */
export function toFacts(raw = {}) {
  return {
    ...mapCapacity({ capacity: raw.capacity, refreshes: raw.refreshes }),
    models: mapModels(raw.datasets),
    reports: mapReports(raw.reports),
    pipelines: mapPipelines(raw.pipelines),
    lineage: mapLineage(raw.lineage),
    access: mapAccess(raw.access),
    usage: mapUsage(raw.usage),
  };
}

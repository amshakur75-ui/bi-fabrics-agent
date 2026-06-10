import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  mapModels,
  mapReports,
  mapPipelines,
  mapLineage,
  mapAccess,
  mapUsage,
  toFacts,
} from './index.js';

// --- mapModels ---

test('mapModels renames groupName→workspace, sizeBytes→sizeGB (4.2e9 → 4.2)', () => {
  const result = mapModels([
    { groupName: 'Finance', name: 'Sales', sizeBytes: 4.2e9, relationshipsBidi: 9, autoTimeIntelligence: true, refreshFailureRatePct: 12 },
  ]);
  assert.equal(result[0].workspace, 'Finance');
  assert.equal(result[0].name, 'Sales');
  assert.equal(result[0].sizeGB, 4.2);
  assert.equal(result[0].bidirectionalRels, 9);
  assert.equal(result[0].autoDateTime, true);
  assert.equal(result[0].refreshFailRatePct, 12);
});

test('mapModels sets autoDateTime=false when autoTimeIntelligence is falsy', () => {
  const result = mapModels([
    { groupName: 'HR', name: 'Headcount', sizeBytes: 0, autoTimeIntelligence: false },
  ]);
  assert.equal(result[0].autoDateTime, false);
});

test('mapModels defaults bidirectionalRels to 0 when absent', () => {
  const result = mapModels([{ groupName: 'X', name: 'Y', sizeBytes: 0 }]);
  assert.equal(result[0].bidirectionalRels, 0);
});

test('mapModels defaults refreshFailRatePct to 0 when absent', () => {
  const result = mapModels([{ groupName: 'X', name: 'Y', sizeBytes: 0 }]);
  assert.equal(result[0].refreshFailRatePct, 0);
});

test('mapModels with empty array returns []', () => {
  assert.deepEqual(mapModels([]), []);
});

test('mapModels with no argument returns []', () => {
  assert.deepEqual(mapModels(), []);
});

// --- mapReports ---

test('mapReports renames groupName→workspace, visualCount→visuals, storageMode→mode', () => {
  const result = mapReports([
    { groupName: 'Finance', name: 'Exec Dashboard', visualCount: 41, storageMode: 'DirectQuery', slowestVisualMs: 12000, datasourceType: 'Synapse: dw_sales' },
  ]);
  assert.equal(result[0].workspace, 'Finance');
  assert.equal(result[0].visuals, 41);
  assert.equal(result[0].mode, 'DirectQuery');
  assert.equal(result[0].slowestVisualMs, 12000);
  assert.equal(result[0].source, 'Synapse: dw_sales');
});

test('mapReports defaults mode to Import when storageMode absent', () => {
  const result = mapReports([{ groupName: 'X', name: 'Y' }]);
  assert.equal(result[0].mode, 'Import');
});

test('mapReports defaults source to unknown when datasourceType absent', () => {
  const result = mapReports([{ groupName: 'X', name: 'Y' }]);
  assert.equal(result[0].source, 'unknown');
});

test('mapReports with empty array returns []', () => {
  assert.deepEqual(mapReports([]), []);
});

// --- mapPipelines ---

test('mapPipelines renames groupName→workspace, lastRunStatus→lastStatus, failurePct→failRatePct', () => {
  const result = mapPipelines([
    { groupName: 'Finance', name: 'Nightly Load', lastRunStatus: 'Failed', failurePct: 18, gatewayHealthy: false, lastRunTime: '2026-06-08T02:14:00.000Z' },
  ]);
  assert.equal(result[0].workspace, 'Finance');
  assert.equal(result[0].lastStatus, 'Failed');
  assert.equal(result[0].failRatePct, 18);
  assert.equal(result[0].gatewayHealthy, false);
  assert.equal(result[0].lastRunAt, '2026-06-08T02:14:00.000Z');
});

test('mapPipelines defaults lastStatus to Succeeded when absent', () => {
  const result = mapPipelines([{ groupName: 'X', name: 'Y' }]);
  assert.equal(result[0].lastStatus, 'Succeeded');
});

test('mapPipelines sets gatewayHealthy=false only when explicitly false', () => {
  const healthy = mapPipelines([{ groupName: 'X', name: 'Y', gatewayHealthy: true }]);
  assert.equal(healthy[0].gatewayHealthy, true);
  const unhealthy = mapPipelines([{ groupName: 'X', name: 'Y', gatewayHealthy: false }]);
  assert.equal(unhealthy[0].gatewayHealthy, false);
  const absent = mapPipelines([{ groupName: 'X', name: 'Y' }]);
  assert.equal(absent[0].gatewayHealthy, true);
});

test('mapPipelines with empty array returns []', () => {
  assert.deepEqual(mapPipelines([]), []);
});

// --- mapLineage ---

test('mapLineage maps items→nodes (itemType→type, displayName→name, groupName→workspace)', () => {
  const result = mapLineage({
    items: [
      { id: 'pl-1', itemType: 'pipeline', groupName: 'Finance', displayName: 'Nightly Load', status: 'Failed', failedAt: '2026-06-08T02:14:00.000Z' },
    ],
    links: [],
  });
  assert.equal(result.nodes.length, 1);
  assert.equal(result.nodes[0].id, 'pl-1');
  assert.equal(result.nodes[0].type, 'pipeline');
  assert.equal(result.nodes[0].workspace, 'Finance');
  assert.equal(result.nodes[0].name, 'Nightly Load');
  assert.equal(result.nodes[0].status, 'Failed');
  assert.equal(result.nodes[0].failedAt, '2026-06-08T02:14:00.000Z');
});

test('mapLineage maps links→edges (source→from, target→to)', () => {
  const result = mapLineage({
    items: [],
    links: [{ source: 'pl-1', target: 'ds-1' }],
  });
  assert.equal(result.edges.length, 1);
  assert.equal(result.edges[0].from, 'pl-1');
  assert.equal(result.edges[0].to, 'ds-1');
});

test('mapLineage with empty input returns { nodes: [], edges: [] }', () => {
  const result = mapLineage({});
  assert.deepEqual(result, { nodes: [], edges: [] });
});

test('mapLineage with no argument returns { nodes: [], edges: [] }', () => {
  const result = mapLineage();
  assert.deepEqual(result, { nodes: [], edges: [] });
});

// --- mapAccess ---

test('mapAccess passes through adminGrants, externalShares, accessEvents unchanged', () => {
  const raw = {
    adminGrants:    [{ workspace: 'Finance', principal: 'ext@vendor.com', role: 'Admin', grantedAt: '', sensitive: true }],
    externalShares: [{ workspace: 'Finance', item: 'Exec Dashboard', sharedWith: 'partner@othercorp.com', at: '' }],
    accessEvents:   [{ user: 'jdoe', workspace: 'Finance', count: 220, baselineCount: 20 }],
  };
  const result = mapAccess(raw);
  assert.equal(result.adminGrants.length, 1);
  assert.equal(result.externalShares.length, 1);
  assert.equal(result.accessEvents.length, 1);
});

test('mapAccess with no argument returns empty arrays', () => {
  const result = mapAccess();
  assert.deepEqual(result, { adminGrants: [], externalShares: [], accessEvents: [] });
});

// --- mapUsage ---

test('mapUsage maps reportViews→reports (groupName→workspace)', () => {
  const result = mapUsage({
    reportViews: [{ groupName: 'Ops', name: 'Old Quarterly', views30d: 0 }],
    capacityUtil: [],
  });
  assert.equal(result.reports[0].workspace, 'Ops');
  assert.equal(result.reports[0].name, 'Old Quarterly');
  assert.equal(result.reports[0].views30d, 0);
});

test('mapUsage maps capacityUtil→capacities (avgCuPercent→avgCuPct)', () => {
  const result = mapUsage({
    reportViews: [],
    capacityUtil: [{ id: 'F64', sku: 'F64', avgCuPercent: 3 }],
  });
  assert.equal(result.capacities[0].id, 'F64');
  assert.equal(result.capacities[0].sku, 'F64');
  assert.equal(result.capacities[0].avgCuPct, 3);
});

test('mapUsage with no argument returns { reports: [], capacities: [] }', () => {
  const result = mapUsage();
  assert.deepEqual(result, { reports: [], capacities: [] });
});

// --- toFacts ---

test('toFacts({}) returns all domains with empty/default values and does not throw', () => {
  const facts = toFacts({});
  assert.ok('capacity' in facts, 'should have capacity');
  assert.ok(Array.isArray(facts.models), 'models should be array');
  assert.equal(facts.models.length, 0);
  assert.ok(Array.isArray(facts.reports), 'reports should be array');
  assert.equal(facts.reports.length, 0);
  assert.ok(Array.isArray(facts.pipelines), 'pipelines should be array');
  assert.equal(facts.pipelines.length, 0);
  assert.ok(facts.lineage && Array.isArray(facts.lineage.nodes), 'lineage.nodes should be array');
  assert.ok(facts.lineage && Array.isArray(facts.lineage.edges), 'lineage.edges should be array');
  assert.ok('access' in facts, 'should have access');
  assert.ok(Array.isArray(facts.access.adminGrants), 'access.adminGrants should be array');
  assert.ok('usage' in facts, 'should have usage');
  assert.ok(Array.isArray(facts.usage.reports), 'usage.reports should be array');
});

test('toFacts spreads mapCapacity result (capacity key present with correct shape)', () => {
  const facts = toFacts({
    capacity: { displayName: 'F64', tenantName: 'Contoso' },
    refreshes: [],
    datasets: [],
    reports: [],
    pipelines: [],
  });
  assert.equal(facts.capacity.capacityId, 'F64');
  assert.equal(facts.capacity.tenant, 'Contoso');
});

test('toFacts converts datasets via mapModels', () => {
  const facts = toFacts({
    datasets: [{ groupName: 'Finance', name: 'Sales', sizeBytes: 4.2e9, relationshipsBidi: 9, autoTimeIntelligence: true, refreshFailureRatePct: 12 }],
  });
  assert.equal(facts.models.length, 1);
  assert.equal(facts.models[0].sizeGB, 4.2);
  assert.equal(facts.models[0].bidirectionalRels, 9);
});

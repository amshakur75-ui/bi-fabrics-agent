import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mapTable, mergeFacts, num } from './map.js';

test('num() extracts numbers from messy cells', () => {
  assert.equal(num('87%'), 87);
  assert.equal(num('1,234 ms'), 1234);
  assert.equal(num('4.2 GB'), 4.2);
  assert.ok(Number.isNaN(num('n/a')));
});

test('maps a capacity-metrics items export to capacity scalars + refreshes', () => {
  const headers = ['Capacity Name', 'SKU', 'Timepoint', 'CU % of base capacity', 'Throttling (min)', 'Workspace', 'Item Name', 'Size (GB)', 'Duration (min)', 'Scheduled'];
  const rows = [
    { 'Capacity Name': 'PROD-CAP', SKU: 'F64', Timepoint: '2026-06-09T09:00', 'CU % of base capacity': '72', 'Throttling (min)': '0', Workspace: 'Finance', 'Item Name': 'GL Model', 'Size (GB)': '5.1', 'Duration (min)': '14', Scheduled: '06:00' },
    { 'Capacity Name': 'PROD-CAP', SKU: 'F64', Timepoint: '2026-06-09T10:00', 'CU % of base capacity': '93', 'Throttling (min)': '12', Workspace: 'Sales', 'Item Name': 'Pipeline', 'Size (GB)': '1.2', 'Duration (min)': '4', Scheduled: '06:00' },
  ];
  const { capacity, coverage } = mapTable(headers, rows);
  assert.equal(capacity.sku, 'F64');
  assert.equal(capacity.capacityId, 'PROD-CAP');
  assert.equal(capacity.peakCuPct, 93);          // max across rows
  assert.equal(capacity.peakAt, '2026-06-09T10:00');
  assert.equal(capacity.throttleMinutes, 12);     // sum across rows
  assert.equal(capacity.refreshes.length, 2);
  assert.equal(capacity.refreshes[0].sizeGB, 5.1);
  assert.equal(capacity.refreshes[0].scheduledAt, '06:00');
  // coverage tells us where peakCuPct came from
  assert.ok(coverage.find(c => c.field === 'peakCuPct' && c.source === 'CU % of base capacity'));
});

test('real Capacity Metrics timepoint export: picks "Total CU Usage %", not "100% in CU(s)" or "CU % Limit"', () => {
  const headers = ['Background %', 'Interactive %', 'Background non-billable %', 'Interactive non-billable %', 'Interactive non-billable CU(s)', 'Background non-billable CU(s)', 'Interactive CU(s)', 'Background CU(s)', '100% in CU(s)', 'Autoscale %', 'Timepoint', 'Total CU Usage %', 'Total CU(s)', 'CU % Limit', 'Capacity State Change From Previous Window'];
  const rows = [
    { 'Total CU Usage %': '42', '100% in CU(s)': '30720', 'CU % Limit': '100', Timepoint: '2026-06-09T09:00' },
    { 'Total CU Usage %': '118', '100% in CU(s)': '30720', 'CU % Limit': '100', Timepoint: '2026-06-09T13:30' },
  ];
  const { capacity, coverage } = mapTable(headers, rows);
  assert.equal(coverage.find(c => c.field === 'peakCuPct').source, 'Total CU Usage %');
  assert.equal(capacity.peakCuPct, 118);          // not 30720
  assert.equal(capacity.peakAt, '2026-06-09T13:30');
  assert.equal(capacity.sku, '');                 // no SKU column -> not invented
});

test('records a coverage note when a needed capacity column is missing', () => {
  const { coverage } = mapTable(['SKU', 'CU %'], [{ SKU: 'F32', 'CU %': '50' }]);
  const throttle = coverage.find(c => c.field === 'throttleMinutes');
  assert.equal(throttle.source, null);
  assert.match(throttle.note, /throttling/);
});

test('maps a model export (vertipaq-ish columns)', () => {
  const headers = ['Workspace', 'Model Name', 'Size (GB)', 'Bidirectional Relationships', 'Auto Date/Time', 'Refresh Fail Rate %'];
  const rows = [{ Workspace: 'Finance', 'Model Name': 'GL', 'Size (GB)': '7.5', 'Bidirectional Relationships': '6', 'Auto Date/Time': 'Yes', 'Refresh Fail Rate %': '12' }];
  const { models } = mapTable(headers, rows);
  assert.equal(models.length, 1);
  assert.deepEqual(models[0], { workspace: 'Finance', name: 'GL', sizeGB: 7.5, bidirectionalRels: 6, autoDateTime: true, refreshFailRatePct: 12 });
});

test('maps a report export and normalizes storage mode', () => {
  const headers = ['Workspace', 'Report Name', 'Visuals', 'Storage Mode', 'Slowest Visual (ms)'];
  const rows = [{ Workspace: 'Sales', 'Report Name': 'Exec', Visuals: '34', 'Storage Mode': 'Direct Query', 'Slowest Visual (ms)': '8200' }];
  const { reports } = mapTable(headers, rows);
  assert.equal(reports[0].visuals, 34);
  assert.equal(reports[0].mode, 'DirectQuery');
  assert.equal(reports[0].slowestVisualMs, 8200);
});

test('mergeFacts combines capacity scalars (max CU, summed throttle) across files', () => {
  const a = mapTable(['SKU', 'CU %', 'Throttling min'], [{ SKU: 'F64', 'CU %': '70', 'Throttling min': '5' }]);
  const b = mapTable(['SKU', 'CU %', 'Throttling min'], [{ SKU: 'F64', 'CU %': '95', 'Throttling min': '8' }]);
  const facts = mergeFacts([a, b]);
  assert.equal(facts.capacity.peakCuPct, 95);
  assert.equal(facts.capacity.throttleMinutes, 13);
});

test('mergeFacts joins models from a separate file with capacity from another', () => {
  const cap = mapTable(['SKU', 'CU %'], [{ SKU: 'F64', 'CU %': '88' }]);
  const mod = mapTable(['Model Name', 'Bidirectional Rels'], [{ 'Model Name': 'X', 'Bidirectional Rels': '9' }]);
  const facts = mergeFacts([cap, mod]);
  assert.equal(facts.capacity.peakCuPct, 88);
  assert.equal(facts.models.length, 1);
  assert.equal(facts.models[0].bidirectionalRels, 9);
});

test('non-capacity, non-model table yields empty facts (no false capacity)', () => {
  const facts = mergeFacts([mapTable(['Foo', 'Bar'], [{ Foo: '1', Bar: '2' }])]);
  assert.deepEqual(facts, {});
});

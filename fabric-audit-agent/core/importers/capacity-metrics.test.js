import { test } from 'node:test';
import assert from 'node:assert/strict';
import { looksLikeItems, mapItems, looksLikeTimepoints, analyzeTimepoints, inspectColumns } from './capacity-metrics.js';

const ITEM_HEADERS = ['Workspace', 'Item kind', 'Item name', 'CU (s)', 'Duration (s)', 'Users', 'Rejected count', 'Billing type'];
const TP_HEADERS = ['Background %', 'Interactive %', '100% in CU(s)', 'Autoscale %', 'Timepoint', 'Total CU Usage %', 'Total CU(s)', 'CU % Limit', 'Capacity State Change From Previous Window'];

test('recognizes the Items table, not the timepoint table', () => {
  assert.equal(looksLikeItems(ITEM_HEADERS), true);
  assert.equal(looksLikeItems(TP_HEADERS), false);
});

test('mapItems ranks items by CU(s), totals CU, and sums rejections', () => {
  const rows = [
    { Workspace: 'Fin', 'Item kind': 'SemanticModel', 'Item name': 'GL', 'CU (s)': '700000', 'Duration (s)': '40', Users: '12', 'Rejected count': '3', 'Billing type': 'Billable' },
    { Workspace: 'Sales', 'Item kind': 'Report', 'Item name': 'Exec', 'CU (s)': '250000', 'Duration (s)': '5', Users: '80', 'Rejected count': '0', 'Billing type': 'Billable' },
    { Workspace: 'Ops', 'Item kind': 'SemanticModel', 'Item name': 'Inv', 'CU (s)': '50000', 'Duration (s)': '9', Users: '4', 'Rejected count': '0', 'Billing type': 'Billable' },
  ];
  const a = mapItems(ITEM_HEADERS, rows);
  assert.equal(a.itemCount, 3);
  assert.equal(a.totalCu, 1_000_000);
  assert.equal(a.top[0].name, 'GL');           // highest CU first
  assert.equal(a.top[0].pctOfTotal, 70);       // 700k / 1M
  assert.equal(a.rejectedTotal, 3);
  assert.equal(a.rejectedItems[0].name, 'GL');
});

test('recognizes the timepoint table and reads reported + computed utilization', () => {
  assert.equal(looksLikeTimepoints(TP_HEADERS), true);
  const rows = [
    { '100% in CU(s)': '30720', Timepoint: 't1', 'Total CU Usage %': '23069', 'Total CU(s)': '30720', 'Capacity State Change From Previous Window': 'None' },
    { '100% in CU(s)': '30720', Timepoint: 't2', 'Total CU Usage %': '15000', 'Total CU(s)': '46080', 'Capacity State Change From Previous Window': 'Overloaded' },
  ];
  const a = analyzeTimepoints(TP_HEADERS, rows);
  assert.equal(a.reportedPeakPct, 23069);      // raw spike from the % column
  assert.equal(a.baseline, 30720);
  assert.equal(a.computedPeakPct, 150);        // 46080 / 30720 * 100
  assert.deepEqual(a.states, { None: 1, Overloaded: 1 });
});

test('inspectColumns gives number stats, shows categories, hides label values', () => {
  const rows = [
    { 'Item name': 'Secret-Project-X', 'CU (s)': '100', 'Item kind': 'Report' },
    { 'Item name': 'Secret-Project-Y', 'CU (s)': '300', 'Item kind': 'SemanticModel' },
  ];
  const stats = inspectColumns(['Item name', 'CU (s)', 'Item kind'], rows);
  const byCol = Object.fromEntries(stats.map(s => [s.column, s]));
  assert.equal(byCol['CU (s)'].type, 'number');
  assert.equal(byCol['CU (s)'].max, 300);
  assert.equal(byCol['Item name'].type, 'label');     // hidden
  assert.equal(byCol['Item name'].values, undefined); // no leak
  assert.equal(byCol['Item kind'].type, 'category');
  assert.deepEqual(byCol['Item kind'].values, ['Report', 'SemanticModel']);
});

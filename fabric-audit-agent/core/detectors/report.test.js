import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectReports } from './report.js';

const execDashboard = {
  workspace: 'Finance', name: 'Exec Dashboard',
  visuals: 41, mode: 'DirectQuery', slowestVisualMs: 12000,
  source: 'Synapse: dw_sales',
};

const logisticsLive = {
  workspace: 'Ops', name: 'Logistics Live',
  visuals: 12, mode: 'Import', slowestVisualMs: 1200,
  source: 'Lakehouse: ops',
};

test('Exec Dashboard yields report.too-many-visuals, report.directquery, report.slow-visual', () => {
  const types = detectReports({ reports: [execDashboard] }).map(f => f.type).sort();
  assert.deepEqual(types, ['report.directquery', 'report.slow-visual', 'report.too-many-visuals']);
});

test('report.too-many-visuals carries correct visual count', () => {
  const f = detectReports({ reports: [execDashboard] }).find(f => f.type === 'report.too-many-visuals');
  assert.equal(f.evidence.visuals, 41);
  assert.equal(f.resource, 'Finance / Exec Dashboard');
});

test('report.directquery carries source evidence', () => {
  const f = detectReports({ reports: [execDashboard] }).find(f => f.type === 'report.directquery');
  assert.equal(f.evidence.source, 'Synapse: dw_sales');
});

test('report.slow-visual carries ms evidence', () => {
  const f = detectReports({ reports: [execDashboard] }).find(f => f.type === 'report.slow-visual');
  assert.equal(f.evidence.ms, 12000);
});

test('Logistics Live yields no flags', () => {
  assert.deepEqual(detectReports({ reports: [logisticsLive] }), []);
});

test('detectReports({}) returns []', () => {
  assert.deepEqual(detectReports({}), []);
});

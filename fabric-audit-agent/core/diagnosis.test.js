import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diagnose, formatDiagnosis } from './diagnosis.js';

test('diagnose flags a throttling capacity with remaining optimizations as "optimize"', async () => {
  const facts = {
    capacity: {
      tenant: 'Acme', capacityId: 'PROD', sku: 'F64', memoryGB: 64,
      peakCuPct: 95, peakAt: '2026-06-09T10:00', throttleMinutes: 20,
      refreshes: [
        { workspace: 'Fin', dataset: 'A', scheduledAt: '06:00', durationMin: 10, sizeGB: 6 },
        { workspace: 'Fin', dataset: 'B', scheduledAt: '06:00', durationMin: 10, sizeGB: 1 },
        { workspace: 'Fin', dataset: 'C', scheduledAt: '06:00', durationMin: 10, sizeGB: 1 },
      ],
    },
  };
  const result = await diagnose(facts);
  assert.ok(result.findings.length > 0, 'should produce findings');
  assert.equal(result.verdict.decision, 'optimize');
  assert.ok(result.health.overall <= 100 && result.health.overall >= 0);
});

test('formatDiagnosis renders findings and the verdict', async () => {
  const result = await diagnose({ capacity: { tenant: 'Acme', capacityId: 'P', sku: 'F64', memoryGB: 64, peakCuPct: 95, peakAt: '', throttleMinutes: 5, refreshes: [] } });
  const text = formatDiagnosis(result);
  assert.match(text, /DIAGNOSIS/);
  assert.match(text, /Capacity verdict:/);
});

test('formatDiagnosis handles a clean estate', () => {
  const text = formatDiagnosis({ findings: [], health: { overall: 100, byDomain: {} }, verdict: { decision: 'healthy', reason: 'ok' }, roadmap: [] });
  assert.match(text, /No issues detected/);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStubReasoner } from './reasoner.stub.js';
import { mergeConfig } from '../core/config.js';

test('turns flags into valid 7-field findings', async () => {
  const reasoner = createStubReasoner();
  const flags = [{
    type: 'capacity.throttle',
    resource: 'Contoso / capacity F64',
    when: '2026-06-08T06:05:00.000Z',
    evidence: { peakCuPct: 96, throttleMinutes: 42 },
    what: 'Capacity F64 reached 96% CU.',
  }];
  const findings = await reasoner.reason({}, flags);
  assert.equal(findings.length, 1);
  assert.equal(findings[0].score.level, 'Critical');
  assert.ok(findings[0].fix.length >= 1);
  assert.equal(findings[0].what, 'Capacity F64 reached 96% CU.');
});

test('returns [] for no flags', async () => {
  assert.deepEqual(await createStubReasoner().reason({}, []), []);
});

// ---------------------------------------------------------------------------
// Inc-12: config override in stub reasoner
// ---------------------------------------------------------------------------

test('createStubReasoner with throttleCritPct=99 yields Warning-level finding for 96% CU throttle', async () => {
  const config = mergeConfig({ capacity: { throttleCritPct: 99 } });
  const reasoner = createStubReasoner({ config });
  const flags = [{
    type: 'capacity.throttle',
    resource: 'Contoso / capacity F64',
    when: '2026-06-08T06:05:00.000Z',
    evidence: { peakCuPct: 96, throttleMinutes: 42 },
    what: 'Capacity F64 reached 96% CU.',
  }];
  const findings = await reasoner.reason({}, flags);
  assert.equal(findings.length, 1);
  assert.equal(findings[0].score.level, 'Warning', 'expected Warning with throttleCritPct=99 config');
});

test('createStubReasoner with default config still yields Critical for 96% CU throttle', async () => {
  const reasoner = createStubReasoner();
  const flags = [{
    type: 'capacity.throttle',
    resource: 'Contoso / capacity F64',
    when: '2026-06-08T06:05:00.000Z',
    evidence: { peakCuPct: 96, throttleMinutes: 42 },
    what: 'Capacity F64 reached 96% CU.',
  }];
  const findings = await reasoner.reason({}, flags);
  assert.equal(findings[0].score.level, 'Critical');
});

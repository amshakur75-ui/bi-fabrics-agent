import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { assessWhatIf } from './whatif.js';
import { DEFAULT_CONFIG } from './config.js';

// Estate-like facts matching fixtures/estate.json capacity section
const ESTATE_FACTS = {
  capacity: {
    tenant: 'Contoso',
    capacityId: 'F64',
    sku: 'F64',
    memoryGB: 64,
    peakCuPct: 96,
    peakAt: '2026-06-08T06:05:00.000Z',
    throttleMinutes: 42,
    refreshes: [
      { workspace: 'Finance', dataset: 'Sales',     scheduledAt: '06:00', durationMin: 47, sizeGB: 4.2 },
      { workspace: 'Finance', dataset: 'Forecast',  scheduledAt: '06:00', durationMin: 31, sizeGB: 2.1 },
      { workspace: 'Ops',     dataset: 'Logistics', scheduledAt: '06:00', durationMin: 22, sizeGB: 1.4 },
      { workspace: 'HR',      dataset: 'Headcount', scheduledAt: '09:00', durationMin: 6,  sizeGB: 0.3 },
    ],
  },
};

const HEALTHY_FACTS = {
  capacity: {
    capacityId: 'F8',
    peakCuPct: 40,
    refreshes: [],
  },
};

describe('assessWhatIf', () => {
  it('returns blocked for model with collision + oversized + hot capacity', () => {
    const proposed = { kind: 'model', sizeGB: 5, refreshAt: '06:00' };
    const result = assessWhatIf(ESTATE_FACTS, proposed);

    assert.equal(result.verdict, 'blocked');
    assert.equal(result.riskScore, 5);
    assert.equal(result.impacts.length, 3);

    // contention impact
    assert.ok(result.impacts.some(i => i.includes('06:00') && i.includes('contention')),
      'should mention contention at 06:00');
    // oversized impact
    assert.ok(result.impacts.some(i => i.includes('5 GB') && i.includes('oversized')),
      'should mention oversized model');
    // hot capacity impact
    assert.ok(result.impacts.some(i => i.includes('96%') && i.includes('headroom')),
      'should mention capacity headroom');
  });

  it('returns safe for small off-peak proposal against healthy facts', () => {
    const proposed = { kind: 'model', sizeGB: 1, refreshAt: '03:00' };
    const result = assessWhatIf(HEALTHY_FACTS, proposed);

    assert.equal(result.verdict, 'safe');
    assert.equal(result.riskScore, 0);
    assert.equal(result.impacts.length, 0);
  });

  it('returns one impact and riskScore 1 for oversized-only (healthy capacity, off-peak)', () => {
    const proposed = { kind: 'model', sizeGB: 5, refreshAt: '03:00' };
    const result = assessWhatIf(HEALTHY_FACTS, proposed);

    assert.equal(result.impacts.length, 1);
    assert.ok(result.impacts[0].includes('oversized') || result.impacts[0].includes('GB'));
    assert.equal(result.riskScore, 1);
    // risk=1 is below risky threshold of 2
    assert.ok(result.verdict === 'safe' || result.verdict === 'risky');
    assert.equal(result.riskScore, 1);
  });

  it('does not throw and returns safe when facts.capacity is missing', () => {
    // When facts.capacity is absent, no contention or hot-capacity checks fire.
    // The oversized check still fires (risk=1) but verdict remains 'safe' (risk < 2).
    const proposed = { kind: 'model', sizeGB: 5, refreshAt: '06:00' };
    let result;
    assert.doesNotThrow(() => { result = assessWhatIf({}, proposed); });
    assert.equal(result.verdict, 'safe');
    assert.ok(result.riskScore < 2, `riskScore ${result.riskScore} should be < 2 (safe threshold)`);
  });

  it('passes proposed through to result', () => {
    const proposed = { kind: 'report', name: 'My Report', sizeGB: 0, refreshAt: '03:00' };
    const result = assessWhatIf({}, proposed);
    assert.equal(result.proposed, proposed);
  });

  it('uses supplied config overrides', () => {
    // Lower the oversizedGB threshold so a 2 GB model triggers it
    const config = { ...DEFAULT_CONFIG, capacity: { ...DEFAULT_CONFIG.capacity, oversizedGB: 1, throttleWarnPct: 99 } };
    const proposed = { kind: 'model', sizeGB: 2, refreshAt: '03:00' };
    const result = assessWhatIf(HEALTHY_FACTS, proposed, config);
    assert.equal(result.impacts.length, 1);
    assert.ok(result.impacts[0].includes('2 GB'));
  });
});

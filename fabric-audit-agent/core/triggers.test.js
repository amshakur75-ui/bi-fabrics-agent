import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { shouldRunScheduled, evaluateThresholdTriggers } from './triggers.js';
import { DEFAULT_CONFIG } from './config.js';

// Estate fixture facts (mirrors fixtures/estate.json)
const ESTATE_FACTS = {
  capacity: {
    capacityId: 'F64',
    peakCuPct: 96,
  },
  pipelines: [
    { workspace: 'Finance', name: 'Nightly Load', lastStatus: 'Failed', failRatePct: 18 },
    { workspace: 'Ops', name: 'Hourly Sync', lastStatus: 'Succeeded', failRatePct: 2 },
  ],
  access: {
    adminGrants: [
      { workspace: 'Finance', principal: 'ext-contractor@vendor.com', role: 'Admin', sensitive: true },
    ],
  },
};

const HEALTHY_FACTS = {
  capacity: {
    capacityId: 'F8',
    peakCuPct: 40,
  },
  pipelines: [
    { workspace: 'Ops', name: 'Hourly Sync', lastStatus: 'Succeeded', failRatePct: 2 },
  ],
  access: {
    adminGrants: [
      { workspace: 'Ops', principal: 'user@corp.com', role: 'Member', sensitive: false },
    ],
  },
};

describe('shouldRunScheduled', () => {
  it('daily at 06:00 fires at 06:00 on any weekday', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'daily', atHour: 6, atMinute: 0 }, { hour: 6, minute: 0, dayOfWeek: 3 }),
      true
    );
  });

  it('daily at 06:00 does not fire at 07:00', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'daily', atHour: 6, atMinute: 0 }, { hour: 7, minute: 0, dayOfWeek: 3 }),
      false
    );
  });

  it('daily at 06:00 does not fire when minute mismatches', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'daily', atHour: 6, atMinute: 0 }, { hour: 6, minute: 5, dayOfWeek: 3 }),
      false
    );
  });

  it('hourly fires at any hour when minute matches', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'hourly', atMinute: 0 }, { hour: 3, minute: 0, dayOfWeek: 2 }),
      true
    );
    assert.equal(
      shouldRunScheduled({ cadence: 'hourly', atMinute: 0 }, { hour: 14, minute: 0, dayOfWeek: 5 }),
      true
    );
  });

  it('hourly does not fire when minute mismatches', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'hourly', atMinute: 0 }, { hour: 3, minute: 1, dayOfWeek: 2 }),
      false
    );
  });

  it('weekly fires only on the configured dayOfWeek', () => {
    // Monday = 1
    assert.equal(
      shouldRunScheduled({ cadence: 'weekly', atHour: 6, atMinute: 0, dayOfWeek: 1 }, { hour: 6, minute: 0, dayOfWeek: 1 }),
      true
    );
    // Wrong day (Tuesday = 2)
    assert.equal(
      shouldRunScheduled({ cadence: 'weekly', atHour: 6, atMinute: 0, dayOfWeek: 1 }, { hour: 6, minute: 0, dayOfWeek: 2 }),
      false
    );
  });

  it('weekly does not fire on the right day but wrong hour', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'weekly', atHour: 6, atMinute: 0, dayOfWeek: 1 }, { hour: 7, minute: 0, dayOfWeek: 1 }),
      false
    );
  });

  it('unknown cadence returns false', () => {
    assert.equal(
      shouldRunScheduled({ cadence: 'monthly', atMinute: 0 }, { hour: 6, minute: 0, dayOfWeek: 1 }),
      false
    );
  });

  it('uses defaults: daily at 06:00 on Monday', () => {
    // Default: cadence='daily', atHour=6, atMinute=0
    assert.equal(shouldRunScheduled({}, { hour: 6, minute: 0, dayOfWeek: 3 }), true);
    assert.equal(shouldRunScheduled({}, { hour: 7, minute: 0, dayOfWeek: 3 }), false);
  });
});

describe('evaluateThresholdTriggers', () => {
  it('returns 3 events against the estate fixture facts', () => {
    const events = evaluateThresholdTriggers(ESTATE_FACTS, DEFAULT_CONFIG);
    assert.equal(events.length, 3, `expected 3 events, got ${events.length}: ${JSON.stringify(events)}`);
  });

  it('includes a Critical capacity event for 96% >= 90% threshold', () => {
    const events = evaluateThresholdTriggers(ESTATE_FACTS, DEFAULT_CONFIG);
    const cap = events.find(e => e.reason.includes('96%') && e.reason.includes('CU'));
    assert.ok(cap, 'expected capacity critical event');
    assert.equal(cap.severity, 'Critical');
    assert.ok(cap.reason.includes('F64'), 'reason should include capacityId');
  });

  it('includes a Critical pipeline event for failed "Nightly Load"', () => {
    const events = evaluateThresholdTriggers(ESTATE_FACTS, DEFAULT_CONFIG);
    const pipe = events.find(e => e.reason.includes('Nightly Load'));
    assert.ok(pipe, 'expected pipeline failed event');
    assert.equal(pipe.severity, 'Critical');
  });

  it('includes a Critical admin grant event for sensitive workspace', () => {
    const events = evaluateThresholdTriggers(ESTATE_FACTS, DEFAULT_CONFIG);
    const grant = events.find(e => e.reason.toLowerCase().includes('admin') && e.reason.includes('Finance'));
    assert.ok(grant, 'expected admin grant event');
    assert.equal(grant.severity, 'Critical');
  });

  it('returns [] for a healthy facts object (peakCu 40, no failed pipelines, no sensitive admin grants)', () => {
    const events = evaluateThresholdTriggers(HEALTHY_FACTS, DEFAULT_CONFIG);
    assert.deepEqual(events, []);
  });

  it('returns [] when facts is empty', () => {
    const events = evaluateThresholdTriggers({}, DEFAULT_CONFIG);
    assert.deepEqual(events, []);
  });

  it('does not emit capacity event when below throttleCritPct', () => {
    const facts = { capacity: { capacityId: 'F8', peakCuPct: 89 }, pipelines: [], access: { adminGrants: [] } };
    const events = evaluateThresholdTriggers(facts, DEFAULT_CONFIG);
    assert.ok(!events.some(e => e.reason.includes('CU')), 'should not fire capacity event below threshold');
  });

  it('emits capacity event exactly at throttleCritPct boundary (90%)', () => {
    const facts = { capacity: { capacityId: 'F8', peakCuPct: 90 }, pipelines: [], access: { adminGrants: [] } };
    const events = evaluateThresholdTriggers(facts, DEFAULT_CONFIG);
    assert.ok(events.some(e => e.reason.includes('CU')), 'should fire capacity event at 90% threshold');
  });

  it('does not emit pipeline event for succeeded pipelines', () => {
    const facts = { pipelines: [{ name: 'Hourly Sync', lastStatus: 'Succeeded' }] };
    const events = evaluateThresholdTriggers(facts, DEFAULT_CONFIG);
    assert.deepEqual(events, []);
  });

  it('does not emit admin-grant event when sensitive is false', () => {
    const facts = { access: { adminGrants: [{ workspace: 'Ops', role: 'Admin', sensitive: false }] } };
    const events = evaluateThresholdTriggers(facts, DEFAULT_CONFIG);
    assert.deepEqual(events, []);
  });

  it('uses custom config throttleCritPct override', () => {
    const config = { ...DEFAULT_CONFIG, capacity: { ...DEFAULT_CONFIG.capacity, throttleCritPct: 95 } };
    // 96% >= 95% still fires
    const events = evaluateThresholdTriggers(ESTATE_FACTS, config);
    assert.ok(events.some(e => e.reason.includes('CU')));
    // 96% < 99% threshold does not fire
    const config2 = { ...DEFAULT_CONFIG, capacity: { ...DEFAULT_CONFIG.capacity, throttleCritPct: 99 } };
    const events2 = evaluateThresholdTriggers(ESTATE_FACTS, config2);
    assert.ok(!events2.some(e => e.reason.includes('CU')));
  });
});

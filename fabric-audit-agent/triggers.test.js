import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { checkTriggers } from './triggers.js';

describe('checkTriggers (CLI integration)', () => {
  it('resolves to a non-empty events array against the bundled estate fixture', async () => {
    const events = await checkTriggers();
    assert.ok(Array.isArray(events), 'events should be an array');
    assert.ok(events.length > 0, `expected at least one trigger event, got ${events.length}`);
  });

  it('includes the capacity-critical event (96% >= 90%)', async () => {
    const events = await checkTriggers();
    const cap = events.find(e => e.reason && e.reason.includes('CU'));
    assert.ok(cap, 'expected a capacity CU trigger event');
    assert.equal(cap.severity, 'Critical');
  });

  it('all events have reason and severity fields', async () => {
    const events = await checkTriggers();
    for (const e of events) {
      assert.ok(typeof e.reason === 'string' && e.reason.length > 0, 'each event should have a non-empty reason');
      assert.ok(typeof e.severity === 'string' && e.severity.length > 0, 'each event should have a non-empty severity');
    }
  });
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTicketingDelivery } from './ticketing.js';

/** Fake client: captures all createIssue calls, no network. */
function makeFakeClient() {
  const calls = [];
  return {
    async createIssue(ticket) {
      calls.push(ticket);
    },
    get calls() { return calls; },
  };
}

const makeFinding = (level, key) => ({
  key,
  what: `Issue at ${level}`,
  where: 'test-workspace',
  why: 'test reason',
  impact: 'test impact',
  fix: ['Fix it'],
  score: { level, reason: 'test' },
});

const CRITICAL_1 = makeFinding('Critical', 'capacity.cu::res-a');
const CRITICAL_2 = makeFinding('Critical', 'capacity.cu::res-b');
const WARNING_1  = makeFinding('Warning',  'model.refresh::res-c');
const INFO_1     = makeFinding('Info',     'report.visual::res-d');

test('createTicketingDelivery: default minLevel:Critical only tickets Critical findings', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client });
  const result = await delivery.open([CRITICAL_1, WARNING_1, INFO_1]);

  assert.equal(client.calls.length, 1, 'only one createIssue call');
  assert.equal(result.created.length, 1);
  assert.equal(result.created[0], CRITICAL_1.key);
});

test('createTicketingDelivery: created lists all ticketed keys', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client });
  const result = await delivery.open([CRITICAL_1, CRITICAL_2, WARNING_1]);

  assert.deepEqual(result.created.sort(), [CRITICAL_1.key, CRITICAL_2.key].sort());
  assert.equal(client.calls.length, 2);
});

test('createTicketingDelivery: alreadyTicketed skips duplicate Critical', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client });
  const alreadyTicketed = new Set([CRITICAL_1.key]);
  const result = await delivery.open([CRITICAL_1, CRITICAL_2], alreadyTicketed);

  assert.equal(client.calls.length, 1, 'only the non-deduped Critical ticketed');
  assert.equal(result.created[0], CRITICAL_2.key);
});

test('createTicketingDelivery: minLevel:Warning tickets Criticals + Warnings', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client, minLevel: 'Warning' });
  const result = await delivery.open([CRITICAL_1, WARNING_1, INFO_1]);

  assert.equal(client.calls.length, 2, 'Critical + Warning ticketed');
  assert.ok(result.created.includes(CRITICAL_1.key), 'Critical in created');
  assert.ok(result.created.includes(WARNING_1.key), 'Warning in created');
  assert.ok(!result.created.includes(INFO_1.key), 'Info not in created');
});

test('createTicketingDelivery: empty findings returns { created: [] } without calling createIssue', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client });
  const result = await delivery.open([]);

  assert.equal(client.calls.length, 0, 'createIssue never called');
  assert.deepEqual(result, { created: [] });
});

test('createTicketingDelivery: unknown minLevel throws with informative message', () => {
  assert.throws(
    () => createTicketingDelivery({ client: { createIssue: async () => {} }, minLevel: 'bogus' }),
    (err) => {
      assert.ok(err instanceof Error, 'should throw an Error');
      assert.ok(err.message.includes('bogus'), 'message should include the bad value');
      assert.ok(err.message.includes('Critical'), 'message should list valid levels');
      return true;
    }
  );
});

test('createTicketingDelivery: default minLevel:Critical still works (no throw)', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client });  // minLevel defaults to 'Critical'
  const result = await delivery.open([CRITICAL_1]);
  assert.equal(result.created.length, 1);
});

test('createTicketingDelivery: findings with no key are ticketed but not tracked in created', async () => {
  const client = makeFakeClient();
  const delivery = createTicketingDelivery({ client });
  const noKeyFinding = { what: 'Keyless issue', score: { level: 'Critical' }, fix: [] };
  const result = await delivery.open([noKeyFinding]);

  assert.equal(client.calls.length, 1, 'issue created');
  assert.deepEqual(result.created, [], 'no key -> not in created array');
});

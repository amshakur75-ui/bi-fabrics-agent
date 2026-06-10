import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTeamsDelivery } from './delivery.teams.js';

const WEBHOOK = 'https://example.webhook.office.com/webhookb2/fake';

/** Fake HTTP client: captures the last (url, body) pair, no network. */
function makeFakeHttp() {
  let captured = null;
  return {
    async postJson(url, body) {
      captured = { url, body };
    },
    get captured() { return captured; },
  };
}

const makeEnvelope = () => ({
  summary: '9 findings detected',
  data: {
    verdict: { decision: 'optimize', reason: 'CU contention' },
    findings: [
      { what: 'CU peaked 93%', fix: ['Stagger refreshes'], score: { level: 'Critical' } },
      { what: 'Slow visual 12000ms', fix: ['Optimize DAX'], score: { level: 'Critical' } },
      { what: 'Auto Date/Time', fix: ['Disable it'], score: { level: 'Warning' } },
    ],
  },
});

test('createTeamsDelivery posts to the webhookUrl with a body containing sections', async () => {
  const http = makeFakeHttp();
  const delivery = createTeamsDelivery({ http, webhookUrl: WEBHOOK });
  const result = await delivery.deliver(makeEnvelope());

  assert.ok(http.captured, 'http.postJson was called');
  assert.equal(http.captured.url, WEBHOOK, 'posts to the correct webhookUrl');
  assert.ok(Array.isArray(http.captured.body.sections), 'body has sections array');
  assert.ok(http.captured.body.sections.length >= 2, 'at least summary + critical findings sections');
});

test('createTeamsDelivery returns { delivered: true, target, sections }', async () => {
  const http = makeFakeHttp();
  const delivery = createTeamsDelivery({ http, webhookUrl: WEBHOOK });
  const result = await delivery.deliver(makeEnvelope());

  assert.equal(result.delivered, true);
  assert.equal(result.target, WEBHOOK);
  assert.equal(typeof result.sections, 'number');
  assert.ok(result.sections >= 2, 'sections count reflects card sections');
});

test('createTeamsDelivery envelope with verdict produces a verdict section in the posted body', async () => {
  const http = makeFakeHttp();
  const delivery = createTeamsDelivery({ http, webhookUrl: WEBHOOK });
  await delivery.deliver(makeEnvelope());

  const verdictSection = http.captured.body.sections.find(s => s.heading === 'Capacity verdict');
  assert.ok(verdictSection, 'verdict section present in posted body');
  assert.ok(verdictSection.text.startsWith('OPTIMIZE'), 'decision is upper-cased');
});

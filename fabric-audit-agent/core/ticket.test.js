import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTicket } from './ticket.js';

const criticalFinding = {
  key: 'capacity.cu::prod-capacity',
  what: 'CU peaked 93%',
  where: 'prod-capacity / 2026-06-09',
  why: 'Concurrent refreshes saturating CU',
  impact: 'Report timeouts for all users',
  fix: ['Stagger refreshes', 'Increase capacity'],
  score: { level: 'Critical', reason: 'CU > 90% for 3+ hours' },
};

test('buildTicket: title starts with [Critical] for a Critical finding', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.title.startsWith('[Critical]'), `title was: ${ticket.title}`);
});

test('buildTicket: title contains the finding what field', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.title.includes('CU peaked 93%'), `title was: ${ticket.title}`);
});

test('buildTicket: body contains Where section', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.body.includes('Where:'), 'body missing Where:');
  assert.ok(ticket.body.includes('prod-capacity'), 'body missing where value');
});

test('buildTicket: body contains Why section', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.body.includes('Why:'), 'body missing Why:');
  assert.ok(ticket.body.includes('Concurrent refreshes'), 'body missing why value');
});

test('buildTicket: body contains Impact section', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.body.includes('Impact:'), 'body missing Impact:');
  assert.ok(ticket.body.includes('Report timeouts'), 'body missing impact value');
});

test('buildTicket: body contains Fix section with bullets', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.body.includes('Fix:'), 'body missing Fix:');
  assert.ok(ticket.body.includes('- Stagger refreshes'), 'body missing fix bullet 1');
  assert.ok(ticket.body.includes('- Increase capacity'), 'body missing fix bullet 2');
});

test('buildTicket: labels include fabric-audit', () => {
  const ticket = buildTicket(criticalFinding);
  assert.ok(ticket.labels.includes('fabric-audit'), `labels: ${ticket.labels}`);
});

test('buildTicket: labels include the finding domain', () => {
  const ticket = buildTicket(criticalFinding);
  // key = 'capacity.cu::prod-capacity' -> domainOf -> 'capacity'
  assert.ok(ticket.labels.includes('capacity'), `labels: ${ticket.labels}`);
});

test('buildTicket: externalKey matches the finding key', () => {
  const ticket = buildTicket(criticalFinding);
  assert.equal(ticket.externalKey, criticalFinding.key);
});

test('buildTicket: severity reflects the finding level', () => {
  const ticket = buildTicket(criticalFinding);
  assert.equal(ticket.severity, 'Critical');
});

test('buildTicket: defaults gracefully when finding fields are missing', () => {
  const ticket = buildTicket({});
  assert.equal(ticket.title, '[Info] Fabric audit finding');
  assert.equal(ticket.severity, 'Info');
  assert.equal(ticket.externalKey, undefined);
  assert.ok(ticket.labels.includes('fabric-audit'));
});

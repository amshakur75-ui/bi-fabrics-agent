import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTeamsCard } from './teams-card.js';

const makeEnvelope = ({ verdict = null, findings = [] } = {}) => ({
  summary: '12 findings across the estate',
  data: {
    verdict,
    findings,
  },
});

const makeFinding = (level, what, fix) => ({
  what,
  fix: fix ? [fix] : ['see report'],
  score: { level },
});

test('buildTeamsCard includes Summary, Capacity verdict, and Critical findings sections', () => {
  const envelope = makeEnvelope({
    verdict: { decision: 'optimize', reason: 'contention + bidirectional bloat' },
    findings: [
      makeFinding('Critical', 'CU peaked 93%', 'Stagger refreshes'),
      makeFinding('Critical', '5 models refresh at 02:00', 'Offset schedules'),
      makeFinding('Warning', 'Auto Date/Time on Sales model', 'Disable auto date/time'),
    ],
  });

  const card = buildTeamsCard(envelope);

  assert.equal(card.type, 'message');
  assert.equal(card.summary, envelope.summary);

  const summarySection = card.sections.find(s => s.heading === 'Summary');
  assert.ok(summarySection, 'Summary section present');
  assert.equal(summarySection.text, envelope.summary);

  const verdictSection = card.sections.find(s => s.heading === 'Capacity verdict');
  assert.ok(verdictSection, 'Capacity verdict section present');
  assert.ok(verdictSection.text.startsWith('OPTIMIZE'), 'verdict decision is upper-cased');
  assert.ok(verdictSection.text.includes('contention + bidirectional bloat'), 'includes verdict reason');

  const criticalSection = card.sections.find(s => s.heading === 'Critical findings (2)');
  assert.ok(criticalSection, 'Critical findings section with correct count');
  assert.equal(criticalSection.items.length, 2, 'only critical findings listed');
  assert.ok(criticalSection.items[0].includes('CU peaked 93%'), 'first critical finding present');
  assert.ok(criticalSection.items[0].includes('Stagger refreshes'), 'fix text present');
});

test('buildTeamsCard with no verdict omits verdict section and does not throw', () => {
  const envelope = makeEnvelope({
    verdict: null,
    findings: [makeFinding('Warning', 'Slow visual', 'Optimize DAX')],
  });

  const card = buildTeamsCard(envelope);

  const verdictSection = card.sections.find(s => s.heading === 'Capacity verdict');
  assert.equal(verdictSection, undefined, 'no verdict section when verdict is absent');

  const summarySection = card.sections.find(s => s.heading === 'Summary');
  assert.ok(summarySection, 'Summary section still present');

  const criticalSection = card.sections.find(s => s.heading === 'Critical findings (0)');
  assert.ok(criticalSection, 'Critical findings (0) section present');
  assert.equal(criticalSection.items.length, 0);
});

test('buildTeamsCard caps critical findings at 10', () => {
  const findings = Array.from({ length: 15 }, (_, i) =>
    makeFinding('Critical', `Issue ${i}`, 'Fix it'),
  );
  const envelope = makeEnvelope({ findings });
  const card = buildTeamsCard(envelope);

  const criticalSection = card.sections.find(s => s.heading === 'Critical findings (15)');
  assert.ok(criticalSection, 'heading shows real count');
  assert.equal(criticalSection.items.length, 10, 'items capped at 10');
});

test('buildTeamsCard handles missing envelope gracefully', () => {
  const card = buildTeamsCard(null);
  assert.equal(card.type, 'message');
  assert.equal(card.summary, 'Fabric audit');
  assert.ok(Array.isArray(card.sections));
});

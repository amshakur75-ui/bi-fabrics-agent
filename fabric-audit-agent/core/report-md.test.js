import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildMarkdownReport } from './report-md.js';

// Full envelope matching the estate fixture shape
const fullEnvelope = {
  summary: '17 findings (9 critical, 7 warning)',
  data: {
    tenant: 'Contoso',
    narrative: 'The estate has capacity pressure and refresh chain issues.',
    healthScore: {
      overall: 6,
      byDomain: {
        capacity: 10,
        model: 20,
        report: 50,
        pipeline: 30,
        lineage: 60,
        security: 40,
        cost: 55,
      },
    },
    verdict: {
      decision: 'optimize',
      reason: 'CU contention + bidirectional bloat detected',
    },
    roadmap: [
      { rank: 1, level: 'Critical', what: 'CU peaked at 93%', fix: 'Stagger refreshes' },
      { rank: 2, level: 'Critical', what: 'Bidirectional relationships', fix: 'Remodel to single-direction' },
      { rank: 3, level: 'Warning', what: 'Auto Date/Time enabled', fix: 'Disable auto date/time' },
    ],
    findings: Array.from({ length: 17 }, (_, i) => ({
      key: i < 9 ? `capacity.finding${i}::item` : `model.finding${i}::item`,
      what: i < 9 ? `Critical issue ${i}` : `Warning issue ${i}`,
      where: `Dataset ${i}`,
      why: `Reason ${i}`,
      impact: `Impact ${i}`,
      fix: [`Fix step ${i}`, `Fix step ${i}b`],
      score: { level: i < 9 ? 'Critical' : 'Warning' },
    })),
    correlations: [
      { theme: 'capacity-pressure', findingKeys: ['capacity.finding0::item'], narrative: 'Multiple capacity findings cluster around refresh windows.' },
      { theme: 'refresh-chain', findingKeys: ['model.finding9::item'], narrative: 'Failing refreshes propagate downstream.' },
      { theme: 'security-cluster', findingKeys: ['model.finding10::item'], narrative: 'Three security issues share the same workspace.' },
    ],
  },
};

test('buildMarkdownReport full envelope contains all required sections', () => {
  const md = buildMarkdownReport(fullEnvelope);

  assert.ok(md.includes('# Fabric Audit Report'), 'must contain title');
  assert.ok(md.includes('## Health: 6/100'), 'must contain health score header');
  assert.ok(md.includes('| capacity | 10 |'), 'must contain capacity domain row');
  assert.ok(md.includes('## Capacity verdict: OPTIMIZE'), 'must contain verdict header upper-cased');
  assert.ok(md.includes('1. **[Critical]**'), 'must contain numbered roadmap entry with level');
  assert.ok(md.includes('## Findings (17)'), 'must contain findings count');
  assert.ok(md.includes('### [Critical]'), 'must contain a Critical finding header');
  assert.ok(md.includes('- **Fix:**'), 'must contain a Fix line');
  assert.ok(md.includes('## Correlations'), 'must contain correlations section');
});

test('buildMarkdownReport empty envelope does not throw and starts with title', () => {
  const md = buildMarkdownReport({});

  assert.ok(typeof md === 'string', 'must return a string');
  assert.ok(md.startsWith('# Fabric Audit Report'), 'must start with title');
  assert.ok(md.includes('## Findings (0)'), 'must contain Findings (0)');
});

test('buildMarkdownReport finding with missing fix produces empty Fix line without throwing', () => {
  const envelope = {
    data: {
      findings: [
        {
          what: 'Some issue',
          where: 'Somewhere',
          why: 'Some reason',
          impact: 'Some impact',
          // fix intentionally omitted
          score: { level: 'Warning' },
        },
      ],
    },
  };

  const md = buildMarkdownReport(envelope);
  assert.ok(md.includes('- **Fix:** '), 'must render empty Fix line without throwing');
});

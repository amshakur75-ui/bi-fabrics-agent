import { buildMarkdownReport } from './core/report-md.js';
import { buildTeamsCard } from './core/teams-card.js';

// Full fixture from report-md.test.js
const fullEnvelope = {
  summary: '17 findings (9 critical, 7 warning)',
  data: {
    tenant: 'Contoso',
    narrative: 'The estate has capacity pressure and refresh chain issues.',
    healthScore: { overall: 6, byDomain: { capacity: 10, model: 20, report: 50, pipeline: 30, lineage: 60, security: 40, cost: 55 } },
    verdict: { decision: 'optimize', reason: 'CU contention + bidirectional bloat detected' },
    roadmap: [
      { rank: 1, level: 'Critical', what: 'CU peaked at 93%', fix: 'Stagger refreshes' },
      { rank: 2, level: 'Critical', what: 'Bidirectional relationships', fix: 'Remodel to single-direction' },
      { rank: 3, level: 'Warning', what: 'Auto Date/Time enabled', fix: 'Disable auto date/time' },
    ],
    findings: Array.from({ length: 3 }, (_, i) => ({
      key: `capacity.finding${i}::item`, what: `Issue ${i}`, where: `Dataset ${i}`,
      why: `Reason ${i}`, impact: `Impact ${i}`, fix: [`Fix step ${i}`, `Fix step ${i}b`],
      score: { level: i < 2 ? 'Critical' : 'Warning' },
    })),
    correlations: [
      { theme: 'capacity-pressure', findingKeys: ['x'], narrative: 'Multiple capacity findings cluster.' },
    ],
  },
};
console.log('FULL_MD\n' + buildMarkdownReport(fullEnvelope));
console.log('---HASH---', JSON.stringify(buildMarkdownReport(fullEnvelope)).length);

// teams: decision missing entirely (no decision key) -> String(undefined) = 'undefined'
console.log('TC_NODEC', JSON.stringify(buildTeamsCard({ data: { verdict: { reason: 'r' }, findings: [] } })));
// teams: verdict.reason = '' empty -> `... — ` (empty kept)
console.log('TC_EMPTYREASON', JSON.stringify(buildTeamsCard({ data: { verdict: { decision: 'optimize', reason: '' }, findings: [] } })));
// teams finding what missing -> `undefined — Fix:`
console.log('TC_NOWHAT', JSON.stringify(buildTeamsCard({ data: { findings: [{ fix: ['f'], score: { level: 'Critical' } }] } })));

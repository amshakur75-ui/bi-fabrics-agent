import { scoreSeverity } from '../core/severity.js';
import { getRemediation } from '../core/kb/index.js';
import { createFinding } from '../core/finding.js';
import { sanitize } from '../core/sanitize.js';
import { DEFAULT_CONFIG } from '../core/config.js';

const DEFAULT_MODEL = 'claude-sonnet-4-6';

const SYSTEM_TEXT = [
  'You are a Microsoft Fabric / Power BI performance expert.',
  'You receive a JSON array of detected issues; each has an id, a type, and sanitized numeric evidence (no names).',
  'For EACH issue return an object {"id","why","impact","fix"}: why = one-sentence root cause; impact = one sentence; fix = array of 2-4 concrete remediation steps.',
  'Respond with ONLY a JSON array. No prose, no markdown fences.',
].join(' ');

// Prompt caching: pass system as an array of content blocks with cache_control.
// The static system block is stable across all requests — this maximises cache hits.
// Per the claude-api skill: cache_control on the system block caches tools+system together;
// minimum cacheable prefix is ~1024 tokens (Sonnet 4.6 threshold). The system block here
// is ~60 tokens — caching is declared so it will be cached if the model decides to, and
// adding it costs nothing if it falls below the threshold.
const SYSTEM = [
  {
    type: 'text',
    text: SYSTEM_TEXT,
    cache_control: { type: 'ephemeral' },
  },
];

function extractJsonArray(text) {
  const s = text.indexOf('['); const e = text.lastIndexOf(']');
  return (s >= 0 && e >= s) ? text.slice(s, e + 1) : '[]';
}

/**
 * @param {{ client: {messages:{create:Function}}, model?: string, config?: object, maxFlags?: number }} deps
 * @returns {{ reason: (facts:object, flags:object[]) => Promise<object[]> }}
 */
export function createClaudeReasoner({ client, model = DEFAULT_MODEL, config = DEFAULT_CONFIG, maxFlags = 50 }) {
  return {
    async reason(facts, flags) {
      if (!flags.length) return [];
      const sanitized = sanitize(flags.slice(0, maxFlags));

      let enriched = [];
      try {
        const resp = await client.messages.create({
          model,
          max_tokens: 1024,
          system: SYSTEM,
          messages: [{ role: 'user', content: JSON.stringify(sanitized) }],
        });
        const text = resp?.content?.[0]?.text ?? '[]';
        enriched = JSON.parse(extractJsonArray(text));
      } catch (_err) {
        // Network error, API error, or JSON parse failure — fall back to KB below.
        enriched = [];
      }

      const byId = new Map(enriched.map(e => [e.id, e]));
      return flags.map((flag, i) => {
        const e = byId.get(i) ?? {};
        const kb = getRemediation(flag.type);
        const finding = createFinding({
          what: flag.what,
          where: flag.resource,
          when: flag.when,
          why: e.why ?? kb.rootCause,
          impact: e.impact ?? 'Impact not assessed.',
          fix: (Array.isArray(e.fix) && e.fix.length) ? e.fix : kb.fixes,
          score: scoreSeverity(flag, config),
        });
        finding.key = `${flag.type}::${flag.resource}`;
        if (e.why) finding.reasonedBy = 'claude';
        return finding;
      });
    },
  };
}

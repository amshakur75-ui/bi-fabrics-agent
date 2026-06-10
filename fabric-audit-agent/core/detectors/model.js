import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Semantic-model design + health detectors. Pure: facts in, flags out.
 * @param {{models?:object[]}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectModels(facts, config = DEFAULT_CONFIG) {
  const models = facts?.models ?? [];
  const flags = [];
  for (const m of models) {
    const where = `${m.workspace} / ${m.name}`;
    if ((m.bidirectionalRels ?? 0) >= config.model.bidirectionalMin) {
      flags.push({
        type: 'model.bidirectional', resource: where, when: m.observedAt ?? '',
        evidence: { count: m.bidirectionalRels },
        what: `Model "${m.name}" has ${m.bidirectionalRels} bidirectional relationships.`,
      });
    }
    if (m.autoDateTime === true) {
      flags.push({
        type: 'model.auto-datetime', resource: where, when: m.observedAt ?? '',
        evidence: {},
        what: `Model "${m.name}" has Auto Date/Time enabled.`,
      });
    }
    if ((m.refreshFailRatePct ?? 0) >= config.model.refreshFailPct) {
      flags.push({
        type: 'model.refresh-failing', resource: where, when: m.observedAt ?? '',
        evidence: { failRatePct: m.refreshFailRatePct },
        what: `Model "${m.name}" refresh fail rate is ${m.refreshFailRatePct}%.`,
      });
    }
  }
  return flags;
}

import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Refresh/pipeline health detectors. Pure: facts in, flags out.
 * @param {{pipelines?:object[]}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectPipelines(facts, config = DEFAULT_CONFIG) {
  const pipelines = facts?.pipelines ?? [];
  const flags = [];
  for (const p of pipelines) {
    const where = `${p.workspace} / ${p.name}`;
    if (p.lastStatus === 'Failed' || (p.failRatePct ?? 0) >= config.pipeline.failRatePct) {
      flags.push({
        type: 'pipeline.failing', resource: where, when: p.lastRunAt ?? '',
        evidence: { status: p.lastStatus, failRatePct: p.failRatePct ?? 0 },
        what: `Pipeline "${p.name}" last status ${p.lastStatus} (fail rate ${p.failRatePct ?? 0}%).`,
      });
    }
    if (p.gatewayHealthy === false) {
      flags.push({
        type: 'pipeline.gateway', resource: where, when: p.lastRunAt ?? '',
        evidence: {},
        what: `Pipeline "${p.name}" depends on an unhealthy gateway.`,
      });
    }
  }
  return flags;
}

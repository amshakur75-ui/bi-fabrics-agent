import { createFinding } from '../core/finding.js';
import { scoreSeverity } from '../core/severity.js';
import { getRemediation } from '../core/kb/index.js';
import { DEFAULT_CONFIG } from '../core/config.js';

function impactFor(flag) {
  switch (flag.type) {
    case 'capacity.throttle':
      return 'Reports and datasets on this capacity slow down or queue during the peak window.';
    case 'capacity.contention':
      return `Downstream reports for ${flag.evidence.datasets.join(', ')} show stale data until refreshes drain.`;
    case 'capacity.oversized-model':
      return 'Long refreshes consume capacity memory and CU, compounding contention.';
    case 'capacity.concentration':
      return 'One item monopolizing CU can slow or throttle every other workload on the same capacity.';
    case 'model.bidirectional':
    case 'model.auto-datetime':
      return 'Slower queries and a larger model that consumes more capacity memory.';
    case 'model.refresh-failing':
      return 'Reports on this model show stale data when refreshes fail.';
    case 'report.too-many-visuals':
    case 'report.slow-visual':
      return 'Slow page loads for every user who opens this report.';
    case 'report.directquery':
      return 'Every interaction round-trips to the source, adding load and latency.';
    case 'pipeline.failing':
    case 'pipeline.gateway':
      return 'Downstream datasets and reports go stale until the pipeline recovers.';
    case 'lineage.blast-radius':
      return 'Every downstream dataset and report shows stale or missing data until the root item is fixed.';
    case 'security.admin-grant':
    case 'security.external-share':
    case 'security.unusual-access':
      return 'Potential data exposure or compliance risk until reviewed.';
    case 'cost.unused-report':
      return 'Wasted storage/refresh load; safe to clean up.';
    case 'cost.idle-capacity':
      return 'Ongoing spend with little utilization.';
    case 'meta.detector-error':
      return 'This check could not run; its findings are missing from this audit.';
    default:
      return 'Impact not assessed.';
  }
}

/**
 * Deterministic ReasonerPort (no LLM) for build/test. The real Claude reasoner
 * (later increment) implements the same `reason(facts, flags)` signature and enriches
 * the why/impact/fix prose.
 * @param {{ config?: object }} [opts]
 * @returns {{reason: (facts:object, flags:object[]) => Promise<import('../core/finding.js').Finding[]>}}
 */
export function createStubReasoner({ config = DEFAULT_CONFIG } = {}) {
  return {
    async reason(facts, flags) {
      return flags.map(flag => {
        const kb = getRemediation(flag.type);
        const finding = createFinding({
          what: flag.what,
          where: flag.resource,
          when: flag.when,
          why: kb.rootCause,
          impact: impactFor(flag),
          fix: kb.fixes,
          score: scoreSeverity(flag, config),
        });
        finding.key = `${flag.type}::${flag.resource}`;
        return finding;
      });
    },
  };
}

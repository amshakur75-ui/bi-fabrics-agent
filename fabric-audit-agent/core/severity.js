import { DEFAULT_CONFIG } from './config.js';

/**
 * Map a detector flag to a severity level + reason.
 * @param {{type:string, evidence:object}} flag
 * @param {object} [config]
 * @returns {{level:'Critical'|'Warning'|'Info', reason:string}}
 */
export function scoreSeverity(flag, config = DEFAULT_CONFIG) {
  const e = flag.evidence || {};
  switch (flag.type) {
    case 'capacity.throttle':
      if (e.peakCuPct >= config.capacity.throttleCritPct && e.throttleMinutes > config.capacity.throttleCritMinutes) {
        return { level: 'Critical', reason: `CU peaked ${e.peakCuPct}% with ${e.throttleMinutes} min throttled` };
      }
      return { level: 'Warning', reason: `CU peaked ${e.peakCuPct}%` };

    case 'capacity.contention': {
      const n = e.datasets?.length ?? 0;
      if (n >= config.capacity.contentionCritCount) return { level: 'Critical', reason: `${n} models refresh at ${e.time}` };
      return { level: 'Warning', reason: `${n} models refresh at ${e.time}` };
    }

    case 'capacity.oversized-model':
      if (e.sizeGB >= (config.capacity.oversizedCritPct / 100) * e.memoryGB) {
        return { level: 'Critical', reason: `model ${e.sizeGB}GB vs ${e.memoryGB}GB capacity` };
      }
      return { level: 'Warning', reason: `model ${e.sizeGB}GB on ${e.memoryGB}GB capacity` };

    case 'capacity.concentration':
      if (e.sharePct >= config.capacity.concentrationCritPct) return { level: 'Critical', reason: `${e.sharePct}% of capacity CU in one item` };
      return { level: 'Warning', reason: `${e.sharePct}% of capacity CU in one item` };

    case 'model.bidirectional':
      if (e.count >= config.model.bidirectionalCritMin) return { level: 'Critical', reason: `${e.count} bidirectional relationships` };
      return { level: 'Warning', reason: `${e.count} bidirectional relationships` };

    case 'model.auto-datetime':
      return { level: 'Warning', reason: 'Auto Date/Time inflates model size' };

    case 'model.refresh-failing':
      if (e.failRatePct >= config.model.refreshFailCritPct) return { level: 'Critical', reason: `${e.failRatePct}% refresh failures` };
      return { level: 'Warning', reason: `${e.failRatePct}% refresh failures` };

    case 'report.too-many-visuals':
      if (e.visuals >= config.report.visualsCritMin) return { level: 'Critical', reason: `${e.visuals} visuals on one page` };
      return { level: 'Warning', reason: `${e.visuals} visuals on one page` };

    case 'report.directquery':
      return { level: 'Warning', reason: 'DirectQuery adds per-interaction query load' };

    case 'report.slow-visual':
      if (e.ms >= config.report.slowVisualCritMs) return { level: 'Critical', reason: `visual renders in ${e.ms} ms` };
      return { level: 'Warning', reason: `visual renders in ${e.ms} ms` };

    case 'pipeline.failing':
      if (e.status === 'Failed') return { level: 'Critical', reason: 'last run failed' };
      return { level: 'Warning', reason: `${e.failRatePct}% failure rate` };

    case 'pipeline.gateway':
      return { level: 'Critical', reason: 'gateway unhealthy — refreshes will fail' };

    case 'lineage.blast-radius':
      if (e.affectedCount >= 1) return { level: 'Critical', reason: `${e.affectedCount} downstream assets impacted` };
      return { level: 'Warning', reason: 'isolated failure, no downstream impact' };

    case 'security.admin-grant':
      return { level: 'Critical', reason: 'admin role on a sensitive workspace' };
    case 'security.external-share':
      return { level: 'Warning', reason: 'item shared outside the org' };
    case 'security.unusual-access':
      if (e.ratio >= config.security.unusualCritRatio) return { level: 'Critical', reason: `${e.ratio}x normal access rate` };
      return { level: 'Warning', reason: `${e.ratio}x normal access rate` };
    case 'cost.unused-report':
      return { level: 'Info', reason: '0 views in 30 days' };
    case 'cost.idle-capacity':
      return { level: 'Warning', reason: `${e.avgCuPct}% average CU` };

    case 'meta.detector-error':
      return { level: 'Warning', reason: 'a detector failed and was skipped' };

    default:
      return { level: 'Info', reason: 'unclassified' };
  }
}

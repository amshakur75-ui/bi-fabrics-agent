import { DEFAULT_CONFIG } from '../config.js';

/** @typedef {import('./capacity.js').Flag} Flag */

/**
 * Report-performance detectors. Pure: facts in, flags out.
 * @param {{reports?:object[]}} facts
 * @param {object} [config]
 * @returns {Flag[]}
 */
export function detectReports(facts, config = DEFAULT_CONFIG) {
  const reports = facts?.reports ?? [];
  const flags = [];
  for (const r of reports) {
    const where = `${r.workspace} / ${r.name}`;
    if ((r.visuals ?? 0) >= config.report.visualsMin) {
      flags.push({
        type: 'report.too-many-visuals', resource: where, when: '',
        evidence: { visuals: r.visuals },
        what: `Report "${r.name}" has ${r.visuals} visuals on its busiest page.`,
      });
    }
    if (r.mode === 'DirectQuery') {
      flags.push({
        type: 'report.directquery', resource: where, when: '',
        evidence: { source: r.source ?? 'unknown' },
        what: `Report "${r.name}" uses DirectQuery against ${r.source ?? 'an unknown source'}.`,
      });
    }
    if ((r.slowestVisualMs ?? 0) >= config.report.slowVisualMs) {
      flags.push({
        type: 'report.slow-visual', resource: where, when: '',
        evidence: { ms: r.slowestVisualMs },
        what: `Report "${r.name}" has a visual rendering in ${r.slowestVisualMs} ms.`,
      });
    }
  }
  return flags;
}

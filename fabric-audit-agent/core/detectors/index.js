import { DEFAULT_CONFIG } from '../config.js';
import { detectCapacity } from './capacity.js';
import { detectModels } from './model.js';
import { detectReports } from './report.js';
import { detectPipelines } from './pipeline.js';
import { detectBlastRadius } from './blast-radius.js';
import { detectSecurity } from './security.js';
import { detectCost } from './cost.js';

const DETECTORS = [detectCapacity, detectModels, detectReports, detectPipelines, detectBlastRadius, detectSecurity, detectCost];

/**
 * Run every registered detector and flatten the flags.
 * A failing detector emits a `meta.detector-error` flag instead of crashing the run.
 * @param {object} facts
 * @param {object} [config]
 * @param {Function[]} [detectors]  override for testability; defaults to all registered detectors
 * @returns {import('./capacity.js').Flag[]}
 */
export function detectAll(facts, config = DEFAULT_CONFIG, detectors = DETECTORS) {
  const flags = [];
  for (const fn of detectors) {
    try {
      flags.push(...fn(facts, config));
    } catch (err) {
      const name = fn.name || 'unknown-detector';
      flags.push({
        type: 'meta.detector-error',
        resource: name,
        when: '',
        evidence: { detector: name, message: String(err?.message ?? err) },
        what: `Detector "${name}" failed and was skipped: ${err?.message ?? err}`,
      });
    }
  }
  return flags;
}

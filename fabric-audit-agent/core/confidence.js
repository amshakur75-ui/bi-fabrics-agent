/**
 * Confidence for a finding. Deterministic detections = high; meta/errors = low;
 * Claude-enriched (marked `reasonedBy:'claude'`) = medium. Pure.
 * @param {object} finding
 * @returns {'high'|'medium'|'low'}
 */
export function scoreConfidence(finding = {}) {
  const type = typeof finding.key === 'string' ? finding.key.split('::')[0] : '';
  if (type.startsWith('meta.')) return 'low';
  if (finding.reasonedBy === 'claude') return 'medium';
  return 'high';
}

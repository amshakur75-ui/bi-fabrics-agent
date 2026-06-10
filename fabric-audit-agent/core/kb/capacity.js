/** Capacity remediation playbooks: anti-pattern -> root cause -> fixes -> owner. */
export const CAPACITY_PLAYBOOKS = {
  'capacity.throttle': {
    rootCause: 'CU demand exceeds the capacity SKU during peak windows, forcing throttling.',
    fixes: [
      'Identify the top CU-consuming items during the peak window.',
      'Stagger heavy refreshes out of the peak window.',
      'If demand is structural after optimization, size up the capacity SKU.',
    ],
    owner: 'Power BI team',
  },
  'capacity.contention': {
    rootCause: 'Multiple large models refresh at the same time, queuing on one capacity.',
    fixes: [
      'Stagger refresh start times across the hour.',
      'Move non-critical refreshes off the peak window.',
      'Enable incremental refresh to shrink each refresh job.',
    ],
    owner: 'Power BI team',
  },
  'capacity.oversized-model': {
    rootCause: 'Semantic model footprint is large relative to capacity memory.',
    fixes: [
      'Enable incremental refresh.',
      'Add aggregations for high-grain tables.',
      'Remove unused columns and disable auto date/time.',
      'Reduce high-cardinality columns.',
    ],
    owner: 'Report author + Power BI team',
  },
};

/**
 * @param {string} flagType
 * @returns {{rootCause:string, fixes:string[], owner:string}}
 */
export function getRemediation(flagType) {
  return CAPACITY_PLAYBOOKS[flagType] ?? {
    rootCause: 'Pattern not yet in the knowledge base.',
    fixes: ['Investigate manually and add a playbook entry.'],
    owner: 'Power BI team',
  };
}

export const LINEAGE_PLAYBOOKS = {
  'lineage.blast-radius': {
    rootCause: 'A single upstream failure cascades to every downstream dataset and report.',
    fixes: [
      'Fix the root item first — downstream assets recover once it succeeds.',
      'Add a retry policy and a failure alert on the root item.',
      'Review the dependency chain and remove unnecessary coupling.',
    ],
    owner: 'Power BI team',
  },
};

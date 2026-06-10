export const PIPELINE_PLAYBOOKS = {
  'pipeline.failing': {
    rootCause: 'The pipeline is failing, blocking every downstream dataset and report.',
    fixes: [
      'Open the failed run and read the activity-level error.',
      'Fix the root activity (source, auth, or transform) and re-run.',
      'Add a retry policy and a failure alert.',
    ],
    owner: 'Power BI team',
  },
  'pipeline.gateway': {
    rootCause: 'An on-premises data gateway is unhealthy, so refreshes through it fail.',
    fixes: [
      'Check the gateway service status and cluster health.',
      'Restart or fail over to a healthy gateway node.',
      'Add gateway monitoring so this is caught proactively.',
    ],
    owner: 'Power BI team',
  },
};

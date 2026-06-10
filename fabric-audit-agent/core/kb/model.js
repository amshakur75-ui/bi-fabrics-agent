export const MODEL_PLAYBOOKS = {
  'model.bidirectional': {
    rootCause: 'Bidirectional relationships force expensive cross-filtering at query time.',
    fixes: [
      'Replace bidirectional relationships with single-direction where possible.',
      'Use measures with CROSSFILTER() only where genuinely needed.',
      'Flatten the model to a star schema.',
    ],
    owner: 'Report author + Power BI team',
  },
  'model.auto-datetime': {
    rootCause: 'Auto Date/Time builds a hidden date table per date column, inflating the model.',
    fixes: [
      'Disable Auto Date/Time (Options > Data Load).',
      'Use a single shared, marked Date dimension.',
    ],
    owner: 'Report author',
  },
  'model.refresh-failing': {
    rootCause: 'Refreshes are failing intermittently — source, credentials, or timeout.',
    fixes: [
      'Check the gateway/source credentials and connectivity.',
      'Review refresh logs for the failing table.',
      'Enable incremental refresh to shorten and de-risk refreshes.',
    ],
    owner: 'Power BI team',
  },
};

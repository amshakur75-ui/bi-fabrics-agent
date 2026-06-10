/** Author-facing, plain-English tips. null = not author-actionable (infra/team owns it). */
const TIPS = {
  'model.bidirectional': 'Set relationships to single-direction unless you genuinely need two-way filtering — it speeds up every query.',
  'model.auto-datetime': 'Turn off Auto Date/Time (Options > Data Load) and use one shared Date table.',
  'model.refresh-failing': 'Check your data source credentials; if refreshes keep failing, flag the Power BI team.',
  'report.too-many-visuals': 'Keep pages under ~20 visuals — split busy pages or use drill-through.',
  'report.directquery': 'Use Import mode when your data size allows; it is far faster for readers.',
  'report.slow-visual': 'Run Performance Analyzer, find the slow visual, and simplify its DAX measure.',
  'cost.unused-report': 'If this report is yours and no longer used, archive or delete it to cut refresh load.',
};

/** @param {string} flagType @returns {string|null} */
export function getUserTip(flagType) {
  return TIPS[flagType] ?? null;
}

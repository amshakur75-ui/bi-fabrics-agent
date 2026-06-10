// Heuristic text rules — may have false positives; intended as hints, not verdicts.
const RULES = [
  { test: /FILTER\s*\(\s*'?[A-Za-z0-9_ ]+'?\s*,/i, pattern: 'filter-whole-table',
    suggestion: 'FILTER() over an entire table is slow — filter a single column (KEEPFILTERS or a boolean column filter) instead.' },
  { test: /(SUMX|AVERAGEX|MAXX|MINX|COUNTX)[\s\S]*?(SUMX|AVERAGEX|MAXX|MINX|COUNTX)/i, pattern: 'nested-iterators',
    suggestion: 'Possible nested iterators (X-functions inside X-functions). If nested, they evaluate row-by-row quadratically — precompute with a VAR or an aggregation table.' },
  { test: /\bCALCULATE\b[\s\S]*\bCALCULATE\b/i, pattern: 'repeated-calculate',
    suggestion: 'Repeated CALCULATE recomputes filter context — hoist shared expressions into VARs.' },
  { test: /\bEARLIER\s*\(/i, pattern: 'earlier',
    suggestion: 'EARLIER() signals legacy row-context nesting — refactor with VARs.' },
  { test: /(?<![/:])[^/]\/[^/]/, pattern: 'raw-division',
    suggestion: 'Use DIVIDE(numerator, denominator) instead of "/" for safe, efficient division.' },
];

/**
 * Analyze a DAX measure for performance anti-patterns. Pure (rule-based text analysis).
 * @param {string} measureText  the DAX expression
 * @param {{ durationMs?:number }} [stats]
 * @returns {Array<{ pattern:string, suggestion:string }>}
 *
 * At transfer: replace the measureText argument with a live fetch via the Power BI remote MCP
 * (powerbi-remote: getModelSchema + executeDax) for a named report/measure, and pass the
 * actual execution durationMs from the query result into stats.
 */
export function analyzeDax(measureText = '', stats = {}) {
  const text = String(measureText);
  const suggestions = RULES.filter(r => r.test.test(text)).map(({ pattern, suggestion }) => ({ pattern, suggestion }));
  if ((stats.durationMs ?? 0) >= 5000 && suggestions.length === 0) {
    suggestions.push({
      pattern: 'slow-no-obvious-cause',
      suggestion: `Measure runs in ${stats.durationMs} ms with no obvious anti-pattern — profile with Performance Analyzer / DAX Studio and check the storage-engine vs formula-engine split.`,
    });
  }
  return suggestions;
}

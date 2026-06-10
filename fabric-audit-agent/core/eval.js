const round2 = (x) => Math.round(x * 100) / 100;
const typesOf = (findings) => new Set(findings.map(f => (typeof f.key === 'string' ? f.key.split('::')[0] : null)).filter(Boolean));

/**
 * Score one case's findings against expected finding types. Pure.
 * @param {object[]} actualFindings
 * @param {{ types?: string[] }} expected
 * @returns {{ matched:number, missing:string[], extra:string[], recall:number, precision:number, pass:boolean }}
 */
export function scoreCase(actualFindings = [], expected = {}) {
  const found = typesOf(actualFindings);
  const want = new Set(expected.types ?? []);
  const matched = [...want].filter(t => found.has(t));
  const missing = [...want].filter(t => !found.has(t));
  const extra = [...found].filter(t => !want.has(t));
  const recall = want.size ? matched.length / want.size : 1;
  const precision = found.size ? matched.length / found.size : 1;
  return { matched: matched.length, missing, extra, recall: round2(recall), precision: round2(precision), pass: missing.length === 0 };
}

/**
 * Aggregate per-case scores into a suite scorecard. Pure.
 * @param {Array<{ name:string, score:object }>} results
 */
export function scoreSuite(results = []) {
  const passed = results.filter(r => r.score.pass).length;
  const avg = (sel) => results.length ? round2(results.reduce((s, r) => s + sel(r.score), 0) / results.length) : 1;
  return { cases: results.length, passed, failed: results.length - passed, avgRecall: avg(s => s.recall), avgPrecision: avg(s => s.precision) };
}

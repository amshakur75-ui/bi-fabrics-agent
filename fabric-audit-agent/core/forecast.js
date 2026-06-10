/** Least-squares slope of y over index 0..n-1. Pure. */
function slopeOf(ys) {
  const n = ys.length;
  if (n < 2) return 0;
  const mx = (n - 1) / 2;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) { num += (i - mx) * (ys[i] - my); den += (i - mx) ** 2; }
  return den === 0 ? 0 : num / den;
}

/**
 * Forecast peak-CU trend from the store metric series. Pure.
 * @param {Array<{metrics?:{peakCuPct?:number}}>} history  chronological run records (include the current run last)
 * @param {{ ceiling?:number, minPoints?:number }} [opts]
 * @returns {{ trend:string, points:number, current?:number, slopePerRun?:number, runsToCeiling?:number|null, message?:string }}
 */
export function forecastCapacity(history = [], { ceiling = 100, minPoints = 3 } = {}) {
  const series = history.map(h => h?.metrics?.peakCuPct).filter(v => typeof v === 'number');
  if (series.length < minPoints) return { trend: 'insufficient-data', points: series.length };

  const slope = slopeOf(series);
  const current = series[series.length - 1];
  const trend = slope > 0.5 ? 'rising' : slope < -0.5 ? 'falling' : 'flat';
  const runsToCeiling = (slope > 0 && current < ceiling) ? Math.ceil((ceiling - current) / slope) : null;
  const slopePerRun = Math.round(slope * 10) / 10;
  const message = runsToCeiling != null
    ? `At current trend (+${slopePerRun}%/run), peak CU reaches ${ceiling}% in ~${runsToCeiling} run(s).`
    : `Peak CU trend is ${trend}; no ceiling breach projected.`;
  return { trend, points: series.length, current, slopePerRun, runsToCeiling, message };
}

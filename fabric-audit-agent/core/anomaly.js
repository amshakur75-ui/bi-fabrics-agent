function stats(ys) {
  const n = ys.length;
  const mean = ys.reduce((a, b) => a + b, 0) / n;
  const variance = ys.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
  return { mean, stddev: Math.sqrt(variance) };
}

/**
 * Flag metrics that deviate > z standard deviations from their historical baseline. Pure.
 * @param {object} facts  current facts (reads facts.capacity.peakCuPct)
 * @param {Array<{metrics?:{peakCuPct?:number}}>} history  chronological run records
 * @param {{ z?:number, minPoints?:number }} [opts]
 * @returns {Array<{metric:string, resource:string, current:number, mean:number, stddev:number, sigma:number, direction:string, message:string}>}
 */
export function detectAnomalies(facts = {}, history = [], { z = 2, minPoints = 4 } = {}) {
  const anomalies = [];
  const series = history.map(h => h?.metrics?.peakCuPct).filter(v => typeof v === 'number');
  const current = facts?.capacity?.peakCuPct;

  if (series.length >= minPoints && typeof current === 'number') {
    const { mean, stddev } = stats(series);
    if (stddev > 0 && Math.abs(current - mean) > z * stddev) {
      const sigma = Math.round(((current - mean) / stddev) * 10) / 10;
      anomalies.push({
        metric: 'peakCuPct',
        resource: `capacity ${facts.capacity?.capacityId ?? ''}`.trim(),
        current,
        mean: Math.round(mean * 10) / 10,
        stddev: Math.round(stddev * 10) / 10,
        sigma,
        direction: current > mean ? 'above' : 'below',
        message: `Peak CU ${current}% is anomalous vs baseline (mean ${Math.round(mean)}%, ${Math.abs(sigma)}σ ${current > mean ? 'above' : 'below'}).`,
      });
    }
  }
  return anomalies;
}

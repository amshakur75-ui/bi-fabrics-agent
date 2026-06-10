import { pathToFileURL } from 'node:url';
import { analyzeDax } from './core/dax.js';

// node dax.js "Sales := CALCULATE(SUM(F[Amt]), FILTER(F, F[Y]=2026))"
// At transfer: replace the argv measure with a live fetch via the Power BI MCP
// (powerbi-remote: getModelSchema + executeDax) for a named report/measure.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const measure = process.argv.slice(2).join(' ');
  const suggestions = analyzeDax(measure);
  if (!suggestions.length) console.log('No obvious DAX anti-patterns detected.');
  for (const s of suggestions) console.log(`[${s.pattern}] ${s.suggestion}`);
}

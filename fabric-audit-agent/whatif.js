import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { createMockCollector } from './adapters/collector.mock.js';
import { assessWhatIf } from './core/whatif.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Run a what-if against the current (mock) estate. */
export async function whatIf(proposed) {
  const facts = await createMockCollector(join(__dirname, 'fixtures', 'estate.json')).collect();
  return assessWhatIf(facts, proposed);
}

// node whatif.js <kind> <sizeGB> <refreshAt>   e.g. node whatif.js model 5 06:00
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [kind, sizeGB, refreshAt] = process.argv.slice(2);
  const res = await whatIf({ kind, sizeGB: Number(sizeGB) || 0, refreshAt });
  console.log(`What-if verdict: ${res.verdict.toUpperCase()} (risk ${res.riskScore})`);
  for (const i of res.impacts) console.log(`  - ${i}`);
}

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { createMockCollector } from './adapters/collector.mock.js';
import { evaluateThresholdTriggers } from './core/triggers.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Evaluate immediate triggers against the current (mock) estate. */
export async function checkTriggers() {
  const facts = await createMockCollector(join(__dirname, 'fixtures', 'estate.json')).collect();
  return evaluateThresholdTriggers(facts);
}

// At transfer: a cron host calls shouldRunScheduled() on a timer, and a live event source
// (capacity/activity webhooks) calls evaluateThresholdTriggers() to fire immediate audits.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const events = await checkTriggers();
  if (!events.length) console.log('No immediate triggers.');
  for (const e of events) console.log(`[${e.severity}] ${e.reason}`);
}

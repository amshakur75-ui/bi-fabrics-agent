import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { createLifecycleStore } from './adapters/lifecycle.store.js';
import { setState } from './core/lifecycle.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STORE = join(__dirname, 'runs', 'lifecycle.json');
const VALID = new Set(['open', 'acknowledged', 'snoozed', 'resolved', 'wontfix']);

/** Set a finding's lifecycle state. Returns the new record. */
export async function manage(action, key, opts = {}) {
  if (!VALID.has(action)) throw new Error(`Unknown action "${action}" (use: ${[...VALID].join(', ')})`);
  if (!key) throw new Error('A finding key is required.');
  if (action === 'snoozed' && !opts.snoozeUntil) throw new Error('snoozed requires snoozeUntil (an ISO date)');
  const store = createLifecycleStore(STORE);
  const next = setState(await store.load(), key, action, opts);
  await store.save(next);
  return next[key];
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [action, key, ...rest] = process.argv.slice(2);
  let snoozeUntil;
  let note;
  if (action === 'snoozed') {
    snoozeUntil = rest.shift();
    note = rest.join(' ') || undefined;
  } else {
    note = rest.join(' ') || undefined;
  }
  const res = await manage(action, key, { note, snoozeUntil, now: new Date().toISOString() });
  console.log(`Set ${key} -> ${res.state}`);
}

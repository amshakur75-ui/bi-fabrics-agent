import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';

/** Persists the lifecycle states map (key -> state record). Prod-DB at transfer. */
export function createLifecycleStore(filePath) {
  return {
    async load() {
      try { return JSON.parse(await readFile(filePath, 'utf-8')); }
      catch (err) { if (err.code === 'ENOENT') return {}; throw err; }
    },
    async save(states) {
      await mkdir(dirname(filePath), { recursive: true });
      await writeFile(filePath, JSON.stringify(states, null, 2), 'utf-8');
      return states;
    },
  };
}

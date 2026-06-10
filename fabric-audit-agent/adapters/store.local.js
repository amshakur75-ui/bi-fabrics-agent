import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';

/**
 * Local-JSON StorePort: persists run history. The prod store (transfer time)
 * implements the same { history, append } against a real DB.
 * @param {string} filePath
 * @param {{ keep?: number }} [options]
 */
export function createLocalStore(filePath, { keep = 180 } = {}) {
  return {
    /** @returns {Promise<Array<{runAt:string, findings:object[]}>>} */
    async history() {
      try {
        return JSON.parse(await readFile(filePath, 'utf-8'));
      } catch (err) {
        if (err.code === 'ENOENT') return [];
        throw err;
      }
    },
    /** @param {{runAt:string, findings:object[]}} run */
    async append(run) {
      const all = await this.history();
      all.push(run);
      const trimmed = all.slice(-keep);
      await mkdir(dirname(filePath), { recursive: true });
      await writeFile(filePath, JSON.stringify(trimmed, null, 2), 'utf-8');
      return trimmed.length;
    },
  };
}

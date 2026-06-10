import { writeFile, mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';

/**
 * File DeliveryPort: writes the envelope as pretty JSON. The real delivery
 * (transfer time) posts to Teams / opens tickets, implementing the same `deliver`.
 * @param {string} outPath
 * @returns {{deliver: (envelope:object) => Promise<string>}}
 */
export function createFileDelivery(outPath) {
  return {
    async deliver(envelope) {
      await mkdir(dirname(outPath), { recursive: true });
      await writeFile(outPath, JSON.stringify(envelope, null, 2), 'utf-8');
      return outPath;
    },
  };
}

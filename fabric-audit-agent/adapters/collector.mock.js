import { readFile } from 'node:fs/promises';

/**
 * Mock CollectorPort: reads facts from a fixture file. The real collector
 * (transfer time) calls Fabric/Power BI/Azure APIs and emits the same fact shape.
 * @param {string} fixturePath
 * @returns {{collect: () => Promise<object>}}
 */
export function createMockCollector(fixturePath) {
  return {
    async collect() {
      return JSON.parse(await readFile(fixturePath, 'utf-8'));
    },
  };
}

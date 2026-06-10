import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createMockCollector } from './collector.mock.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fixture = join(__dirname, '..', 'fixtures', 'capacity.throttle.json');

test('mock collector loads facts from the fixture', async () => {
  const facts = await createMockCollector(fixture).collect();
  assert.equal(facts.capacity.capacityId, 'F64');
  assert.equal(facts.capacity.refreshes.length, 4);
});

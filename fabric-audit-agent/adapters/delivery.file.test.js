import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createFileDelivery } from './delivery.file.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const out = join(__dirname, '..', 'runs', 'test-delivery.json');

test('writes the envelope as pretty JSON and returns the path', async () => {
  await rm(out, { force: true });
  const env = { success: true, agent_id: 'fabric-audit-agent', data: { findings: [] }, summary: 's', timestamp: 't' };
  const written = await createFileDelivery(out).deliver(env);
  assert.equal(written, out);
  const onDisk = JSON.parse(await readFile(out, 'utf-8'));
  assert.equal(onDisk.agent_id, 'fabric-audit-agent');
  await rm(out, { force: true });
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createToolDefinitions } from './tools.js';

test('exposes a read-only run_audit tool with correct shape', () => {
  const t = createToolDefinitions().find(x => x.name === 'run_audit');
  assert.ok(t, 'run_audit tool present');
  assert.match(t.description, /read-only/i);
  assert.equal(t.input_schema.type, 'object');
  assert.equal(typeof t._handler, 'function');
});

test('run_audit handler runs the pipeline and returns findings + verdict + digest', async () => {
  const t = createToolDefinitions().find(x => x.name === 'run_audit');
  const res = await t._handler({});
  assert.ok(res.findings.length >= 9, 'returns findings across domains');
  assert.ok(res.verdict && typeof res.verdict.decision === 'string', 'includes a verdict');
  assert.ok(res.digest && res.digest.totals, 'includes a digest');
  assert.match(res.summary, /findings/);
});

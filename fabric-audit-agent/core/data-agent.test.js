import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildDataAgentManifest } from './data-agent.js';
import { createToolDefinitions } from '../tools.js';

test('buildDataAgentManifest returns correct name and readOnly flag', () => {
  const manifest = buildDataAgentManifest(createToolDefinitions());
  assert.equal(manifest.name, 'fabric-audit-agent');
  assert.equal(manifest.readOnly, true);
});

test('buildDataAgentManifest manifest.tools contains run_audit with input_schema', () => {
  const manifest = buildDataAgentManifest(createToolDefinitions());
  const tool = manifest.tools.find(t => t.name === 'run_audit');
  assert.ok(tool, 'run_audit present in manifest.tools');
  assert.ok(tool.input_schema, 'run_audit has input_schema');
  assert.equal(tool.input_schema.type, 'object');
  assert.match(tool.description, /read-only/i);
});

test('buildDataAgentManifest strips _handler — no handler leaks into manifest tools', () => {
  const manifest = buildDataAgentManifest(createToolDefinitions());
  for (const tool of manifest.tools) {
    assert.equal(tool._handler, undefined, `_handler leaked into tool "${tool.name}"`);
  }
});

test('buildDataAgentManifest with empty toolDefinitions returns empty tools array', () => {
  const manifest = buildDataAgentManifest([]);
  assert.deepEqual(manifest.tools, []);
  assert.equal(manifest.name, 'fabric-audit-agent');
});

test('buildDataAgentManifest includes displayName and description', () => {
  const manifest = buildDataAgentManifest(createToolDefinitions());
  assert.ok(manifest.displayName, 'displayName present');
  assert.ok(manifest.description, 'description present');
  assert.ok(manifest.instructions, 'instructions present');
});

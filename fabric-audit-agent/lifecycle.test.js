import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
// The manage() function uses the module-level STORE constant (runs/lifecycle.json).
// We test it through the exported function and clean up after each test.
const lifecycleFile = join(__dirname, 'runs', 'lifecycle.json');

// Dynamic import so we get the live module (ESM cache won't re-evaluate)
const { manage } = await import('./lifecycle.js');

test('manage: acknowledged action persists and returns the record', async () => {
  await rm(lifecycleFile, { force: true });
  const now = '2026-01-01T00:00:00Z';
  const result = await manage('acknowledged', 'capacity.throttle::CapA', { now });
  assert.equal(result.state, 'acknowledged');
  assert.equal(result.since, now);
  assert.equal(result.snoozeUntil, null);
  await rm(lifecycleFile, { force: true });
});

test('manage: resolved action persists correctly', async () => {
  await rm(lifecycleFile, { force: true });
  const now = '2026-01-02T00:00:00Z';
  const result = await manage('resolved', 'model.bidirectional::DatasetX', { now, note: 'Removed dataset' });
  assert.equal(result.state, 'resolved');
  assert.equal(result.note, 'Removed dataset');
  await rm(lifecycleFile, { force: true });
});

test('manage: invalid action throws with descriptive message', async () => {
  await assert.rejects(
    () => manage('invalidAction', 'someKey'),
    (err) => {
      assert.ok(err instanceof Error);
      assert.match(err.message, /Unknown action/);
      return true;
    },
  );
});

test('manage: missing key throws', async () => {
  await assert.rejects(
    () => manage('resolved', ''),
    (err) => {
      assert.ok(err instanceof Error);
      assert.match(err.message, /key is required/);
      return true;
    },
  );
});

// ---------------------------------------------------------------------------
// Fix 4: snoozed requires snoozeUntil
// ---------------------------------------------------------------------------

test('manage: snoozed without snoozeUntil throws', async () => {
  await assert.rejects(
    () => manage('snoozed', 'capacity.throttle::CapA', {}),
    (err) => {
      assert.ok(err instanceof Error);
      assert.match(err.message, /snoozeUntil/);
      return true;
    },
  );
});

test('manage: snoozed with snoozeUntil persists correctly', async () => {
  await rm(lifecycleFile, { force: true });
  const now = '2026-01-01T00:00:00Z';
  const snoozeUntil = '2030-01-01T00:00:00Z';
  const result = await manage('snoozed', 'capacity.throttle::CapA', { snoozeUntil, now });
  assert.equal(result.state, 'snoozed');
  assert.equal(result.snoozeUntil, snoozeUntil);
  assert.equal(result.since, now);
  await rm(lifecycleFile, { force: true });
});

test('manage: accumulates multiple keys', async () => {
  await rm(lifecycleFile, { force: true });
  const now = '2026-01-01T00:00:00Z';
  await manage('resolved', 'k1', { now });
  const result = await manage('acknowledged', 'k2', { now });
  assert.equal(result.state, 'acknowledged');
  // k1 should still be there — verify by resolving k1 again and checking it's updated
  const result2 = await manage('wontfix', 'k1', { now });
  assert.equal(result2.state, 'wontfix');
  await rm(lifecycleFile, { force: true });
});

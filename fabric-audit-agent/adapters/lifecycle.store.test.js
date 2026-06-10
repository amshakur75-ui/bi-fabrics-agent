import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createLifecycleStore } from './lifecycle.store.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const testFile = join(__dirname, '..', 'runs', 'test-lifecycle-store.json');

test('load() returns {} when file does not exist', async () => {
  await rm(testFile, { force: true });
  const store = createLifecycleStore(testFile);
  const result = await store.load();
  assert.deepEqual(result, {});
});

test('save() then load() round-trips a states map', async () => {
  await rm(testFile, { force: true });
  const store = createLifecycleStore(testFile);
  const states = {
    'capacity.throttle::CapA': { state: 'resolved', since: '2026-01-01T00:00:00Z', snoozeUntil: null, note: 'Fixed it' },
    'model.bidirectional::DatasetX': { state: 'acknowledged', since: '2026-01-02T00:00:00Z', snoozeUntil: null, note: null },
  };
  await store.save(states);
  const loaded = await store.load();
  assert.deepEqual(loaded, states);
  await rm(testFile, { force: true });
});

test('save() overwrites previous data', async () => {
  await rm(testFile, { force: true });
  const store = createLifecycleStore(testFile);
  await store.save({ 'k1': { state: 'open', since: null, snoozeUntil: null, note: null } });
  await store.save({ 'k2': { state: 'resolved', since: '2026-01-01T00:00:00Z', snoozeUntil: null, note: null } });
  const loaded = await store.load();
  assert.ok(!('k1' in loaded), 'k1 should have been overwritten');
  assert.ok('k2' in loaded);
  await rm(testFile, { force: true });
});

test('save() creates the runs directory if it does not exist', async () => {
  const tmpFile = join(__dirname, '..', 'runs', 'subdir-lifecycle-test', 'lifecycle.json');
  await rm(join(__dirname, '..', 'runs', 'subdir-lifecycle-test'), { recursive: true, force: true });
  const store = createLifecycleStore(tmpFile);
  await store.save({ 'k1': { state: 'open', since: null, snoozeUntil: null, note: null } });
  const loaded = await store.load();
  assert.ok('k1' in loaded);
  await rm(join(__dirname, '..', 'runs', 'subdir-lifecycle-test'), { recursive: true, force: true });
});

test('save() returns the saved states', async () => {
  await rm(testFile, { force: true });
  const store = createLifecycleStore(testFile);
  const states = { 'k': { state: 'wontfix', since: null, snoozeUntil: null, note: null } };
  const returned = await store.save(states);
  assert.deepEqual(returned, states);
  await rm(testFile, { force: true });
});

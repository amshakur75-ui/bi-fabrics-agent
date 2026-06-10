import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createLocalStore } from './store.local.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const testFile = join(__dirname, '..', 'runs', 'test-store-history.json');

const fakeRun = (n) => ({
  runAt: `2026-01-0${n}T00:00:00Z`,
  findings: [{ key: `capacity.throttle::Cap${n}`, level: 'Critical' }],
});

test('history() returns [] when file does not exist', async () => {
  await rm(testFile, { force: true });
  const store = createLocalStore(testFile);
  const h = await store.history();
  assert.deepEqual(h, []);
});

test('append() then history() round-trips a run', async () => {
  await rm(testFile, { force: true });
  const store = createLocalStore(testFile);
  const run = fakeRun(1);
  await store.append(run);
  const h = await store.history();
  assert.equal(h.length, 1);
  assert.equal(h[0].runAt, run.runAt);
  assert.equal(h[0].findings[0].key, 'capacity.throttle::Cap1');
  await rm(testFile, { force: true });
});

test('second append() accumulates (does not overwrite)', async () => {
  await rm(testFile, { force: true });
  const store = createLocalStore(testFile);
  await store.append(fakeRun(1));
  await store.append(fakeRun(2));
  const h = await store.history();
  assert.equal(h.length, 2);
  assert.equal(h[0].runAt, '2026-01-01T00:00:00Z');
  assert.equal(h[1].runAt, '2026-01-02T00:00:00Z');
  await rm(testFile, { force: true });
});

test('append() returns the new total count', async () => {
  await rm(testFile, { force: true });
  const store = createLocalStore(testFile);
  const count1 = await store.append(fakeRun(1));
  assert.equal(count1, 1);
  const count2 = await store.append(fakeRun(2));
  assert.equal(count2, 2);
  await rm(testFile, { force: true });
});

test('append() creates the runs directory if it does not exist', async () => {
  // Use a fresh sub-path that likely won't exist
  const tmpFile = join(__dirname, '..', 'runs', 'subdir-test', 'test-store.json');
  await rm(join(__dirname, '..', 'runs', 'subdir-test'), { recursive: true, force: true });
  const store = createLocalStore(tmpFile);
  await store.append(fakeRun(1));
  const h = await store.history();
  assert.equal(h.length, 1);
  await rm(join(__dirname, '..', 'runs', 'subdir-test'), { recursive: true, force: true });
});

test('keep option caps history length and drops oldest run', async () => {
  const tmpFile = join(__dirname, '..', 'runs', 'test-keep-cap.json');
  await rm(tmpFile, { force: true });
  const store = createLocalStore(tmpFile, { keep: 3 });
  await store.append(fakeRun(1));
  await store.append(fakeRun(2));
  await store.append(fakeRun(3));
  await store.append(fakeRun(4));
  const h = await store.history();
  assert.equal(h.length, 3, 'history should be capped at 3');
  assert.ok(!h.some(r => r.runAt === '2026-01-01T00:00:00Z'), 'oldest run should have been dropped');
  assert.equal(h[0].runAt, '2026-01-02T00:00:00Z', 'first kept run is run 2');
  assert.equal(h[2].runAt, '2026-01-04T00:00:00Z', 'last run is run 4');
  await rm(tmpFile, { force: true });
});

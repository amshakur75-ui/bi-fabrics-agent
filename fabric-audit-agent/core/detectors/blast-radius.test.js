import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectBlastRadius } from './blast-radius.js';

// --- helper facts builders ---

const chainFacts = {
  lineage: {
    nodes: [
      { id: 'pl-nightly', type: 'pipeline', workspace: 'Finance', name: 'Nightly Load', status: 'Failed', failedAt: '2026-06-08T02:14:00.000Z' },
      { id: 'ds-sales',   type: 'dataset',  workspace: 'Finance', name: 'Sales',         status: 'OK' },
      { id: 'rpt-exec',   type: 'report',   workspace: 'Finance', name: 'Exec Dashboard', status: 'OK' },
    ],
    edges: [
      { from: 'pl-nightly', to: 'ds-sales' },
      { from: 'ds-sales',   to: 'rpt-exec' },
    ],
  },
};

// --- tests ---

test('chain pipeline(Failed)→dataset→report yields exactly 1 flag', () => {
  const flags = detectBlastRadius(chainFacts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].type, 'lineage.blast-radius');
});

test('evidence.affectedCount === 2 and affected names are Sales + Exec Dashboard (BFS order)', () => {
  const flag = detectBlastRadius(chainFacts)[0];
  assert.equal(flag.evidence.affectedCount, 2);
  assert.deepEqual(flag.evidence.affected, ['Sales', 'Exec Dashboard']);
});

test('evidence.root and rootType carry the failed pipeline info', () => {
  const flag = detectBlastRadius(chainFacts)[0];
  assert.equal(flag.evidence.root, 'Nightly Load');
  assert.equal(flag.evidence.rootType, 'pipeline');
});

test('resource is workspace / name of the root cause node', () => {
  const flag = detectBlastRadius(chainFacts)[0];
  assert.equal(flag.resource, 'Finance / Nightly Load');
});

test('a Failed node that has a Failed upstream is NOT reported as its own root', () => {
  const facts = {
    lineage: {
      nodes: [
        { id: 'a', type: 'pipeline', workspace: 'W', name: 'Root',   status: 'Failed', failedAt: '2026-06-08T01:00:00Z' },
        { id: 'b', type: 'dataset',  workspace: 'W', name: 'Middle', status: 'Failed', failedAt: '2026-06-08T01:01:00Z' },
        { id: 'c', type: 'report',   workspace: 'W', name: 'Leaf',   status: 'OK' },
      ],
      edges: [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'c' },
      ],
    },
  };
  const flags = detectBlastRadius(facts);
  // Only 'a' is a root cause (b has a failed upstream)
  assert.equal(flags.length, 1);
  assert.equal(flags[0].evidence.root, 'Root');
});

test('isolated Failed node (no edges) yields 1 flag with affectedCount === 0', () => {
  const facts = {
    lineage: {
      nodes: [
        { id: 'x', type: 'pipeline', workspace: 'W', name: 'Orphan', status: 'Failed', failedAt: '' },
      ],
      edges: [],
    },
  };
  const flags = detectBlastRadius(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].evidence.affectedCount, 0);
  assert.deepEqual(flags[0].evidence.affected, []);
});

test('detectBlastRadius({}) returns []', () => {
  assert.deepEqual(detectBlastRadius({}), []);
});

test('lineage with no failures returns []', () => {
  const facts = {
    lineage: {
      nodes: [
        { id: 'a', type: 'pipeline', workspace: 'W', name: 'A', status: 'OK' },
        { id: 'b', type: 'dataset',  workspace: 'W', name: 'B', status: 'OK' },
      ],
      edges: [{ from: 'a', to: 'b' }],
    },
  };
  assert.deepEqual(detectBlastRadius(facts), []);
});

test('cycle (a→b→a) does not infinite-loop and reports root correctly', () => {
  const facts = {
    lineage: {
      nodes: [
        { id: 'a', type: 'pipeline', workspace: 'W', name: 'A', status: 'Failed', failedAt: '' },
        { id: 'b', type: 'dataset',  workspace: 'W', name: 'B', status: 'OK' },
      ],
      edges: [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'a' },  // creates a cycle
      ],
    },
  };
  // Must not hang; should return exactly 1 finding
  const flags = detectBlastRadius(facts);
  assert.equal(flags.length, 1);
  assert.equal(flags[0].evidence.root, 'A');
});

test('cycle root A is never listed as an affected asset in its own finding', () => {
  const facts = {
    lineage: {
      nodes: [
        { id: 'a', type: 'pipeline', workspace: 'W', name: 'A', status: 'Failed', failedAt: '' },
        { id: 'b', type: 'dataset',  workspace: 'W', name: 'B', status: 'OK' },
      ],
      edges: [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'a' },  // cycle back to root
      ],
    },
  };
  const flags = detectBlastRadius(facts);
  assert.equal(flags.length, 1);
  assert.ok(!flags[0].evidence.affected.includes('A'), 'root A must not appear in its own affected list');
});

test('edges: null does not throw and returns 1 flag with affectedCount === 0', () => {
  const flags = detectBlastRadius({
    lineage: {
      nodes: [{ id: 'a', type: 'pipeline', workspace: 'W', name: 'A', status: 'Failed' }],
      edges: null,
    },
  });
  assert.equal(flags.length, 1);
  assert.equal(flags[0].evidence.affectedCount, 0);
});

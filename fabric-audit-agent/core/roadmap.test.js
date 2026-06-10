import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRoadmap } from './roadmap.js';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
function makeF(key, level, recurringRuns = 1, fix = []) {
  return {
    key,
    score: { level },
    what: `what for ${key}`,
    where: 'somewhere',
    fix,
    recurringRuns,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test('buildRoadmap: Criticals rank before Warnings before Info', () => {
  const findings = [
    makeF('model.oversized::M1', 'Info'),
    makeF('capacity.contention::C2', 'Warning'),
    makeF('capacity.throttle::C1', 'Critical'),
  ];
  const roadmap = buildRoadmap(findings);
  assert.equal(roadmap[0].level, 'Critical');
  assert.equal(roadmap[1].level, 'Warning');
  assert.equal(roadmap[2].level, 'Info');
});

test('buildRoadmap: within same level, higher recurringRuns ranks first', () => {
  const findings = [
    makeF('capacity.contention::C1', 'Critical', 1),
    makeF('capacity.throttle::C2', 'Critical', 5),
    makeF('capacity.peak::C3', 'Critical', 3),
  ];
  const roadmap = buildRoadmap(findings);
  assert.equal(roadmap[0].key, 'capacity.throttle::C2');  // recurringRuns=5
  assert.equal(roadmap[1].key, 'capacity.peak::C3');       // recurringRuns=3
  assert.equal(roadmap[2].key, 'capacity.contention::C1'); // recurringRuns=1
});

test('buildRoadmap: rank is 1-based and sequential', () => {
  const findings = [
    makeF('a.x::1', 'Critical'),
    makeF('b.y::2', 'Warning'),
    makeF('c.z::3', 'Info'),
  ];
  const roadmap = buildRoadmap(findings);
  assert.equal(roadmap[0].rank, 1);
  assert.equal(roadmap[1].rank, 2);
  assert.equal(roadmap[2].rank, 3);
});

test('buildRoadmap: fix is first element of finding.fix array or null', () => {
  const findings = [
    makeF('a.x::1', 'Critical', 1, ['Fix this first', 'Also try this']),
    makeF('b.y::2', 'Warning', 1, []),
    makeF('c.z::3', 'Info', 1, undefined),
  ];
  const roadmap = buildRoadmap(findings);
  assert.equal(roadmap[0].fix, 'Fix this first');
  assert.equal(roadmap[1].fix, null);
  assert.equal(roadmap[2].fix, null);
});

test('buildRoadmap: empty findings → []', () => {
  assert.deepEqual(buildRoadmap([]), []);
});

test('buildRoadmap: no-arg call → []', () => {
  assert.deepEqual(buildRoadmap(), []);
});

test('buildRoadmap: returned entries have required shape', () => {
  const findings = [makeF('capacity.throttle::C1', 'Critical', 2, ['Reduce refresh load'])];
  const roadmap = buildRoadmap(findings);
  const entry = roadmap[0];
  assert.equal(entry.rank, 1);
  assert.equal(entry.key, 'capacity.throttle::C1');
  assert.equal(entry.level, 'Critical');
  assert.equal(entry.what, 'what for capacity.throttle::C1');
  assert.equal(entry.fix, 'Reduce refresh load');
  assert.equal(entry.recurringRuns, 2);
});

test('buildRoadmap: does not mutate the original array', () => {
  const f1 = makeF('a.x::1', 'Warning');
  const f2 = makeF('b.y::2', 'Critical');
  const original = [f1, f2];
  buildRoadmap(original);
  assert.equal(original[0].key, 'a.x::1', 'original array order must not change');
});

test('buildRoadmap: missing score.level falls back to Info rank', () => {
  const findings = [
    makeF('capacity.throttle::C1', 'Critical'),
    { key: 'unknown::X', score: {}, what: 'no level', where: 'x', fix: [] },
  ];
  const roadmap = buildRoadmap(findings);
  // Critical should rank before unknown (which has no level → rank 9 > Info's 2)
  assert.equal(roadmap[0].level, 'Critical');
  assert.equal(roadmap[1].level, 'Info'); // fallback from score with no level
});

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { scoreCase, scoreSuite } from './eval.js';

// Helper: build a minimal finding with a key like 'type::resource'
const mkFinding = (type, resource = 'r') => ({ key: `${type}::${resource}` });

describe('scoreCase', () => {
  it('all expected types found → pass:true, recall:1, missing:[]', () => {
    const findings = [mkFinding('capacity.throttle'), mkFinding('capacity.contention')];
    const result = scoreCase(findings, { types: ['capacity.throttle', 'capacity.contention'] });
    assert.equal(result.pass, true);
    assert.equal(result.recall, 1);
    assert.deepEqual(result.missing, []);
    assert.equal(result.matched, 2);
  });

  it('expected type absent → pass:false, that type in missing, recall < 1', () => {
    const findings = [mkFinding('capacity.throttle')];
    const result = scoreCase(findings, { types: ['capacity.throttle', 'capacity.contention'] });
    assert.equal(result.pass, false);
    assert.ok(result.recall < 1);
    assert.ok(result.missing.includes('capacity.contention'));
  });

  it('extra found types → precision < 1 but pass still true if nothing missing', () => {
    const findings = [mkFinding('capacity.throttle'), mkFinding('security.admin-grant')];
    const result = scoreCase(findings, { types: ['capacity.throttle'] });
    assert.equal(result.pass, true);
    assert.ok(result.precision < 1);
    assert.ok(result.extra.includes('security.admin-grant'));
  });

  it('empty expected → recall:1, pass:true', () => {
    const findings = [];
    const result = scoreCase(findings, { types: [] });
    assert.equal(result.pass, true);
    assert.equal(result.recall, 1);
  });

  it('empty expected with actual findings → recall:1, pass:true, precision < 1', () => {
    const findings = [mkFinding('capacity.throttle')];
    const result = scoreCase(findings, { types: [] });
    assert.equal(result.pass, true);
    assert.equal(result.recall, 1);
    assert.ok(result.precision < 1);
  });

  it('no findings, no expected → recall:1, precision:1, pass:true', () => {
    const result = scoreCase([], {});
    assert.equal(result.pass, true);
    assert.equal(result.recall, 1);
    assert.equal(result.precision, 1);
  });

  it('deduplicates types from findings with same type prefix', () => {
    // two findings of the same type → counts as one distinct type
    const findings = [mkFinding('capacity.throttle', 'r1'), mkFinding('capacity.throttle', 'r2')];
    const result = scoreCase(findings, { types: ['capacity.throttle'] });
    assert.equal(result.pass, true);
    assert.equal(result.recall, 1);
  });
});

describe('scoreSuite', () => {
  it('aggregates passed/failed correctly over 3 results', () => {
    const results = [
      { name: 'a', score: { pass: true,  recall: 1,   precision: 1   } },
      { name: 'b', score: { pass: false, recall: 0.5, precision: 1   } },
      { name: 'c', score: { pass: true,  recall: 1,   precision: 0.5 } },
    ];
    const suite = scoreSuite(results);
    assert.equal(suite.cases, 3);
    assert.equal(suite.passed, 2);
    assert.equal(suite.failed, 1);
    assert.equal(suite.avgRecall, Math.round((1 + 0.5 + 1) / 3 * 100) / 100);
    assert.equal(suite.avgPrecision, Math.round((1 + 1 + 0.5) / 3 * 100) / 100);
  });

  it('all passing → failed:0', () => {
    const results = [
      { name: 'a', score: { pass: true, recall: 1, precision: 1 } },
      { name: 'b', score: { pass: true, recall: 1, precision: 1 } },
    ];
    const suite = scoreSuite(results);
    assert.equal(suite.failed, 0);
    assert.equal(suite.passed, 2);
    assert.equal(suite.avgRecall, 1);
    assert.equal(suite.avgPrecision, 1);
  });

  it('empty results → cases:0, passed:0, failed:0, averages:1', () => {
    const suite = scoreSuite([]);
    assert.equal(suite.cases, 0);
    assert.equal(suite.passed, 0);
    assert.equal(suite.failed, 0);
    assert.equal(suite.avgRecall, 1);
    assert.equal(suite.avgPrecision, 1);
  });
});

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { analyzeDax } from './dax.js';

describe('analyzeDax', () => {
  it('detects filter-whole-table', () => {
    const result = analyzeDax('CALCULATE(SUM(Sales[Amount]), FILTER(Sales, Sales[Year]=2026))');
    assert.ok(result.some(s => s.pattern === 'filter-whole-table'), 'should flag filter-whole-table');
  });

  it('detects nested-iterators', () => {
    const result = analyzeDax('SUMX(Sales, SUMX(RELATEDTABLE(Orders), Orders[Qty]))');
    assert.ok(result.some(s => s.pattern === 'nested-iterators'), 'should flag nested-iterators');
  });

  it('detects repeated-calculate', () => {
    const result = analyzeDax('CALCULATE(CALCULATE(SUM(Sales[Amt]), Year=2025), Region="West")');
    assert.ok(result.some(s => s.pattern === 'repeated-calculate'), 'should flag repeated-calculate');
  });

  it('detects raw-division on [A] / [B]', () => {
    const result = analyzeDax('[Revenue] / [Cost]');
    assert.ok(result.some(s => s.pattern === 'raw-division'), 'should flag raw-division');
  });

  it('does NOT flag DIVIDE([A],[B]) as raw-division', () => {
    const result = analyzeDax('DIVIDE([Revenue],[Cost])');
    assert.ok(!result.some(s => s.pattern === 'raw-division'), 'DIVIDE() should not be flagged');
  });

  it('still flags spaced division [A] / [B]', () => {
    const result = analyzeDax('[A] / [B]');
    assert.ok(result.some(s => s.pattern === 'raw-division'), '[A] / [B] should still flag raw-division');
  });

  it('does NOT flag raw-division for :// in a URL string', () => {
    const result = analyzeDax('VAR url = "http://x" RETURN SUM(Sales[Amount])');
    assert.ok(!result.some(s => s.pattern === 'raw-division'), '":// " adjacency should not trigger raw-division');
  });

  it('detects earlier', () => {
    const result = analyzeDax('CALCULATE(SUM(T[V]), EARLIER(T[Category]) = T[Category])');
    assert.ok(result.some(s => s.pattern === 'earlier'), 'should flag earlier');
  });

  it('returns empty array for a clean measure with no stats', () => {
    const result = analyzeDax('SUM(Sales[Amount])');
    assert.deepEqual(result, []);
  });

  it('adds slow-no-obvious-cause when durationMs >= 5000 and no other findings', () => {
    const result = analyzeDax('SUM(Sales[Amount])', { durationMs: 8000 });
    assert.equal(result.length, 1);
    assert.equal(result[0].pattern, 'slow-no-obvious-cause');
    assert.ok(result[0].suggestion.includes('8000 ms'), 'suggestion should include duration');
  });

  it('does NOT add slow-no-obvious-cause when there are other findings', () => {
    const result = analyzeDax('CALCULATE(SUM(Sales[Amount]), FILTER(Sales, Sales[Year]=2026))', { durationMs: 8000 });
    assert.ok(!result.some(s => s.pattern === 'slow-no-obvious-cause'), 'should not add slow-no-obvious-cause when other patterns matched');
  });

  it('does NOT add slow-no-obvious-cause when durationMs < 5000', () => {
    const result = analyzeDax('SUM(Sales[Amount])', { durationMs: 4999 });
    assert.deepEqual(result, []);
  });
});

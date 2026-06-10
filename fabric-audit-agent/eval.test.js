import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { runEval } from './eval.js';

describe('eval harness (golden cases)', () => {
  it('all bundled golden cases pass against current detectors (suite.failed === 0)', async () => {
    const { suite } = await runEval();
    assert.equal(suite.failed, 0, `Expected 0 failures but got ${suite.failed}`);
  });

  it('"healthy" case yields expected.types:[] with pass:true', async () => {
    const { results } = await runEval();
    const healthy = results.find(r => r.name === 'healthy');
    assert.ok(healthy, '"healthy" case not found in results');
    assert.equal(healthy.score.pass, true);
    assert.equal(healthy.score.recall, 1);
  });

  it('suite has 3 cases (one per labeled golden case)', async () => {
    const { suite } = await runEval();
    assert.ok(suite.cases >= 3, `Expected at least 3 cases, got ${suite.cases}`);
  });
});

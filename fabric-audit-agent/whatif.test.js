import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { whatIf } from './whatif.js';

describe('whatIf (CLI integration)', () => {
  it('resolves to blocked verdict for model 5 GB at 06:00 against bundled estate fixture', async () => {
    const result = await whatIf({ kind: 'model', sizeGB: 5, refreshAt: '06:00' });

    assert.equal(result.verdict, 'blocked');
    assert.ok(result.riskScore >= 4, `expected riskScore >= 4, got ${result.riskScore}`);
    assert.ok(Array.isArray(result.impacts) && result.impacts.length > 0, 'should have impacts');
  });
});

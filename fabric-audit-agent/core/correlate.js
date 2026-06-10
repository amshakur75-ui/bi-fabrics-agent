/**
 * Group related active findings into root-cause clusters with a narrative. Pure.
 * @param {object[]} findings  active findings (with .key)
 * @returns {Array<{theme:string, findingKeys:string[], narrative:string}>}
 */
export function correlate(findings = []) {
  const has = (prefix) => findings.filter(f => typeof f.key === 'string' && f.key.startsWith(prefix));
  const correlations = [];

  // 1. Capacity pressure: a throttle driven by contention / oversized models.
  const throttle = has('capacity.throttle');
  const drivers = [...has('capacity.contention'), ...has('capacity.oversized-model')];
  if (throttle.length && drivers.length) {
    correlations.push({
      theme: 'capacity-pressure',
      findingKeys: [...throttle, ...drivers].map(f => f.key),
      narrative: `Capacity throttling is likely driven by ${drivers.length} optimization issue(s) — resolve those before sizing up the SKU.`,
    });
  }

  // 2. Refresh chain: failures spanning models AND pipelines = shared upstream.
  const modelFail = has('model.refresh-failing');
  const pipeFail = has('pipeline.failing');
  if (modelFail.length && pipeFail.length) {
    correlations.push({
      theme: 'refresh-chain',
      findingKeys: [...modelFail, ...pipeFail].map(f => f.key),
      narrative: `Refresh failures span ${modelFail.length} model(s) and ${pipeFail.length} pipeline(s) — likely a shared gateway/source. Investigate the upstream together.`,
    });
  }

  // 3. Security cluster: multiple access/security findings = one access review.
  const sec = has('security.');
  if (sec.length >= 2) {
    correlations.push({
      theme: 'security-cluster',
      findingKeys: sec.map(f => f.key),
      narrative: `${sec.length} security/access findings detected together — handle as one access-review action.`,
    });
  }

  return correlations;
}

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { routeFindings, DEFAULT_ROUTES } from './routing.js';

// helpers
function f(key) { return { key }; }

// ---------------------------------------------------------------------------
// basic domain routing
// ---------------------------------------------------------------------------

test('routeFindings: security.admin-grant::X → security-team', () => {
  const result = routeFindings([f('security.admin-grant::X')]);
  assert.deepEqual(result['security-team'], ['security.admin-grant::X']);
});

test('routeFindings: cost.idle-capacity::Y → finops', () => {
  const result = routeFindings([f('cost.idle-capacity::Y')]);
  assert.deepEqual(result['finops'], ['cost.idle-capacity::Y']);
});

test('routeFindings: capacity.throttle::Z → powerbi-team', () => {
  const result = routeFindings([f('capacity.throttle::Z')]);
  assert.deepEqual(result['powerbi-team'], ['capacity.throttle::Z']);
});

test('routeFindings: report.* → powerbi-team', () => {
  const result = routeFindings([f('report.too-many-visuals::Finance/Exec')]);
  assert.deepEqual(result['powerbi-team'], ['report.too-many-visuals::Finance/Exec']);
});

test('routeFindings: pipeline.* → powerbi-team', () => {
  const result = routeFindings([f('pipeline.failing::Finance/Nightly')]);
  assert.deepEqual(result['powerbi-team'], ['pipeline.failing::Finance/Nightly']);
});

test('routeFindings: model.* → powerbi-team', () => {
  const result = routeFindings([f('model.bidirectional::DatasetX')]);
  assert.deepEqual(result['powerbi-team'], ['model.bidirectional::DatasetX']);
});

test('routeFindings: lineage.* → powerbi-team', () => {
  const result = routeFindings([f('lineage.broken-source::DS')]);
  assert.deepEqual(result['powerbi-team'], ['lineage.broken-source::DS']);
});

// ---------------------------------------------------------------------------
// unknown domain → unrouted
// ---------------------------------------------------------------------------

test('routeFindings: unknown domain key → unrouted', () => {
  const result = routeFindings([f('unknown.thing::ABC')]);
  assert.deepEqual(result['unrouted'], ['unknown.thing::ABC']);
  assert.equal(result['security-team'], undefined);
  assert.equal(result['finops'], undefined);
  assert.equal(result['powerbi-team'], undefined);
});

// ---------------------------------------------------------------------------
// accumulation — same destination
// ---------------------------------------------------------------------------

test('routeFindings: multiple findings of the same destination accumulate in the same array', () => {
  const findings = [
    f('security.admin-grant::WS1'),
    f('security.external-share::WS2'),
    f('security.unusual-access::WS3'),
  ];
  const result = routeFindings(findings);
  assert.ok(Array.isArray(result['security-team']), 'security-team should be an array');
  assert.equal(result['security-team'].length, 3);
  assert.ok(result['security-team'].includes('security.admin-grant::WS1'));
  assert.ok(result['security-team'].includes('security.external-share::WS2'));
  assert.ok(result['security-team'].includes('security.unusual-access::WS3'));
});

test('routeFindings: mixed destinations accumulate correctly', () => {
  const findings = [
    f('security.admin-grant::WS1'),
    f('cost.idle-capacity::Cap1'),
    f('model.bidirectional::DS1'),
    f('security.external-share::WS2'),
    f('cost.oversized::Cap2'),
    f('report.too-many-visuals::Rpt1'),
  ];
  const result = routeFindings(findings);
  assert.equal(result['security-team'].length, 2);
  assert.equal(result['finops'].length, 2);
  assert.equal(result['powerbi-team'].length, 2);
});

// ---------------------------------------------------------------------------
// edge cases
// ---------------------------------------------------------------------------

test('routeFindings: empty findings → {}', () => {
  assert.deepEqual(routeFindings([]), {});
});

test('routeFindings: no-arg call → {}', () => {
  assert.deepEqual(routeFindings(), {});
});

// ---------------------------------------------------------------------------
// custom routes override
// ---------------------------------------------------------------------------

test('routeFindings: custom routes override changes destination', () => {
  const customRoutes = { ...DEFAULT_ROUTES, security: 'custom-security-team' };
  const result = routeFindings([f('security.admin-grant::X')], customRoutes);
  assert.deepEqual(result['custom-security-team'], ['security.admin-grant::X']);
  assert.equal(result['security-team'], undefined);
});

test('routeFindings: custom routes with empty object routes everything to unrouted', () => {
  const result = routeFindings([f('security.admin-grant::X')], {});
  assert.deepEqual(result['unrouted'], ['security.admin-grant::X']);
});

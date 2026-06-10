import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sanitizeEvidence, sanitize } from './sanitize.js';
import { detectAll } from './detectors/index.js';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const estate = JSON.parse(
  readFileSync(join(__dirname, '..', 'fixtures', 'estate.json'), 'utf8')
);

// ── sanitizeEvidence ──────────────────────────────────────────────────────────

test('sanitizeEvidence keeps numbers', () => {
  const out = sanitizeEvidence({ peakCuPct: 96, throttleMinutes: 42 });
  assert.equal(out.peakCuPct, 96);
  assert.equal(out.throttleMinutes, 42);
});

test('sanitizeEvidence keeps booleans', () => {
  const out = sanitizeEvidence({ gatewayHealthy: false });
  assert.equal(out.gatewayHealthy, false);
});

test('sanitizeEvidence turns arrays into a count', () => {
  const out = sanitizeEvidence({ datasets: ['Sales', 'Finance', 'Logistics'] });
  assert.equal(out.datasetsCount, 3);
  assert.ok(!('datasets' in out), 'raw array must be stripped');
});

test('sanitizeEvidence keeps safe enum keys (sku, status)', () => {
  const out = sanitizeEvidence({ sku: 'F64', status: 'Failed' });
  assert.equal(out.sku, 'F64');
  assert.equal(out.status, 'Failed');
});

test('sanitizeEvidence keeps time as a safe non-identifying temporal value', () => {
  const out = sanitizeEvidence({ time: '06:00' });
  assert.equal(out.time, '06:00');
});

test('sanitizeEvidence drops free-text strings (source, workspace, name)', () => {
  const out = sanitizeEvidence({ source: 'Synapse: dw_sales', workspace: 'Finance', name: 'Sales' });
  assert.ok(!('source' in out), 'source must be stripped');
  assert.ok(!('workspace' in out), 'workspace must be stripped');
  assert.ok(!('name' in out), 'name must be stripped');
});

test('sanitizeEvidence handles empty evidence object', () => {
  assert.deepEqual(sanitizeEvidence({}), {});
});

test('sanitizeEvidence handles undefined (default param)', () => {
  assert.deepEqual(sanitizeEvidence(), {});
});

// ── sanitize (flag array) ─────────────────────────────────────────────────────

test('sanitize produces items with only id, type, evidence', () => {
  const flags = [
    {
      type: 'capacity.throttle',
      resource: 'Contoso / capacity F64',
      what: 'Capacity F64 reached 96% CU.',
      when: '2026-06-08T06:05:00.000Z',
      evidence: { peakCuPct: 96, throttleMinutes: 42 },
    },
  ];
  const result = sanitize(flags);
  assert.equal(result.length, 1);
  assert.equal(result[0].id, 0);
  assert.equal(result[0].type, 'capacity.throttle');
  assert.ok('evidence' in result[0], 'evidence must be present');
  assert.ok(!('resource' in result[0]), 'resource must be stripped');
  assert.ok(!('what' in result[0]), 'what must be stripped');
  assert.ok(!('when' in result[0]), 'when must be stripped');
});

test('sanitize assigns sequential ids', () => {
  const flags = [
    { type: 'a', resource: 'r1', what: 'w', when: 't', evidence: {} },
    { type: 'b', resource: 'r2', what: 'w', when: 't', evidence: {} },
    { type: 'c', resource: 'r3', what: 'w', when: 't', evidence: {} },
  ];
  const result = sanitize(flags);
  assert.deepEqual(result.map(r => r.id), [0, 1, 2]);
});

test('sanitize returns [] for empty array', () => {
  assert.deepEqual(sanitize([]), []);
});

// ── Privacy: no identifiers in sanitized payload from estate.json ─────────────

// ── Inc 31: Sensitivity-aware redaction ────────────────────────────────────────

test('sanitizeEvidence: sensitive:true → { redacted:true } (numeric field NOT leaked)', () => {
  const out = sanitizeEvidence({ sensitive: true, peakCuPct: 96 });
  assert.deepEqual(out, { redacted: true });
  assert.ok(!('peakCuPct' in out), 'numeric field must not be leaked when sensitive:true');
});

test('sanitizeEvidence: sensitivityLabel set → { redacted:true }', () => {
  const out = sanitizeEvidence({ sensitivityLabel: 'Confidential', x: 1 });
  assert.deepEqual(out, { redacted: true });
});

test('sanitizeEvidence: sensitive:false → normal sanitization, NOT redacted', () => {
  const out = sanitizeEvidence({ sensitive: false, peakCuPct: 80 });
  assert.ok(!('redacted' in out), 'should not be redacted when sensitive:false');
  assert.equal(out.peakCuPct, 80);
});

// ── Inc 31 privacy — test restored below ───────────────────────────────────────

test('privacy: sanitize(detectAll(estate)) contains no identifying names', () => {
  const flags = detectAll(estate);
  const payload = JSON.stringify(sanitize(flags));

  const IDENTIFIERS = ['Contoso', 'Finance', 'Sales', 'Synapse', 'Logistics'];
  for (const id of IDENTIFIERS) {
    assert.ok(
      !payload.includes(id),
      `Sanitized payload must not contain identifier "${id}"`
    );
  }
});

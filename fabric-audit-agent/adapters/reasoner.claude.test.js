import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createClaudeReasoner } from './reasoner.claude.js';
import { getRemediation } from '../core/kb/index.js';
import { scoreConfidence } from '../core/confidence.js';

// ── Fake client (never the real @anthropic-ai/sdk) ────────────────────────────

function makeFakeClient(responseText) {
  let capturedReq = null;
  const client = {
    messages: {
      async create(req) {
        capturedReq = req;
        return {
          content: [{ type: 'text', text: responseText }],
        };
      },
    },
    getCaptured() { return capturedReq; },
  };
  return client;
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

const ONE_FLAG = {
  type: 'capacity.throttle',
  resource: 'Contoso / capacity F64',
  what: 'Capacity F64 reached 96% CU.',
  when: '2026-06-08T06:05:00.000Z',
  evidence: { peakCuPct: 96, throttleMinutes: 42 },
};

const API_RESPONSE_TEXT = JSON.stringify([
  { id: 0, why: 'Peak CU demand exceeds SKU ceiling.', impact: 'All reports queued.', fix: ['Stagger refreshes', 'Resize SKU'] },
]);

// ── Happy path ─────────────────────────────────────────────────────────────────

test('reason() returns one finding enriched from fake response', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });

  const findings = await reasoner.reason({}, [ONE_FLAG]);

  assert.equal(findings.length, 1);
  assert.equal(findings[0].why, 'Peak CU demand exceeds SKU ceiling.');
  assert.equal(findings[0].impact, 'All reports queued.');
  assert.deepEqual(findings[0].fix, ['Stagger refreshes', 'Resize SKU']);
});

test('reason() sets finding.key to type::resource', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);
  assert.equal(findings[0].key, 'capacity.throttle::Contoso / capacity F64');
});

test('reason() scores severity via scoreSeverity', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);
  // peakCuPct=96 >=90 and throttleMinutes=42 >30 => Critical
  assert.equal(findings[0].score.level, 'Critical');
});

test('reason() returns a 7-field finding', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);
  const f = findings[0];
  for (const field of ['what', 'where', 'when', 'why', 'impact', 'fix', 'score']) {
    assert.ok(f[field] !== undefined && f[field] !== null, `finding must have field "${field}"`);
  }
});

// ── Empty flags ────────────────────────────────────────────────────────────────

test('reason() returns [] without calling client when flags is empty', async () => {
  let called = false;
  const fakeClient = {
    messages: {
      async create() { called = true; return { content: [] }; },
    },
  };
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const result = await reasoner.reason({}, []);
  assert.deepEqual(result, []);
  assert.equal(called, false, 'client.messages.create must NOT be called for empty flags');
});

// ── Privacy ────────────────────────────────────────────────────────────────────

test('privacy: captured request contains no identifiers from the flag', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  await reasoner.reason({}, [ONE_FLAG]);

  const req = fakeClient.getCaptured();
  assert.ok(req, 'request must have been captured');
  const reqStr = JSON.stringify(req);

  // These strings appear in the flag but must NOT reach the API payload
  const IDENTIFIERS = ['Contoso', 'F64', 'capacity F64'];
  for (const id of IDENTIFIERS) {
    assert.ok(
      !reqStr.includes(id),
      `Request must not contain identifier "${id}" (resource/what should be stripped)`
    );
  }
});

// ── Fallback on malformed response ─────────────────────────────────────────────

test('reason() falls back to KB when API returns non-JSON text', async () => {
  const fakeClient = makeFakeClient('not json at all');
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);

  const kb = getRemediation('capacity.throttle');
  assert.equal(findings.length, 1);
  assert.equal(findings[0].why, kb.rootCause,
    'why should fall back to KB rootCause when response is malformed');
  assert.deepEqual(findings[0].fix, kb.fixes,
    'fix should fall back to KB fixes when response is malformed');
});

test('reason() falls back to KB when API throws an error', async () => {
  const errorClient = {
    messages: {
      async create() { throw new Error('simulated network error'); },
    },
  };
  const reasoner = createClaudeReasoner({ client: errorClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);

  const kb = getRemediation('capacity.throttle');
  assert.equal(findings.length, 1);
  assert.equal(findings[0].why, kb.rootCause);
  assert.deepEqual(findings[0].fix, kb.fixes);
});

// ── Prompt caching: system block has cache_control ────────────────────────────

test('captured request has system as array with cache_control on first block', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  await reasoner.reason({}, [ONE_FLAG]);

  const req = fakeClient.getCaptured();
  assert.ok(Array.isArray(req.system), 'system must be an array for cache_control support');
  assert.equal(req.system[0].type, 'text');
  assert.ok(req.system[0].cache_control, 'system[0] must have cache_control');
  assert.equal(req.system[0].cache_control.type, 'ephemeral');
});

// ── Multiple flags → correct id-based re-attachment ──────────────────────────

// ── Inc 31: maxFlags spend cap ────────────────────────────────────────────────

function makeFlag(n) {
  return {
    type: 'capacity.throttle',
    resource: `Resource${n}`,
    what: `What ${n}`,
    when: '2026-01-01T00:00:00.000Z',
    evidence: { peakCuPct: 90 + n, throttleMinutes: 35 },
  };
}

test('maxFlags:2 with 5 flags → sent payload has length 2', async () => {
  let capturedPayload = null;
  const fakeClient = {
    messages: {
      async create(req) {
        capturedPayload = JSON.parse(req.messages[0].content);
        // Return enrichment for flags 0 and 1 only (the capped portion)
        return {
          content: [{ type: 'text', text: JSON.stringify([
            { id: 0, why: 'Why 0', impact: 'Impact 0', fix: ['Fix 0'] },
            { id: 1, why: 'Why 1', impact: 'Impact 1', fix: ['Fix 1'] },
          ]) }],
        };
      },
    },
  };
  const reasoner = createClaudeReasoner({ client: fakeClient, maxFlags: 2 });
  const flags = [makeFlag(0), makeFlag(1), makeFlag(2), makeFlag(3), makeFlag(4)];
  const findings = await reasoner.reason({}, flags);

  // The sanitized array sent to Claude must have length 2
  assert.ok(capturedPayload !== null, 'payload should have been captured');
  assert.equal(capturedPayload.length, 2, `expected 2 items sent to Claude, got ${capturedPayload.length}`);

  // All 5 flags still become findings (3 via KB fallback)
  assert.equal(findings.length, 5, `expected 5 findings (all flags), got ${findings.length}`);
});

test('maxFlags:2 with 5 flags → findings[2..4] use KB fallback', async () => {
  const fakeClient = {
    messages: {
      async create() {
        return {
          content: [{ type: 'text', text: JSON.stringify([
            { id: 0, why: 'Claude Why 0', impact: 'Impact 0', fix: ['Fix 0'] },
            { id: 1, why: 'Claude Why 1', impact: 'Impact 1', fix: ['Fix 1'] },
          ]) }],
        };
      },
    },
  };
  const reasoner = createClaudeReasoner({ client: fakeClient, maxFlags: 2 });
  const flags = [makeFlag(0), makeFlag(1), makeFlag(2), makeFlag(3), makeFlag(4)];
  const findings = await reasoner.reason({}, flags);

  // Flags 0 and 1 should have Claude enrichment
  assert.equal(findings[0].why, 'Claude Why 0');
  assert.equal(findings[1].why, 'Claude Why 1');

  // Flags 2, 3, 4 should fall back to KB
  const kb = getRemediation('capacity.throttle');
  assert.equal(findings[2].why, kb.rootCause, 'finding[2] should use KB fallback');
  assert.equal(findings[3].why, kb.rootCause, 'finding[3] should use KB fallback');
  assert.equal(findings[4].why, kb.rootCause, 'finding[4] should use KB fallback');
});

// ── Multiple flags → correct id-based re-attachment ──────────────────────────

test('reason() correctly maps multiple flags to their enriched data', async () => {
  const FLAG_A = {
    type: 'capacity.throttle',
    resource: 'ResourceA',
    what: 'What A',
    when: '2026-01-01T00:00:00.000Z',
    evidence: { peakCuPct: 90, throttleMinutes: 35 },
  };
  const FLAG_B = {
    type: 'model.bidirectional',
    resource: 'ResourceB',
    what: 'What B',
    when: '2026-01-01T00:00:00.000Z',
    evidence: { count: 10 },
  };

  const twoFlagResponse = JSON.stringify([
    { id: 0, why: 'Why A', impact: 'Impact A', fix: ['Fix A1'] },
    { id: 1, why: 'Why B', impact: 'Impact B', fix: ['Fix B1'] },
  ]);

  const fakeClient = makeFakeClient(twoFlagResponse);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [FLAG_A, FLAG_B]);

  assert.equal(findings.length, 2);
  assert.equal(findings[0].why, 'Why A');
  assert.equal(findings[1].why, 'Why B');
  assert.equal(findings[0].key, 'capacity.throttle::ResourceA');
  assert.equal(findings[1].key, 'model.bidirectional::ResourceB');
});

// ── reasonedBy tagging + confidence wiring (Fix 1) ───────────────────────────

test('Claude-enriched finding gets reasonedBy === "claude" and scoreConfidence === "medium"', async () => {
  const fakeClient = makeFakeClient(API_RESPONSE_TEXT);
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);

  // The response has a `why` for id 0 → must be tagged
  assert.equal(findings[0].reasonedBy, 'claude',
    'finding enriched with Claude why should have reasonedBy === "claude"');
  assert.equal(scoreConfidence(findings[0]), 'medium',
    'confidence for a Claude-enriched finding should be "medium"');
});

test('KB-fallback finding has no reasonedBy and scoreConfidence === "high"', async () => {
  // Empty response → every flag falls back to KB, no e.why, no tag
  const fakeClient = makeFakeClient('[]');
  const reasoner = createClaudeReasoner({ client: fakeClient });
  const findings = await reasoner.reason({}, [ONE_FLAG]);

  assert.equal(findings[0].reasonedBy, undefined,
    'KB-fallback finding must not have reasonedBy');
  assert.equal(scoreConfidence(findings[0]), 'high',
    'confidence for a KB-fallback finding should be "high"');
});

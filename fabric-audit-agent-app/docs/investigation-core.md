# Investigation Core — how it works

The investigation core (Phase 1) is the layer that lets the agent **investigate and explain** a
capacity question instead of only emitting a fixed audit. It is **offline, stdlib-only, and
read-only**, and it is built on one load-bearing principle:

> **Deterministic detectors/collectors decide *whether* a problem exists and assemble the evidence;
> the LLM only *explains, correlates, and hypothesizes over confirmed evidence — and abstains when
> the evidence is thin.*** This bounds hallucination to wording, not diagnosis.

Everything here is pure and dependency-injected (the dict-style port pattern), so it runs the same
offline (mock/fake collectors) or, in a later phase, against live Databricks-hosted sources.

---

## Data flow

```
collector["collect"]()  ->  facts            (detectors/collectors already ran; facts are the truth)
        │
        ▼
playbook (investigate_user / investigate_capacity_spike)
        │   1. locate the subject in facts (user / capacity peak)
        │   2. build_coverage(facts)         -> what we saw / were blind to   (honesty)
        │   3. assess_confidence(...)         -> level from evidence density   (not model say-so)
        │   4. assemble evidence_item[]       -> attribution, capacity, baseline (if history present)
        │   5. _finish(...) packages the envelope and calls the reasoner
        ▼
reasoner["investigate"](bundle)              -> explanation + hypotheses + assumptions + whatWouldConfirm
        │   (stub: grounded ONLY in the passed evidence; abstains when confidence == "insufficient")
        ▼
investigation envelope  (returned to the MCP tool handler, which adds source: live|mock)
```

The playbook never asks the reasoner *whether* there's a problem — it hands it confirmed evidence and
asks it to explain. If the subject isn't in the data, the playbook abstains **before** the reasoner
can invent a cause.

---

## Components

| File | Responsibility |
|------|----------------|
| `investigation/evidence.py` | `build_coverage(facts)`, `assess_confidence(*, found, corroborating_sources)`, `evidence_item(kind, summary, data)` — the honesty + evidence primitives. |
| `investigation/baseline.py` | `compute_baseline(rows)` (p50/p95/p99 + op-mix + peak-hour) and `compare_to_baseline(today_cu, baseline)` — the **CPU×duration** "is today abnormal for this entity" model. |
| `investigation/playbooks.py` | `investigate_user(...)` and `investigate_capacity_spike(...)` — deterministic orchestration; the `_finish(...)` helper packages every return. |
| `adapters/reasoner_investigation.py` | `create_investigation_reasoner()` → `{"investigate": fn}`. Stub is grounded + abstaining. Always the deterministic stub (LLM lives at the agent-loop level). |
| `tools.py` | The MCP tool handlers: `user_activity`, `investigate_user`, `investigate_capacity_spike` (+ the existing `run_audit`, `list_workspaces`). |
| `mcp_server.py` | `build_mcp_server` registers every tool (no-arg + arg-taking) with FastMCP. |
| `eval/score_investigations.py` | `score_investigation_case(case)` + `run_suite()` — the groundedness + coverage-honesty scorer; CLI `python -m fabric_audit_agent eval-investigations`. |

---

## The investigation envelope (output contract)

Every playbook returns the same shape; the tool handlers add `source`:

```jsonc
{
  "subject": "user x@co",
  "abstained": false,
  "coverage": {
    "workspacesSeen": ["Sales"],
    "sources": ["attribution", "capacity"],   // "inventory" instead of "attribution" if items but no users
    "sourcesFailed": [],                        // attempted-but-errored sources (EMPTY-due-to-failure)
    "mode": "live",                             // data-shape heuristic — NOT the live-vs-mock truth
    "blind": []                                 // sources/scope we never queried (BLIND)
  },
  "confidence": { "level": "high", "basis": "2 sources corroborate" },
  "evidence": [
    { "kind": "attribution", "summary": "x@co = 90% of monitored CU ...", "data": { ... } },
    { "kind": "capacity",    "summary": "capacity peaked 120% (10 min throttled)", "data": { ... } },
    { "kind": "baseline",    "summary": "today 900 CU(s) vs p50 30 over last 30d (n=5): ABOVE p95 ...", "data": { ... } }
  ],
  "result": {
    "explanation": "...",            // grounded in the evidence above
    "hypotheses": ["...", "..."],    // [] when abstaining
    "assumptions": ["CPU-time is a proxy for CU (monitored, not authoritative capacity CU)", "..."],
    "confidence": "high",
    "whatWouldConfirm": ["corroborate against Capacity Metrics / Capacity Events CU%"]
  },
  "source": "mock"   // added by the tool handler: "live" iff a real source is configured (_has_live_source)
}
```

---

## Honesty guarantees (the must-fixes, as enforced in code)

1. **Detectors ground the LLM.** The reasoner only narrates the assembled `evidence`; the stub literally
   cites `evidence[].summary`. It cannot introduce a finding the playbook didn't assemble.
2. **Abstention is first-class.** Subject absent (user not in `facts["users"]`) or no signal
   (`capacity.peakCuPct` missing) → `abstained: true`, `confidence.level == "insufficient"`,
   `hypotheses: []`. No fabrication.
3. **Coverage honesty — BLIND vs EMPTY.** `build_coverage` reports `workspacesSeen`, `sourcesFailed`
   (attempted-but-errored), and `blind` (never queried). `sources` says `"attribution"` only when real
   per-user data is present, else `"inventory"`.
4. **Confidence from evidence, not the model.** `assess_confidence` maps `found` + `corroborating_sources`
   → level; the model never sets its own confidence.
5. **Monitored ≠ capacity CU.** Proxy shares (`attributionMode == "cost"`) are labeled `"monitored CU"`;
   only authoritative sources (CSV / Capacity Metrics) say `"capacity CU"`.
6. **Mock ≠ live.** Tool handlers label `source: "live"|"mock"` from `_has_live_source(env)` — the
   *configured-source* truth, **not** `coverage.mode` (the mock fixture has real-looking data, so the
   data-shape heuristic alone would misread it as live).
7. **No silent degradation.** The investigation reasoner is always the deterministic stub; the LLM lives at
   the agent-loop level where failures surface via the agent harness, not silently.
8. **Read-only.** Handlers only `collect()` and return; nothing is written or persisted on the
   interactive path (persistence is the scheduled Job's role).

---

## How to use it

**MCP tools** (read-only; offline they run against `fixtures/estate.json` and report `source: "mock"`):
- `user_activity` — `{}` → ranked top users; `{"user": "<upn>"}` → that user's detail.
- `investigate_user` — `{"user": "<upn>", "days": 30}` → grounded investigation (baselines included when history is present).
- `investigate_capacity_spike` — `{"when": "<optional>"}` → top driver of a capacity peak, or abstains.

**Eval harness** (offline, no API key):
```
python -m fabric_audit_agent eval-investigations     # -> "Investigations: N/N passed"
```
Golden cases live in `fabric_audit_agent/eval/investigation_cases.json`; the scorer enforces
groundedness (every hypothesis traces to cited evidence) and coverage-honesty (abstains iff expected).

**Tests:** `cd fabric-audit-agent-py && python -m pytest -q` (currently 460 passed, 1 skipped).

---

## Dormant by design / Phase-2 seams

- **Claude reasoner path** — the LLM lives at the agent-loop level (Phase 2+); the playbook reasoner is
  always the deterministic stub (`create_investigation_reasoner()`).
- **Baseline history** — `investigate_user` computes a baseline only when `facts["history"][<user>]` is
  present. Phase-1 collectors don't populate per-entity history, so the baseline path is dormant until a
  Phase-2 history collector (FUAM / Workspace Monitoring time series) fills it. `compute_baseline` is fully
  tested in isolation and wired through the playbook, so it lights up the moment history arrives.
- **MCP arg-tool registration test** — the union-signature FastMCP wrapper is exercised in production but
  has no offline test (FastMCP isn't in the test env — the 1 skipped test). Add when the `mcp` extra is in CI.

---

## Next: Phase 2

Wrap this core as an MLflow `ResponsesAgent` (raw Anthropic tool-loop) on a Databricks App, with OBO
read-only auth and MLflow tracing, calling the in-tenant Claude endpoint + these MCP tools. See
`docs/superpowers/plans/2026-06-30-capacity-investigator-phase1.md` (the phase roadmap) and
`research/agent-arch/10-rerun-verdict.md` (the validated architecture + must-fixes).

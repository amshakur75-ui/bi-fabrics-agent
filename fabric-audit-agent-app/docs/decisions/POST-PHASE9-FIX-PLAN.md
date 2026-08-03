# Fix Plan: Post-Phase-9 Issues (2026-07-30)

## Issues Found

### P0: Phase 6 audit_findings write never wired (critical — breaks Phase 6 end-to-end)
`pipeline.py`'s `run_audit()` has no `findings_store` parameter. `job.py` never constructs one.
The `audit_findings` Delta table exists and `create_findings_store_delta()` is built, but nothing
ever calls `write()` — the table is permanently empty and Phase 6's context injection always
returns an empty list.

### ~~P1a: size-up verdict announced in Teams card without being asked~~ — SUPERSEDED
*Superseded by delivery removal (Part A of post-Phase-9 sprint, 2026-07-30).
`teams_card.py` has been deleted entirely; Phase 10 (Entra bot identity) will build
delivery from scratch with correct rules.*

### ~~P1b: Adaptive Card schema version 1.4 — breaks mobile Teams~~ — SUPERSEDED
*Superseded by delivery removal (Part A of post-Phase-9 sprint, 2026-07-30).
`teams_card.py` has been deleted entirely; Phase 10 will use schema version 1.2.*

### P2a: Tier 2 cadence 15 → 5 minutes
5-minute deterministic check is strongly better for the primary use case (catching throttling and
concentration events near real-time). The check is cheap (no LLM, just gate functions against the
Capacity Events stream) — 5 minutes adds negligible compute cost and meaningfully closes the
response window.

### P2b: Tier 1 daily → hourly LLM sweep
Hourly with `decide_alert()`'s existing change-gating means a quiet healthy capacity generates
exactly one "no material change" record per hour with zero alert fired. A worsening capacity
gets a proper trend-aware narrative every hour instead of once daily — this is the right
operating cadence for a production monitoring agent.

### P3a: No end-to-end wiring test for Phase 5→6 pipeline
Each component is unit-tested in isolation (store_delta.py, context_findings.py both have their
own tests) but there's no test that: (1) runs a full `run_audit()` with an injected fake
findings_store, (2) asserts that `findings_store["write"]` was called with real findings, and
(3) asserts that `query_recent_findings()` against a pre-populated store actually injects
non-empty context. Classic AI blind spot: the same model wrote all three components and their
unit tests, never catching the missing wire between them.

### P3b: No test for size-up suppression in Teams card
The system prompt prevents the LLM from volunteering size-up in text — but there's no test
confirming `build_teams_card()` withholds the verdict section when `decision == "size-up"` and
it wasn't asked. The card builder is pure Python; this is exactly the kind of logic that should
have a regression test before the bug is introduced, not after it ships.

### P3c: No test confirming Tier 2 alert payload never includes size-up
Same gap for the Tier 2 path: `tier2_check.py`'s `_build_tier2_alert_summary()` doesn't
reference the verdict decision at all (it only surfaces concentration/throttle/pressure/overage
triggers), so this one is currently safe — but there's no test asserting it stays that way.

### P4a: Tier 2 reads static CSV sources every 5 minutes (waste)
`run_tier2_job()` calls `build_collector_from_env(env, window="15m")` which composes ALL
configured collectors including CSV. CSV data doesn't change between checks. Tier 2 should only
pull live-stream sources (Capacity Events KQL), not static files — both for performance and
correctness (a stale CSV read can make the deterministic check fire on historical data).

### P4b: store_delta.py history() full table scan
`.orderBy("run_at").limit(keep)` (ascending) forces Spark to scan the full table on every call.
As the table grows (24 rows/day at hourly sweep = ~2,000 rows/quarter), this becomes an
increasingly inefficient read. Fix: use descending order with limit, then reverse in Python.
This also makes `[-1]` access in `alerting.py` semantically clearer (most recent first).

### P4c: context_findings.py query() uses f-string scope injection (minor)
The `query()` function in `create_findings_store_delta()` injects `scope` and `tenant` directly
into the SQL string via f-string. For an internal tool where values come from pipeline code (not
user input) this is low risk, but should use Spark's parameterized query API instead.

### P5: Investigation and alert output gaps (2026-07-30)
Based on direct code review of `finding.py`, `trend.py`, `anomaly.py`, `forecast.py`,
`accountability.py`, `correlate.py`, `coaching.py`, `narrative.py`, `teams_card.py`, and
`system_prompt.py`, the following enrichment is either absent or not surfaced to the user in
the alert/investigation output:

- **P5a: Teams alert card is sparse** — `build_teams_card()` only surfaces summary + verdict +
  critical finding titles + first fix step. All of recurrence count, anomaly context,
  healthy-vs-unhealthy framing, trend/forecast data, and WHY narrative are computed and
  available in the pipeline envelope — they just aren’t put into the card.
- **P5b: trend.py window hardcoded to 7 runs, not human-readable** — at hourly sweeps this is
  7 hours of context. The user never sees how long something has been recurring in calendar
  terms (e.g. "for the past 3 days").
- **P5c: No healthy-vs-unhealthy framing in Tier 2 alerts** — the cadence-vs-causation
  distinction exists in the system prompt and interactive path but Tier 2 deterministic alerts
  fire on thresholds with no context. A legitimate large report run looks identical to a
  runaway automated process in the alert payload.
- **P5d: No multi-month baseline comparison** — anomaly detection compares current vs.
  historical mean/stddev (correct) but there’s no weekly/monthly bucketed summary enabling
  “this is 2× April’s baseline” comparisons. The 90-day retention window supports it;
  the bucketing and prompt rule don’t exist yet.
- **P5e: System prompt has no explicit rule about surfacing recurrence** — `recurringRuns`
  and `accountability` are computed and available in every tool result, but the prompt doesn’t
  mandate stating them when reporting a finding. The agent may omit them depending on phrasing.

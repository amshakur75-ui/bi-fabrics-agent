# Alerting Redesign + Plugin Parity — Execution Progress Ledger

Durable resume point for the 6-sub-plan program. Spec: `docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md`. Baseline suite: 1775 passed.

## Sub-plan 1 — Alerting redesign (DONE — suite 1803)
- [x] **Phase 0** — query text captured (EventText→queryText). Shape detector unblocked.
- [x] **1a** — absolute-cost detector `detectors/absolute_cost.py` (`activity.slow-operation`). `23ecf2e`.
- [x] **1b** — query fingerprint + `detectors/query_shape.py` (`activity.recurring-shape`). `5a637e4`.
- [x] **1-WIRE** — `job._build_events_collector` attaches bounded (5000, costliest-first, fail-open) `facts["events"]`; both detectors now fire in the sweep. `2d01337`.
- [x] **1c/1d** — retired CU-blended `user_concentration` + `capacity.user-concentration`; item-level concentration KEPT intact; concentrationPct value unchanged (low-stakes knob). `2c58d76`.
- [x] **FIX 0** — `detectors/capacity.py::_capacity_refreshes` falls back to top-level `facts["refreshes"]` on the merge path → optimize verdict reachable. `944926c`.
- [x] **FIX 3** — `accountability.AUTO_RESOLVING_TYPES` (throttle/pressure/overage) excluded from "no-resolution"/SLA-breach language. `944926c`.

## Sub-plan 2 — Bad-activity taxonomy detectors (DONE — suite 1852)
- [x] **2a** refresh sub-causes (credential/gateway/timeout/concurrency/constraint; silent-success skipped=no rows/bytes field). `847247c`
- [x] **2b** query anti-patterns incl. flagship MDX Hierarchize/CrossJoin shape + DAX via analyze_dax; fact/dim + SE-count skipped (no data). `7ef4ce5`
- [x] **2c** XMLA/connection error classifier + detector (uses EventText/queryText) + "session moved" suppression. `c557918`
- [x] **2d** long-running-cluster detector (wired); per-user baseline helper built-but-DEFERRED (no per-user history store); multi-visual suppression in query_shape (all-fast recurring shapes suppressed). `fb1fa01`

## Sub-plan 3 — Daily-summary + card/notification redesign (DONE — suite 1868)
- [x] **3a** Part-7 fix (Python): ticket keyed by `incident_key` (nullable chat_id), gate decoupled, migration SQL, both sweep+tier2. `1f41fcf`. **CARRY-FORWARD → Sub-plan 6:** app-side `/api/alerts` is chat-driven, so chat-less tickets still need a TS read-path change (independent alert_ticket query keyed by incident_key + ack + UI deep-link fallback) to actually surface.
- [x] **3b** daily-summary rebuilt around taxonomy: refresh isolated, recurring-shape vs slow-ops, top-users from `facts["events"]` (no %), CU one-liner, no-issues fallback. suite 1862.
- [x] **3c/3d** cards carry separate `Capacity this window` fact + `When`/first-noticed; investigation PIVOT in both `_investigate_query` builders + system_prompt. `013b967`.

## Sub-plan 4 — Infra, health & wiring integrity (NOT STARTED)
health report + FAIL-OPEN classification + WIRING-MAP.md; Lakebase auth identity + retry; webhook URLError; error-conflation (25e); egress on ticketing/conversation; wire assert_model_map_invariant at startup.

## Sub-plan 5 — Plugin query-audit depth + safety rails (NOT STARTED)
domain-subset audit rules (BEST/HINT/relevant PERF+CORRECT, skip App-Insights TELEMETRY); perf-tuner patterns; large-result display gate (>50 rows); kql_format; SessionStart preflight; audit-before-execute prompt rule.

## Sub-plan 6 — Frontend UX (NOT STARTED)
U1 structured investigation card; U3 per-number CU-vs-proxy marker. (U2/U4 already shipped.)

## Deferred (NOT in program): HR enrichment; Teams-push (Phase 10); App-Insights TELEMETRY rules; business-measure formulas.

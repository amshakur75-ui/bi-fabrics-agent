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

## Sub-plan 4 — Infra, health & wiring integrity (DONE — suite 1907)
- [x] **4c** Lakebase auth uses execution identity (`_resolve_pg_user`, databricks.yml cleaned) + write reconnect/retry + webhook URLError. suite 1876.
- [x] **4d** error-conflation: `classify_live_query_error` distinguishes not-found/auth/throttled/timeout/network in describe_source + sample_events. `6cceea1`.
- [x] **4a/4b** `automation/health.py` HealthReport + digest banner + startup invariant wired + `docs/WIRING-MAP.md`. `b307201`. (Future work noted in WIRING-MAP: per-detector isolation, sweep-path health threading, full 107-except census.)
- [x] **4e** egress: ticketing + conversation route through `apply_egress_controls`. `e55a9d3`.

## Sub-plan 5 — Plugin query-audit depth + safety rails (DONE — suite 1994)
- [x] **5a** ported 22 domain-relevant audit rules (now 27 total; TELEMETRY skipped) + perf-tuner patterns; +59 tests. `3f6c923`.
- [x] **5b** large-result display gate on run_kql (>50 rows → `largeResult` + 4 options + 100-row cap) + prompt rule. suite 1974.
- [x] **5c/5d/5e** `query/kql_format.py` (wired to display queryKql) + `automation/preflight.py` (startup snapshot → health) + audit-before-execute prompt rule. `e25dcc1`.

## Sub-plan 6 — Frontend UX + Part-7 read-path (DONE — Python 1996, client builds)
- [x] **6a** Part-7 TS read-path: chat-less tickets surfaced (`getChatlessAlertTickets` + `/by-incident/` routes + UI `?query=` deep-link fallback); manual migration `packages/db/migrations/manual/0001_alert_ack_incident_key.sql` (apply at deploy). client+server builds pass. `4e73b1f`,`1819c69`.
- [x] **6b/6c** U1 structured investigation answers (Finding/Evidence/Root cause/Fix headings) + U3 inline proxy labeling — both via single-sourced system_prompt rules (PROSE already styles headings). `6fae6b5`.

## PROGRAM COMPLETE — all 6 sub-plans done. Python suite 1775 → 1996 (+221), zero regressions.

### Deploy-time steps (operator, not code):
1. Apply `scripts/create_lakebase_alert_ticket.sql` (incident_key PK migration — Part 7).
2. Apply `packages/db/migrations/manual/0001_alert_ack_incident_key.sql` (alert_ack incident_key — Part 7 read-path).
3. Apply `scripts/create_delta_tables.sql` liquid-clustering ALTERs (from the earlier phase-7 work).
4. Grant the job's service-principal client id on Lakebase (Part 16 auth).
5. Frontend: `npm run build && npm run lint`; deploy the chat app.

### Deferred (unchanged): HR enrichment; Teams-push (Phase 10); App-Insights TELEMETRY rules; business-measure formulas; per-user baseline detector (needs a history store); run_sql/run_dax large-result gate; describe_sql_table/semantic_model error-class parity.

## Deferred (NOT in program): HR enrichment; Teams-push (Phase 10); App-Insights TELEMETRY rules; business-measure formulas.

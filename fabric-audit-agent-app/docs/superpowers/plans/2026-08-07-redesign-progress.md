# Alerting Redesign + Plugin Parity — Execution Progress Ledger

Durable resume point for the 6-sub-plan program. Spec: `docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md`. Baseline suite: 1775 passed.

## Sub-plan 1 — Alerting redesign (IN PROGRESS)
- [x] **Phase 0** — verify query text captured. DONE: `EventText`→`queryText` exists (collector_events_la.py:71, events.py:17/29, threaded through spike/raw_events, ~400-char truncation). Shape detector unblocked.
- [x] **1a** — absolute-cost detector `detectors/absolute_cost.py` (`activity.slow-operation`). DONE, commit `23ecf2e`, suite 1785. **KNOWN GAP:** detector reads `facts["events"]` but the pipeline never attaches events to facts → dormant until the wiring task below.
- [ ] **1b** — query-shape fingerprint (`investigation/query_fingerprint.py`) + `detectors/query_shape.py` (`activity.recurring-shape`). Pure/new. Uses `queryText`.
- [ ] **1-WIRE** — attach raw per-operation events onto `facts["events"]` in the collection path (collector_merge/pipeline/tier2+sweep) so 1a AND 1b actually run in production. (Discovered during 1a — REQUIRED, not optional.)
- [ ] **1c/1d** — retire the CU-blended `user_concentration.metric()` + its `capacity.user-concentration` finding + Tier-2 per-user branch; reframe item-level concentration to config threshold (default 60).
- [ ] **FIX 0** — verdict "optimize" unreachable: nest merged refreshes under `capacity` (collector_merge vs detectors/capacity.py).
- [ ] **FIX 3** — sla.py + accountability.py: exclude throttle/pressure/overage from "no-resolution" SLA language.

## Sub-plan 2 — Bad-activity taxonomy detectors (NOT STARTED)
refresh sub-causes; MDX shape/fact-FILTER/high-SE anti-patterns; XMLA Category-3 + "session moved" suppression; long-running-cluster + per-user baseline detector; multi-visual suppression.

## Sub-plan 3 — Daily-summary + card/notification redesign (NOT STARTED)
Part-7 chat_id swallow bug FIRST; daily-summary taxonomy rebuild + top-10 users + refresh section + no-CU-fallback; card separate capacity/attribution facts + When/first-noticed; investigation pivot.

## Sub-plan 4 — Infra, health & wiring integrity (NOT STARTED)
health report + FAIL-OPEN classification + WIRING-MAP.md; Lakebase auth identity + retry; webhook URLError; error-conflation (25e); egress on ticketing/conversation; wire assert_model_map_invariant at startup.

## Sub-plan 5 — Plugin query-audit depth + safety rails (NOT STARTED)
domain-subset audit rules (BEST/HINT/relevant PERF+CORRECT, skip App-Insights TELEMETRY); perf-tuner patterns; large-result display gate (>50 rows); kql_format; SessionStart preflight; audit-before-execute prompt rule.

## Sub-plan 6 — Frontend UX (NOT STARTED)
U1 structured investigation card; U3 per-number CU-vs-proxy marker. (U2/U4 already shipped.)

## Deferred (NOT in program): HR enrichment; Teams-push (Phase 10); App-Insights TELEMETRY rules; business-measure formulas.

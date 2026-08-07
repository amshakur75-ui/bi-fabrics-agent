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

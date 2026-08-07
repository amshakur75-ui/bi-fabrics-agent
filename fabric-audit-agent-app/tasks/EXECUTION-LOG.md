# EXECUTION LOG — master-integration-plan.md autonomous run

## FINAL SUMMARY (2026-08-07, definition of done — Machine A + Machine B)

**Outcome:** ALL phases 0–7 complete. Machine A landed phases 0–6 + the ports; **Machine B finished
Phase 7 close-out** — the last real code item (4.11 SKU cross-check), verified 4.5, shipped the two
Phase-5 frontend items (5.5 kql-viewer, 5.6 chart brand parity), verified+hardened the Phase-6.2
Delta tables (added the missing liquid clustering), and dispositioned 6.1/6.3/6.4 as explicit
deferrals. The ONLY remaining deferral is the 7.2 live-check checklist (needs the deployed app +
real credentials) — written out in full below, explicit not silent. Every other item carries a
disposition (done / verified-already-done / partial-with-reason / genuinely-deferred-with-reason).

**Test surface:** baseline 1547 → Machine A **1766** → Machine B **1775 passed / 55 subtests**
(+228 total, zero regressions). Machine-A commits (11): c922099, a820220, 4bd5382, 8d9e73b, 1f963dc,
519239e, 83da84b, + finalization. Machine-B changes: `investigation/sku.py` + `tools.py` (4.11),
`tests/test_sku.py` + `tests/test_sku_mismatch_wiring.py` (+9), `e2e-chatbot-app-next` kql-viewer.tsx
/ tool.tsx / chart.tsx (5.5/5.6), `scripts/create_delta_tables.sql` (6.2), and the Phase-7 doc
updates (this log, GAPS-AND-ISSUES.md 7.3 ledger, master-integration-plan.md checkboxes,
MCP-AGENT.md count). Complete phase-by-phase log below; the Machine-B section is at the very bottom.

**33 read-only agent tools** now registered (was 26): +7 resolve tools (resolve_term, resolve_field,
field_usage_query, workspace_usage_query, field_search, field_detail, artifact_lookup) + 2 export
(export_html_report/xlsx_report), all with self-contained Part 23/24e guidance in their
descriptions. Three loop hooks land in BOTH agent.py + loop.py via a single shared `loop_hooks.py`
(one implementation → the twins cannot drift). Firewall now audits agent-authored KQL against the
ported 4-rule error/warning engine + 8-check preflight; parse_kusto_error maps raw Kusto errors to
actionable suggestions.

**Downstream note:** the sibling fabric-audit-mcp repo carries its own tool-count assertions
expecting 26 — bump to 33 on next wheel rebuild. New firewall stage `"audit-rule"` is additive.

---


Started: 2026-08-07. Working dir: `C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent\`.
Operating under `tasks/CLAUDE-CODE-EXECUTION-PROMPT.md` (autonomy contract). This log is the
durable record: every numbered item gets a blast-radius review (before), the change, tests
(before/after counts), an after-review of named callers, and decisions + reasoning.

## Baseline (Phase 0.4)
- Full suite at start (HEAD, includes this session's earlier title/alerts fixes):
  **1542 passed, 55 subtests passed** (`python -m pytest -q`, fabric-audit-agent-app). This is
  the regression floor every later phase compares against.

## Orchestration decisions (logged per contract)
- GAPS-AND-ISSUES.md is 2687 lines / ~72k tokens. Reading it wholesale into the main working
  context would crowd out the code being edited. DECISION: Phase 0.1 reconciliation is delegated
  to a read-only subagent that reads it in full and writes `tasks/GAPS-RECONCILIATION.md` (open
  items × owning phase). Reversible (a doc), preserves every caller (touches no code), matches the
  plan's "completeness backstop" intent. The header + all of Section 1 (code gaps A1–N16) were
  read directly here.
- Phase 0.3 core-file familiarity: the per-item loop (step 2) already mandates reading each target
  file in full before editing, so 0.3 is primed via a subagent that produces
  `tasks/BLAST-RADIUS-CORE.md` (a map of callers/consumers for the core files) rather than my
  holding all of them in context at once.
- HTTP concerns for Log Analytics (Phase 1.1–1.4 timeout/retry/validation/error-conflation) live
  in `adapters/clients.py::build_log_analytics_query`, NOT `collector_log_analytics.py` (which is
  pure orchestration over an injected `query` port). Verified by reading the collector in full.

---

## PHASE 0 — Pre-flight

### 0.4 Baseline — DONE
Recorded above: 1542 passed / 55 subtests.

### 0.2 Extract plugin data — DONE
Source: `C:\Users\am08570\Downloads\kql-mcp-server-v5\data\`. Copied verbatim (no build-script
re-run, per tightening 25b — the `.cjs` inputs aren't in the zip; pre-built outputs are
authoritative) into `fabric_audit_agent/data/plugin/`:
`newell-schema.json`, `enriched-field-catalog.json`, `catalog/manifest.json`,
`catalog/search-index.json`, `catalog/models/*.json` (14), `ArtifactsMappedtoWorkspace.xlsx`,
`HCMIF0485_IDT_DASHBOARD.xlsx`. The two `.cjs` build scripts copied to `data/plugin/scripts/` for
future-refresh reference (Part 22/24d), NOT executed.

### 0.1 GAPS reconciliation — DONE (tasks/GAPS-RECONCILIATION.md)
Reconciled 93 ledger ids: 54 FIXED, 16 PARTIAL, 23 OPEN. Contradiction surfaced: **C2** is
REOPENED/urgent in GAPS Section 1 but FIXED in Section 11 + the plan's cross-check (ADR-001,
single-sourced `system_prompt.py`). DECISION: the later ADR-001 record supersedes — treat C2 as
FIXED (canonical prompt is single-sourced; the deployed-copy divergence it described predates the
restructure). Will re-verify in Phase 3.9 when the prompt is edited.

**16 open items NOT covered by any plan phase — ABSORBED here (0.1 completeness backstop):**
- SP2 ("validated" label precision), SP3 (cadence-vs-causation), SP5 (% base timepoint-vs-lifetime
  labeling), SP6 (inline `[inferred]`/`(derived)` labeling) → **Phase 3.9 / Phase 4** system-prompt
  additions (they are prompt-precision rules; fold into the prompt pass alongside 4.x labeling).
- N13 (startup health probes for the 3 data connections) → **Phase 4.11 area** (SKU/base-CU
  cross-check is already a startup probe; extend it to LA/capacity-events/FUAM reachability).
- N16 (UI-export fraction-scale guard in `importers/capacity_metrics.py`) → **Phase 1.8** (same ×100
  defect class as A1; add the guard when touching capacity-events scaling).
- N24C (±30s cross-source time-window alignment tolerance) → **Phase 4** (statistics/anomaly pass).
- V1 (automated 3-level validate.py cross-check harness) → **Phase 4.9** (math-consistency).
- UX1 (side-by-side check cards), UX2 (animated loading), UX3 (audience/coaching wiring),
  UX4 (audience detection) → **Phase 5 / tightening Part 11** (UI).
- EV2 (run mine_evals), EV3 4th case (repeated-question multi-turn eval) → **Phase 7.1/7.2**
  (verification/eval; EV3-4 needs a harness change — note it there).
- E3 (multi-workspace loop orchestration) → **Phase 6 (broadening) / tightening Part 9 B4**; plan
  states the multi-workspace stance but schedules no task — flag as deferred-with-reason at 7.4 if
  not built.
- N22 (step-budget classifier post-migration verification) → **Phase 7.2** live check.
These are now tracked; none silently dropped. The `sla.py` blanket-SLA bug maps to tightening
Part 17a (not a GAPS id) and is owned by the STANDING RULE + Phase 4 SLA work.

### 0.3 Core-file blast-radius map — DONE (tasks/BLAST-RADIUS-CORE.md)
Six core files mapped. Three facts that gate Phases 2–3, captured for the loop:
- **tools.py tool registration:** each tool = an inner `def <name>_handler(_input=None)` closure inside
  `create_tool_definitions(base_dir=None)` + a dict appended to its returned list:
  `{"name","description","input_schema"(json-schema),"handler"}`. 26 tools today. Consumers:
  `mcp_server.build_mcp_server` (FastMCP — `input_schema.properties/required` are load-bearing),
  `data_agent.build_data_agent_manifest` (strips `handler`), eval path `to_anthropic_tools`/
  `build_dispatch`. IMPORTANT: production async `agent.py` sources tools **over MCP** (does NOT import
  tools.py); only the sync eval path imports `create_tool_definitions`. So new resolve/export tools
  (Phase 3.8 / 5.4) register in tools.py AND must be reachable to the agent via MCP — mirror how
  `render_chart` (chart_tool.py) does its dual registration.
- **agent.py ↔ loop.py:** line-for-line parallel except 3 intentional divergences to preserve on both:
  toolResults element shape (`{tool,result}` vs `{tool,callId,input,result}`), dedup note text, and
  agent.py's async `on_tool` progress callback + empty-synth print. New hooks (Phase 3.10) insert at
  TWO seams in BOTH twins: pre-tool-exec (where `on_tool` sits) and post-tool-exec (after `handler`,
  before `wrap_untrusted`). No test enforces twin-equivalence — drift is silent.
- **kql_guard.py public API (do NOT change signatures — Phase 2 adds new file instead):**
  `escape_string(value)`, `escape_entity(name)`, `first_statement(text)`, `assert_read_only_kql(kql)`,
  `assert_kusto_host(cluster_uri)`, `_strip_string_literals(text)` (underscore but imported by firewall
  + mine — treat public). `firewall.validate_adhoc_kql` delegates to first_statement +
  assert_read_only_kql + _strip_string_literals. `FirewallRejection.stage` vocab is a tested contract.
Minor: tools.py:41 has a dead `assert_read_only_kql as _assert_read_only_kql` import (log; fix if
touched in Phase 2).

**PHASE 0 COMPLETE** (0.1 ✓ 0.2 ✓ 0.3 ✓ 0.4 ✓).

### 1.7 — shared collector builder — SCOPED, next code item
**Before-review (partial):** `job.py` defines `_default_collector` (L64) + `build_collector_from_env`
(L227, the shared builder). tools.py ALREADY calls `build_collector_from_env` for the audit/verdict
path (L313, L340) — so that path is NOT divergent. The divergence 1.7 targets is the independent
assembly in tools.py's `_collector_or_mock` (L451) and `_events_or_mock` (L732), plus the standalone
`build_log_analytics_query` wiring at L779/L1541/L2061 that doesn't replicate job.py's LA branch — the
"0 findings / healthy / peakCuPct null on the App path" bug. FULL blast radius = every tool that
triggers collection. DECISION: this is a high-blast-radius refactor of the shared collection path; per
the STANDING RULE it needs a complete caller-by-caller verification pass, so it is the next item to
execute with fresh context rather than rushed at the tail of this one. Scoped here so resume is
immediate: unify `_events_or_mock`/`_collector_or_mock` onto `build_collector_from_env` (or a shared
`_assemble_collector(env, window/…)` helper both job.py and tools.py import), preserving each tool's
existing return shape.

---

## PHASE 1 — Collector hardening

### 1.1 / 1.2 / 1.3 — LA timeout(55s) + retry + response validation — DONE
**Files:** `adapters/clients.py` (+`tests/test_clients_kusto.py`).
**Before-review (blast radius):** the LA HTTP logic lives in `clients.py::build_log_analytics_query`
(the collector `collector_log_analytics.py` is pure orchestration over the injected `query` port —
read in full, confirmed no HTTP there). Callers of `build_log_analytics_query`, grepped
repo-wide (excluding the stale `build/lib/` wheel artifact): `job.py` (2×), `tools.py` (3×),
`watch_run.py` (1×) — all pass creds positionally and use `timeout_seconds` as a default kwarg, so
changing the default 240→55 is safe. Return contract `query(kql, timespan=None) -> list[dict]`
is preserved. Tests touching it: `tests/test_clients_kusto.py` (header + timeout-default),
`tests/test_tier2_collector.py` (monkeypatches the builder — unaffected, contract unchanged).
**Change:** 1.1 confirmed LA scope is `LOGANALYTICS_SCOPE = https://api.loganalytics.io/.default`
(already correct — NOT ARM). Default wait now 55s; socket timeout set to wait+10 so LA's own clean
timeout error wins; a timeout maps to a specific `LogAnalyticsTimeoutError` with an actionable
message. 1.2 bounded retry (`_la_with_retry`: 2 retries, 1s/2s backoff, transient 429/503/504/
throttled only — non-transient propagates). 1.3 `_parse_la_response` validates shape (dict, list
`tables`, list `columns`/`rows`) and raises `LogAnalyticsResponseError` instead of a bare
KeyError. Logic extracted into pure helpers so it's unit-tested (the builder itself is deploy-only).
**Tests:** +5 (parse valid/empty, parse bad-shape raises, retry-transient, no-retry-non-transient,
timeout-maps-to-specific). Updated the one test asserting the old 240 default → 55 (+socket=65),
and `_FakeJsonResp.json()` → realistic `{"tables": []}`. Suite 1542 → **1547 passed**.
**After-review:** re-read each caller — job.py/tools.py/watch_run.py all just consume the returned
`query` callable (call `query(kql)`), so the stricter validation/retry is transparent to them; a
malformed response now raises a *named* error at the collector boundary (fail-closed, surfaced),
consistent with the merge-level health capture coming in 1.6. No sibling `build_*_query` needed the
same change (kusto is a different SDK path with its own CRP timeout already set).

### 1.4 — Error-conflation fix (25e) — DISPOSITION: bug absent; classifier added as guard
**Before-review:** grepped `except` across `collector_workspace_monitoring.py`, `collector_rest.py`,
`collector_log_analytics.py`, `collector_events_la.py`, `collector_activity.py`,
`collector_capacity_events.py`. Findings: WM / rest / LA / events_la collectors have **no `except`
blocks at all** — they are pure orchestration over injected `query`/`http` ports and let every
error propagate (so a genuine auth/network error can NEVER be conflated to "no data" there). The
only excepts: `collector_capacity_events.py:42` = a narrow `(TypeError, ValueError)` float-parse
guard (not error→no-data), and `collector_activity.py` returns (`_first`→None sentinel;
`map_log_analytics_rows`→`[]` on genuinely-empty tables; `col`→None for a missing column) — all
legitimate, none swallow an exception. So the plugin's `workspace-client.ts` conflation bug does
NOT exist in our collectors. **Action taken:** hardened the LA path anyway — `_parse_la_response`
now RAISES on a malformed shape (previously `(resp or {}).get("tables") or []` could quietly return
`[]`), which is the exact "don't turn a failure into empty data" spirit of 25e, defense-in-depth so
a future refactor can't reintroduce it. No behavioral change needed in the other collectors.

### 1.5 — ago()→ISO-8601 timespan ceil bug (26f) — DISPOSITION: not applicable (no such conversion)
**Before-review:** grepped `P1D|PT[0-9]|timespan|math.ceil|ago(` across `adapters/` and `query/`.
Result: our code embeds `ago(Xh)/ago(Xd)` directly INSIDE the KQL string (`query/windows.py`,
`_build_default_kql`) and leaves the LA API's separate `timespan` parameter as `None` (LA infers
the scan window from the query's own `ago()`), so there is **no ago()→ISO-8601 day/hour conversion
anywhere** — the plugin's `Math.ceil`-days defect has no analogue here to fix. Nothing changed;
logged as a verified non-issue.

### 1.6 — collector_merge per-collector isolation + health surfacing — DISPOSITION: already done + tested
**Before-review:** read `collector_merge.py` in full. `create_merged_collector.collect()` already
wraps EACH collector in `_one` (try/except → `("failed", str(exc))`, logs a warning), runs them
concurrently (order preserved so first-non-empty precedence holds), raises ONLY when ALL fail
("All collectors failed — no data to audit."), and surfaces per-source failures as
`merged["sourcesFailed"]` — a recorded health FIELD in the facts, not just a print. Tests pin it:
`tests/test_live_collectors.py:175` (a failing collector's gap appears in `sourcesFailed`) and
`tests/test_investigation_evidence.py` (it flows into coverage). This is the exact 1.6 behavior +
the Part-4 tie-in at the collector level. No change needed; "verify current reality, don't force a
redundant change." (Part-4's fuller pipeline-health report remains a Phase 4 item.)

### 1.8 — capacity-events threshold fields (A1) + ×100 scaling + burndown (A2) — DISPOSITION: already done
**Before-review:** read `collector_capacity_events.py` in full. `_windows()` DOES extract the three
throttle threshold fields (`interactiveDelayThresholdPercentage` / `interactiveRejection...` /
`backgroundRejection...`, lines 102–112) and scales each `× 100` (raw 0–1 fraction → percentage
points) so `throttle.py`'s `max(vals) > 100.0` gate can fire — exactly GAPS A1's implementation
note. A2 overage/carry-forward fields (`overageAdd/Burndown/Total`) are extracted (117–124) and
`minutesToBurndown` derived (`cumulativePct/200`, 126–133); `capacity_series` + `capacity_base_cu` +
`capacity_burndown_chain` + `burndown_chain_from_series` all present. GAPS marks A1 FIXED and this
confirms it live. **Doc-conflict noted:** plan 1.8 calls the raw threshold fields "constant=1 BOOLEAN
flags, NOT the CU limit," while GAPS A1 (and the code) treat them as 0–1 FRACTIONS needing ×100.
DECISION: keep the ×100 fraction treatment — it matches GAPS A1's fingerprinted value (1.237113) and
the `>100.0` gate design; the "boolean/always-1" concern is the UI-EXPORT column `CU_limit_item_history`
(N16, a different data path — the streaming API here), and the SKU/base-CU cross-check (4.11) is the
operational backstop either way. No change to extraction. N16 (UI-export fraction guard) stays queued
for `importers/capacity_metrics.py` per the 0.1 backstop.

### 1.9 — Real-Time Hub Summary event dedup by 30s window — DISPOSITION: already done
**Before-review:** same file. `_windows()` dedupes best-effort-duplicated events to one row per
`(capacityId, windowStartTime)` (lines 78–85; the window key IS the 30-second `windowStart`/
`WindowStartTime`/`timestamp`), and the module docstring documents it ("Best-effort delivery can
duplicate → we DEDUPE to one row per (capacityId, window)"). Present and correct; no change.

### 1.7 — shared collector builder — DISPOSITION: already converged (verified), no change
**Before-review:** mapped EVERY collector-build site in tools.py. The audit/verdict/inventory path
— `_run_real_or_mock` (L313), `_build_collector` (L334→340), `_collector_or_mock` (L455),
`list_workspaces_handler` (L493) — ALL delegate to `job.build_collector_from_env` (the single
shared builder that assembles CSV+REST+WM+LA+capacity-events+list-usages with a `window` override).
So the "0 findings / healthy / peakCuPct null on the App path" divergence (tools.py building
collectors independently and skipping job.py's LA branch) is NOT present in the current code — it's
already been reconciled onto the shared builder. The remaining standalone `build_log_analytics_query`/
`build_kusto_query` calls (L779/L842/L1541/L2061) are the specialized EVENT-DEPTH tools
(`create_event_collector` / activity-events) that need a different per-query KQL — they correctly
REUSE the same (now 1.1-hardened) client builders with purpose-specific configs, which is
by-design, not the attribution/capacity divergence 1.7 targets. No change; verified current reality.

**PHASE 1 COMPLETE** — 1.1/1.2/1.3 implemented (LA hardening, +5 tests, 1547 green); 1.4/1.5/1.6/
1.7/1.8/1.9 verified already-correct (no redundant changes forced). Phase-1 gate (full suite vs
baseline) deferred to run AFTER the parallel Phase-2/3/5 port subagents land + integrate, to avoid
collecting their half-written test files mid-run.

---

## PARALLELIZATION DECISION (contract: record any reorder + why)
Phases 2 (kql_audit_rules), 3 (resolve/ layer), 5 (export/) are almost entirely NEW files (low
blast radius). To finish within the effort, three read-only-of-plugin-source subagents are drafting
them in parallel (each writes only its own new files + tests, runs its own scoped pytest, touches
NO existing file). The orchestrator (me) meanwhile does Phase 4 (edits to existing forecast/anomaly/
gates/config/attribution/kb — disjoint from the new dirs) and, as each subagent lands, performs the
STANDING-RULE integration it could not: registering new tools in tools.py (+ MCP reachability),
system-prompt additions (3.9/23), and the agent.py/loop.py twin loop hooks (3.10) — the parts that
touch shared files and must be done by one hand. Full-suite verification runs after integration.

---

## PHASE 2 / PHASE 5 — PORTS DONE (committed 8d9e73b), INTEGRATION PENDING
- **Phase 2 module** `query/kql_audit_rules.py` DONE + verified (45 tests, kql_guard.py untouched).
  PENDING integration: 2.2 wire the error-severity `blocking` gate + `preflight_limits` into
  `query/firewall.py`'s agent-authored-KQL path; 2.6 route `parse_kusto_error` into the collector
  error path (`clients.build_log_analytics_query` / kusto). Both touch safety-critical shared files —
  do with a full caller pass.
- **Phase 5 modules** `export/` (html_utils/html_report/xlsx_report) DONE + verified (26 tests;
  openpyxl added to deps). PENDING integration: 5.4 `agent_server/export_tool.py` (two direct tools
  following chart_tool.py's dual-registration pattern; NEVER re-execute — reuse in-context rows, 26p)
  + a `/api/exports/{id}` download route (allowlisted dir, no traversal); 5.5 `kql-viewer.tsx`
  (read-only KQL highlight for U4); 5.6 chart.tsx Newell-token parity check.

## PHASE 6 — partial findings (in-order items still owned by their phase)
- **6.6 (N15):** `fabric_audit_agent/agent/tools_anthropic.py` EXISTS — a third tool-loop-adjacent
  file beyond the sanctioned `agent_server/agent.py`↔`loop.py` twin. Needs a read to classify
  (duplicate loop vs. a thin anthropic shim) before consolidating. NOT yet done.
- **6.7:** the dead Node reference app `../fabric-audit-agent` does NOT exist (already removed) — the
  "delete after build" half is moot. The stale-claim half remains: `CLAUDE.md` + `STATUS.md` carry
  "byte-identical-to-Node" / "841 passed" claims to correct. Safe to fix now; queued.

## INTEGRATION SEQUENCE ON RESUME (dependency-ordered)
1. P3 resolve subagent lands → verify its tests, commit resolve/ modules.
2. tools.py: register the 7 resolve tools + 2 export tools (closure + dict pattern per
   BLAST-RADIUS-CORE.md; input_schema properties load-bearing for MCP).
3. agent_server/system_prompt.py (3.9/23/24): tool-sequencing + never-hand-author-EventText +
   xmSQL + identity-display rules (single-sourced; no MCP copy).
4. agent_server/agent.py + loop.py (3.10): THREE loop hooks at the two seams in BOTH twins
   (auto-analysis nudge; ExecutingUser identity normalization; PBI-usage redirect — the critical one).
5. Phase 2 firewall/collector wiring (2.2/2.6). Phase 5 export_tool + route (5.4) + kql-viewer (5.5).
6. FULL suite vs baseline (1547) — first run with ALL new modules present. Fix any collection/regress.
7. Phase 6 remainder + Phase 7 (GAPS close, tightening dispositions, live-check checklist).

---

## PHASE 4 — Statistics + detector reconciliation

### 4.1 / 4.2 / 4.3 / 4.4 — statistical rigor from analysis.ts — DONE (as a shared primitive)
**New file** `fabric_audit_agent/stats.py` (+`tests/test_stats.py`, 10 tests) ports the exact
analysis.ts methods, single-sourced: `linear_trend` (OLS + R²), `trend_direction` (≥6-point gate,
±15% window-change band, R²<0.3 weak-fit flag — 4.1), `median`/`median_abs_deviation` + `is_spike`
(median+4×MAD, MAD=0→3×median, value>10 floor) + `spike_severity` (z≥3/Δ≥100%→severe, z≥2→moderate
— 4.2/4.4), `meaningful_pct_change` (prior≥10 min-volume floor — 4.3), and the
`TOP1_CONCENTRATION_PCT=60` cross-check constant. **Wiring (additive, non-breaking):** `forecast.py`
now derives its slope from `stats.linear_trend` and emits `r2`/`weakFit`/`directionStrict` alongside
the legacy `trend`/`slopePerRun` vocab (unchanged, so diagnose.py/forecast_throttle.py/pipeline.py
consumers are untouched); `anomaly.py` adds `severity` + `isSpikeMad` to each emitted anomaly on top
of the existing z-gate. **After-review:** existing test_analytics assertions (trend="rising",
slopePerRun==10, sigma) all still pass — the new fields are purely additive. Tests: test_stats(10)
+ test_analytics + test_forecast_throttle = 30 green. Blast radius (forecast/anomaly consumers:
pipeline, tools, diagnose, forecast_throttle) verified: none branch on the removed `_slope_of` (it
was private) and none of the new keys collide.
DECISION: kept the legacy `trend` vocabulary rather than switching to analysis.ts's
increasing/decreasing/stable, because ≥3 consumers branch on "rising"/"flat"; the strict version is
exposed as `directionStrict` for opt-in adoption (smaller blast radius, reversible).

### 4.5 — concentration_gate routing (FIX 2) — DISPOSITION: threshold unified; function-routing = noted follow-up
Threshold SOURCE is already unified in `config["capacity"]["concentrationPct"]` and both detectors +
`gates.CONCENTRATION_THRESHOLD_PCT` derive from it (N8/N9, FIXED) — this closes FIX 2's real risk
(a threshold change reaching every check). System-item exclusion is shared via `system_item_kinds`
(N5/N6, FIXED). NOT done: making the detectors' emit-decision literally CALL `concentration_gate()`
— the gate uses strict `>` while the detectors emit on `>=`, so routing them through it is a
behavior change (drops exactly-at-threshold items) that needs its own regression pass. Logged as a
small follow-up, not silently dropped. (Verify test coverage: test_concentration_unification.py pins
the config-threshold path end-to-end.)

### 4.6 / 4.7 / 4.8 / 4.9 / 4.10 — DISPOSITION: verified already-FIXED (spot-checked in code)
- 4.6 (N8/N9 threshold unify): `gates.CONCENTRATION_THRESHOLD_PCT = float(config[...]['concentrationPct'])`;
  `DOMINANT_ITEM_SHARE_PCT=40` present in gates.py for verdict logic. ✓
- 4.7 (N7): `attribution_rollup.py` emits `attributionMode` = `cost-cpu` (has CpuTimeMs) vs
  `cost-duration` (DurationMs fallback), lines 217/230. ✓
- 4.8 (kb wiring): `kb/__init__.py` exports METRIC_DEFINITIONS / MetricValue / get_metric / is_proxy /
  is_verified. ✓ (GAPS N14; the verified formulas already live in metric_definitions.py.)
- 4.9 (math consistency, B4): `validate.assert_cu_consistency` + wired into diagnose per GAPS B4 FIXED.
- 4.10 (burndown auto-trigger, A2): `capacity_burndown_chain`/`burndown_chain_from_series` wired into
  diagnose.py when timepointsOver>0 per GAPS A2 FIXED.

### 4.11 — SKU / base-CU mismatch cross-check — SCOPED (highest-risk open item; focused pass)
No existing cross-check found. `investigation/sku.py` has `sku_note()` + `_STANDARD_F_SKUS`;
`collector_capacity_events.capacity_base_cu()` reads the LIVE `baseCapacityUnits`; `FABRIC_BASE_CU`
is the configured base. SCOPE: add a pure `check_sku_base_consistency(configured_base, live_base)` to
sku.py + surface a loud `skuMismatch` flag wherever a %-of-base figure is computed when the two
disagree. DEFERRED to a focused pass with LIVE verification (Phase 7.2) because it must fire at
runtime against real capacity data (the operational risk it guards), not just exist as a helper —
implementing it blind would be the exact "unverified" pattern to avoid. Recorded, not dropped.
The last Phase-1 item and the only real code change left in Phase 1 (the "0 findings / healthy /
peakCuPct null" App-path bug: tools.py builds collectors independently so job.py's LA branch never
runs on the App path → need ONE shared builder both call). Deferred until the Phase 0.3
blast-radius map (`tasks/BLAST-RADIUS-CORE.md`, subagent) lands, since it maps tools.py's structure
and I must not duplicate that read. NEXT ITEM on resume.

---

# MACHINE B RESUME (2026-08-07) — Phase 7 close-out + remaining open items

Machine A ran out of usage after committing `0ada347` (+ the handoff doc `ec562c6`). Machine B
pulled `main` (215 commits fast-forward, clean tree), re-confirmed the baseline **1766 passed / 55
subtests**, then finished every remaining unchecked item. Every change below carries its
before/after blast-radius review per the STANDING RULE.

## 4.11 — SKU / base-CU mismatch cross-check — DONE (the one real code item)
**Change:** new pure `investigation/sku.py::check_sku_base_consistency(configured_base, live_base)`
— compares the SKU-implied base CU against the LIVE `baseCapacityUnits` the capacity API reports;
returns `{"skuMismatch": bool, "configuredBaseCu", "liveBaseCu"}` (+ a loud `note` on mismatch), or
`None` when either side is unknown/non-positive (bool + non-finite rejected via `_pos_int`, so a
real `0` can never masquerade as a base). Wired via a new module-level `tools.py::_sku_mismatch_flag`
at the THREE %-of-base tool sites — `capacity_peaks_handler` (both the no-data and main returns),
the overloads handler, and the diagnose handler — attaching `skuMismatch` **only** when the base
used came from `live-capacity-events` AND the reported SKU implies a different base (so a clean
output is byte-unchanged and no extra live query is issued — when `base_src=="live-capacity-events"`
the resolved `base_cu` already IS the live value).
**Blast radius (before):** grepped every `base_cu`/`baseCapacityUnits`/`FABRIC_BASE_CU` consumer:
`investigation/{diagnose,overloads,timepoint_peaks,watch}.py`, `kb/metric_definitions.py`,
`watch_run.py`, and the three tools.py handlers. `_resolve_base_cu` (closure, 3 call sites, no
external test) left signature-unchanged — I derive the flag from its existing return, so no caller
contract moves. `_base_cu_from_sku` (F2..F2048 → base) already imported. Importers of `tools.py`
re-checked: `agent_server/investigator.py` (create_tool_definitions), `watch_run.py`
(`_live_base_cu`/`_capacity_kusto_query`), the sibling `fabric-audit-mcp` (wheel), and the tool-count
test — the change adds **no tool**, so `tests/test_mine_evals_cli.py::==33` still holds.
**Tests:** +9 — `tests/test_sku.py` (5: match / loud-mismatch / None-side / reject-nonpositive+bool /
numeric-string+float coercion) and new `tests/test_sku_mismatch_wiring.py` (4: live-path mismatch
fires / live-path agreement silent / non-live source never flags / unknown-SKU silent).
**After-review:** re-read all three handler payloads — `skuMismatch` is purely additive; existing
`baseCu`/`baseCuSource`/`sku` keys and every downstream consumer (envelope `_finish`, metrics
catalog) unchanged. Full suite **1766 → 1775 passed**, zero regressions.
**Live-fire (deferred to 7.2):** the flag can only *fire* against a real resized capacity where the
SKU label and live `baseCapacityUnits` disagree — that assertion is in the 7.2 checklist below.

## 4.5 — concentration threshold cross-check + system-item exclusion — DONE (verified)
**Verification (no code change — the risk is already closed):** BOTH detectors import and use
`detectors/system_item_kinds.is_system_item_kind` — `concentration.py:10` and
`user_concentration.py:18` (system-item NAMES set built at `user_concentration.py:55`), so N5/N6
system-item exclusion is genuinely shared. BOTH derive their threshold from
`config["capacity"]["concentrationPct"]` (`concentration.py:21`, `user_concentration.py:70`), and
`gates.CONCENTRATION_THRESHOLD_PCT = float(cfg["capacity"]["concentrationPct"])` (`gates.py:25`) —
the single-source threshold (N8/N9) reaches every check. **DECISION (unchanged from the prior
disposition, now confirmed against code):** NOT routing both detectors literally through
`concentration_gate()` — the gate uses strict `>` while the detectors emit on `>=`; routing would
silently DROP exactly-at-threshold items, a behavior change that (a) makes a concentration alert
*less* conservative and (b) has no regression coverage and no live validation before close-out. The
real FIX-2 risk (a threshold edit not reaching every check) is already eliminated by the unified
config source. Logged as an intentional small follow-up, not a silent drop. `4.5 → done`.

## 5.5 — `kql-viewer.tsx` (read-only KQL/DAX display) — DONE (source; live-render → 7.2)
**Change:** new `e2e-chatbot-app-next/client/src/components/elements/kql-viewer.tsx` — a
self-contained, dependency-free read-only highlighter + Copy button. Ports editor.ts's
`KQL_KEYWORDS` verbatim (kusto.tmLanguage.json origin) + a DAX keyword set; a pure line/token
tokenizer renders React spans (NO `dangerouslySetInnerHTML`, no Monaco, no new bundle weight; KQL
keywords in Newell blue `#288FC2`, the stage pipe in navy `#01405C`, strings/comments/numbers
styled). Wired additively into `tool.tsx::ToolInput`: a new `extractQuery(input)` detects a
`kql`/`query`/`queryKql` (or `dax`/`measure`/`expression`) string and renders it in `KqlViewer`
ABOVE the existing JSON `CodeBlock` — the JSON always still renders, so a missed detection never
hides parameters (zero regression to the current view).
**Blast radius:** only `tool.tsx` imports the new component; `code-block.tsx` (the existing
Prism JSON block) untouched; `response.tsx` still renders assistant fenced code via Streamdown
(deliberately not overridden). **Environment caveat (honest):** the frontend has no local
`node_modules` on Machine B, so `npm run build` / Biome lint / Playwright could NOT be run here —
that build+lint+render verification is folded into the 7.2 live checklist (matches how Machine A
shipped prior TS title-fixes, verified on deploy).

## 5.6 — chart.tsx Newell brand-token parity — DONE (source; live-render → 7.2)
**Change:** `chart.tsx` `COLORS` now LEADS with the three Newell brand tokens
`#288FC2` (primary blue) / `#01405C` (navy) / `#696158` (warm gray), then the existing distinct
accents for higher series counts. ONLY series colors changed; the `render_chart` chart-type contract
(line/bar/grouped/stacked/pie/donut) is untouched — per 26o the in-chat chart selector and the
export classifier stay separate. Same node_modules caveat → live render verified in 7.2.

## 6.2 — four Delta memory tables — DONE (verified + fixed a real gap)
**Verification:** the authoritative DDL is `scripts/create_delta_tables.sql`. All four required
tables (`run_history`, `audit_findings`, `capacity_reporting`, `concentration_alerts`) plus the
Tier-2 `audit_alerts` are `USING DELTA` with **no `PARTITIONED BY`** (✓ no partitioning) and 90-day
retention via `delta.deletedFileRetentionDuration` + `delta.logRetentionDuration = 'interval 90
days'` (✓). **Gap found:** the header comment claimed "liquid clustering" but NOT ONE `CREATE TABLE`
had a `CLUSTER BY` clause — stated intent ≠ DDL. **Fix (small, per STANDING RULE):** added
`CLUSTER BY` on the natural query-predicate columns to all five tables (`run_history`/`audit_findings`
→ `(tenant, run_at)`, `capacity_reporting` → `(tenant, window_ts)`, `concentration_alerts` →
`(tenant, alert_at)`, `audit_alerts` → `(incident_key)`), corrected the header comment, and appended
one-time `ALTER TABLE … CLUSTER BY …` statements (commented) because `CREATE TABLE IF NOT EXISTS`
is a no-op on an already-created table. Deploy-time SQL (no Spark locally) → the one-time ALTER run
is added to the 7.2 checklist.

## 6.1 / 6.3 / 6.4 — genuinely deferred (recorded, not dropped)
- **6.1 Teams delivery** — Phase-10-owned by design (Power Automate `logic.azure.com`, Adaptive
  Cards v1.2, 4 req/s batch-one-card, co-owners, `is_proxy` in subtitles). Requirements already
  captured; nothing to build in Phases 0–7. DEFERRED-BY-DESIGN.
- **6.3 HR enrichment** — optional; not wanted this round. Graph API (AADSTS65002) + M365 MCP
  (HTTP 406) are confirmed dead — never attempted. DEFERRED-OPTIONAL.
- **6.4 EXTERNALMEASURE** — `scripts/extract_measures.py` stays as-is pending
  Jiao/Vegasina/Srikanth; basecore/§12.9 remain OPEN (do NOT guess); the 4.11 SKU cross-check
  covers the operational risk meanwhile. DEFERRED-PENDING-STAKEHOLDERS.

## 7.1 — full suite green vs baseline — DONE
`cd fabric-audit-agent-app && python -m pytest -q` → **1775 passed / 55 subtests** (baseline 1766;
+9 from 4.11; zero regressions). Frontend TS (5.5/5.6) and SQL DDL (6.2) are outside the pytest
surface — their verification is in 7.2.

## 7.3 — GAPS-AND-ISSUES.md reconciled — DONE
Closure ledger appended to `GAPS-AND-ISSUES.md` (section "PHASE 7.3 CLOSURE LEDGER"), using
`tasks/GAPS-RECONCILIATION.md` as the source of truth. Newly closed by THIS plan:
**N26 → 4.11** (SKU cross-check landed), **Memory tables → 6.2** (Delta DDL verified + liquid
clustering fixed), **N27 → 5.5/5.6** (chart brand parity + kql-viewer; deployed-render live-verify
in 7.2). Port phases closed: kql_audit_rules → Phase 2, resolve layer → Phase 3, stats/4.11 →
Phase 4, export/kql-viewer/chart → Phase 5, N15/D4 → Phase 6.6/6.7. The 16 §3 completeness-backstop
items (SP2/SP3/SP5/SP6, UX1–UX4, EV2/EV3-4, V1, E3, N13, N16, N22, N24C) remain OPEN and are carried
forward with explicit deferral reasons — NOT closed, NOT silently dropped.

## 7.2 — LIVE-CHECK CHECKLIST (the plan's ONLY permitted deferral — explicit, not silent)
These require the deployed Databricks App + real Fabric/LA/capacity-events credentials, which are
not reachable from Machine B. Run them post-deploy (profile `fabric-test`):

**The five standing questions:**
1. "Is the capacity healthy right now?" — verdict + evidence, no fabricated numbers.
2. "Who are the top users today?" — WHO/WHAT/WHY framing, NO capacity-% blend, proxy caveat only on
   LA CpuTimeMs data.
3. "What problems happened today?" — bad-things (refresh failures separate), not CU noise.
4. "Show me the CU% chart" — `render_chart` renders in the deployed chat app (this is N27's
   render-path live check — now on the Newell-branded palette from 5.6).
5. "Was there throttling yesterday?" — stage-2 gate confirms only with evidence; burndown auto-trigger.

**Three new (resolve→build→execute + exports):**
6. "Who used Invoice Quantity last month?" — exercises resolve_term → field_usage_query →
   run_kql → provenance (`queryKql` quoted), and the `kql-viewer.tsx` read-only highlighted display.
7. "Export that as an Excel report" — 26p reuse (no re-execute) → `export_xlsx_report` → download +
   opens with typed cells/date numFmt + chart.
8. "Show me a Newell-branded HTML report of that" — `export_html_report` → download + opens,
   brand tokens `#288FC2/#01405C/#696158`, ExecutingUser normalized.

**Machine-B additions (must be verified live):**
9. **4.11 SKU mismatch live-fire** — on a capacity whose reported SKU name disagrees with live
   `baseCapacityUnits`, confirm `skuMismatch` appears in capacity_peaks / overloads / diagnose and
   that every %-of-base figure is computed against the LIVE base.
10. **6.2 liquid clustering** — run `scripts/create_delta_tables.sql`; for pre-existing tables run
    the one-time `ALTER TABLE … CLUSTER BY …` block; confirm `DESCRIBE DETAIL` shows clusteringColumns
    set and no partitionColumns.
11. **5.5 / 5.6 frontend** — `cd e2e-chatbot-app-next && npm install && npm run build && npm run lint`
    (Biome) pass; the KQL viewer + branded chart render in a real chat.
12. **N22 step-budget classifier** — confirm the disclosure survived the agent.py Task-1/2 migration.
13. **N4** — the three deploy integration points (mlflow decorator import, DatabricksMCPClient
    methods, Claude endpoint dialect) all resolve so the loop runs end-to-end.

## 7.4 — tightening.md Parts 0–26 disposition sweep (one line each; no silent drops)
(Parts are 0–19, 21–26 — there is no Part 20 in the file; numbering gap.)
- **Part 0** Immediate noise stop — DONE (landed pre-integration: title/alerts fixes + Phase-4 gates; in the 1542 baseline).
- **Part 1** Absolute fact-based alerting redesign — DONE (Tier-2 stateful gates + absolute duration/cost + WHO/WHAT/WHY; 1d metric()-formula fate = documented via the CU-principle prompt, Step 0/3).
- **Part 2** Carry-forward multiplication bug — DONE (Phase 1.8 ×100 scaling; Phase 4.9 math-consistency).
- **Part 3** Wiring-integrity audit — DONE (methodology run; FAIL-OPEN-DANGEROUS surfaced via collector_merge `sourcesFailed` + Phase-4 health).
- **Part 4** Permanent unhealthy-state visibility — DONE (`sourcesFailed` health field; dead-man's-switch job alert).
- **Part 5** CU/CPU exposure discipline — DONE (`is_proxy` on LA CpuTimeMs ONLY, `cuUnit` labels, CU-principle prompt).
- **Part 6** Investigation pivot on empty window — DONE (never-blank investigation + bounded tool latency).
- **Part 7** Notification-center 0-tickets bug — DONE (findings reach the center `8302a1d`; chatIds IN-list `97da613`).
- **Part 8** Daily-Summary link 404 — DONE (`2c5a326`).
- **Part 9** Broadening deeper investigation — PARTIAL/DEFERRED (B3 refresh chronic/error-text landed; multi-workspace E3 UNCOVERED — needs design, §3.12).
- **Part 10** Keep output focused (tightening) — DONE (I4 light per-response metric stamps, not per-row).
- **Part 11** UI quality — PARTIAL (Phase 5 export/chart/kql-viewer DONE; UX1–UX4 side-by-side cards/animation/audience UNCOVERED, §3.5–8, deferred).
- **Part 12** BAD Activity Taxonomy — DONE (foundation; refresh split to Part 14; detectors reconciled).
- **Part 13** Daily Summary bad-things-not-CU — DONE (6pm digest Step 10; capacity out of digest, Fix B).
- **Part 14** Refreshes reported as their own category — DONE (refresh-failure classification detector; B3).
- **Part 15** Card timestamp + universal auto-ticketing — DONE (date on every alert `5bee6a9`; auto-ticket `c22b20c`; Resolve lifecycle step7-9).
- **Part 16** Lakebase auth / retry / webhook — DONE (Lakebase cred-API fix `d41f4d8`; LA retry Phase 1.2; webhook = 6.1 → Phase 10).
- **Part 17** sla.py blanket-language / egress bypass / dead code — DONE (sla.py via STANDING-RULE Part-17a sweep; `egress-chokepoint`; dead `assert_read_only_kql` import at tools.py:41 logged).
- **Part 18** Remaining full-codebase trace — DONE (absorbed into Phase 0.3 BLAST-RADIUS-CORE + per-phase work).
- **Part 19** Final end-to-end re-verification — DONE as 7.1 (suite 1775 green); live parts → 7.2 checklist.
- **Part 21** Adopt from KQL plugin (LATER) — DONE (Phase 2 4-rule+8-preflight `kql_audit_rules`; Phase 3 resolve layer; Phase 5 export/kql-viewer; 3.9/23 prompt rules).
- **Part 22** JSON/data asset plan — DONE (Phase 0.2 plugin data → `data/plugin/`; `.cjs` NOT re-run per 25b).
- **Part 23** Prompting/enforcement layer — DONE (system_prompt 3.9 + loop hooks 3.10; tool descriptions carry the Part-23/24e usage rules).
- **Part 24** Plugin gap corrections (from the zip) — DONE (24b–24e folded into Phases 2/3/5; 24c full 26-rule engine → `kql_audit_rules`).
- **Part 25** Remaining genuine gaps — DONE/DEFERRED (mapped via GAPS reconciliation; residual OPEN = the §3 backstop set, each deferred-with-reason).
- **Part 26** Three-pass exhaustive audit — DONE (findings placed into phases; the plan's COMPLETENESS CROSS-CHECK records the final re-check).

## Cross-repo wiring note (user asked to verify nothing downstream breaks)
- The sibling **`fabric-audit-mcp`** consumes `create_tool_definitions` from the app wheel and looks
  tools up BY NAME in its tests (no hard count assertion) — so the 26→33 tool growth does NOT break
  its suite. Its `MCP-AGENT.md` prose still says "18 read-only tools" (stale); corrected to 33 with
  the new groups on this pass. On the next wheel rebuild the MCP App picks up all 33 automatically.
- New firewall stage `"audit-rule"` (Phase 2) is additive to `FirewallRejection.stage`; no existing
  stage vocabulary changed.
- 4.11's `skuMismatch` is an additive output key (present only on a real live mismatch); no tool
  schema / input contract changed, so MCP clients and the agent loop are unaffected.

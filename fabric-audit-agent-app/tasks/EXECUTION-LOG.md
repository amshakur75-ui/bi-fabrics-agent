# EXECUTION LOG — master-integration-plan.md autonomous run

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

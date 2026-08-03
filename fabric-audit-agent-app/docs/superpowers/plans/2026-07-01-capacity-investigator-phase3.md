# Capacity Investigator — Phase 3 (Event Depth & Patterns) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement **Part A** task-by-task. **Part B** is a live-data runbook (work machine). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the agent's aggregate snapshots ("49% average across 419 users") into **event-level depth + temporal patterns** — per-user spike history with counts/times, interactive-vs-refresh split, refresh collisions, the specific expensive queries, and coupled "surge → spike" narratives — served through the existing read-only MCP, over the data it already reads (Log Analytics + Capacity Events), with an optional **FUAM** track for authoritative CU + owner.

**Architecture:** All new analysis operates on a **normalized event list** (`{ts, user, item, workspace, operation, kind, cuSeconds, durationMs, throttled}`) so the *logic* is pure and offline-testable (Part A, here) while the *live queries* that produce that list are thin adapters (Part B, work machine). Reuses the Phase-1 `baseline.py` (percentiles) — a "spike" is an event above the entity's own p95 **or** an absolute floor. No ML; patterns are deterministic time-bucket correlation. Detectors still ground the LLM; these are new **read-only** tools, not new reasoning.

**Tech Stack:** Python ≥3.10 stdlib only for the core; pytest. Reuses `fabric_audit_agent` (functional core + dict-style ports). Live (Part B): KQL over `PowerBIDatasetsWorkspace`, Capacity Events, and (optional) FUAM via the Lakehouse SQL endpoint.

## Spec sources
- This session's design thread (event-depth + patterns; LA is the workhorse; FUAM = authoritative CU + owner).
- `research/agent-arch/10-rerun-verdict.md` (detectors-ground-LLM, targeted-not-pull-all, metric layer, runbooks).
- LA schema: `PowerBIDatasetsWorkspace` (`OperationName` QueryEnd/CommandEnd/ProgressReportEnd, `EventText`, `CpuTimeMs`, `DurationMs`, `ExecutingUser`, `ArtifactId`, `TimeGenerated`). FUAM: `capacity_metrics_by_item_by_operation_by_day` + inventory.

## Global Constraints
- **Read-only is absolute.** New tools read and return; nothing persists on the interactive path.
- **Targeted, not pull-all.** Live tools take a scope (a user, an item, a time window) — never "dump every event for every user." Heavy whole-capacity pattern mining is the pre-compute **Job's** role (Part B seam), not a live request (~120s Apps timeout).
- **Honesty:** round percentages (no 15-decimal noise); label monitored-CU (proxy) vs authoritative-CU (FUAM/Capacity Metrics); never emit a specific SKU the tool didn't return; flag non-standard SKU names (e.g. `FTL64`) as "verify trial capacity — size-up may not apply."
- **camelCase data keys / snake_case identifiers. stdlib-only core.** Full suite green after every task (`cd fabric-audit-agent-py && python -m pytest -q`; baseline 347 passed, 1 skipped).
- **No overengineering:** reuse `baseline.py`; one normalized event shape; deterministic patterns. Add only what a tool returns.

## Phase-3.5 / Phase-4 menu (noted, NOT built here — avoid scope creep)
Non-dataset CU (Spark/pipelines via Workspace Monitoring), Entra sign-in logs (true "login" surges vs the activity-surge proxy), REST Scanner (idle/over-provisioned inventory), Azure Retail Prices ($ verdict). Flagged where relevant; deferred.

---

# Part A — Event/pattern logic + new tools (offline, TDD, build machine)

### Task 1: The normalized event + spike definition (metric layer)
**Files:** Create `fabric_audit_agent/investigation/events.py`; Test `tests/test_events.py`
**Interfaces:** Produces `normalize_event(row) -> dict` (tolerant of LA/Eventhouse column spellings → `{ts, user, item, workspace, operation, kind, cuSeconds, durationMs, throttled}`; `kind` = `"refresh"` if operation in the refresh set else `"interactive"`; `cuSeconds` from CpuTimeMs→DurationMs fallback ÷1000); `is_spike(event, *, p95, floor_cu) -> bool` (above the entity p95 OR the absolute floor).

- [ ] **Step 1: failing test**
```python
# tests/test_events.py
from fabric_audit_agent.investigation.events import normalize_event, is_spike

def test_normalize_classifies_refresh_vs_interactive_and_cost():
    q = normalize_event({"TimeGenerated": "2026-06-30T19:57Z", "ExecutingUser": "x@co",
                         "ArtifactName": "Sales", "OperationName": "QueryEnd", "CpuTimeMs": 9000})
    assert q["kind"] == "interactive" and q["cuSeconds"] == 9.0 and q["user"] == "x@co"
    r = normalize_event({"OperationName": "CommandEnd", "DurationMs": 4000, "Identity": {"Email": "y@co"}})
    assert r["kind"] == "refresh" and r["cuSeconds"] == 4.0 and r["user"] == "y@co"  # CpuTimeMs absent -> DurationMs

def test_is_spike_relative_or_absolute():
    assert is_spike({"cuSeconds": 100}, p95=50, floor_cu=1000) is True     # above p95
    assert is_spike({"cuSeconds": 1200}, p95=99999, floor_cu=1000) is True  # above absolute floor
    assert is_spike({"cuSeconds": 10}, p95=50, floor_cu=1000) is False
```
- [ ] **Step 2:** run → FAIL (ModuleNotFound). **Step 3:** implement `events.py`:
```python
# fabric_audit_agent/investigation/events.py
"""Normalized capacity event + spike definition (the metric layer for 'a spike'). Pure/stdlib.
One event shape so all Phase-3 analysis is source-agnostic + offline-testable."""
_REFRESH_OPS = {"CommandEnd", "ProgressReportEnd", "Refresh", "CommandBegin"}

def _identity_email(row):
    ident = row.get("Identity")
    if isinstance(ident, dict):
        return ident.get("Email") or ident.get("email")
    return row.get("ExecutingUser") or row.get("user") or row.get("User")

def normalize_event(row):
    cpu = row.get("CpuTimeMs")
    dur = row.get("DurationMs")
    ms = cpu if cpu is not None else dur
    op = row.get("OperationName") or row.get("operation") or ""
    return {
        "ts": row.get("TimeGenerated") or row.get("Timestamp") or row.get("ts") or "",
        "user": (_identity_email(row) or "").lower() or None,
        "item": row.get("ArtifactName") or row.get("ItemName") or row.get("item"),
        "workspace": row.get("PowerBIWorkspaceName") or row.get("WorkspaceName") or row.get("workspace"),
        "operation": op,
        "kind": "refresh" if op in _REFRESH_OPS else "interactive",
        "cuSeconds": round((ms or 0) / 1000.0, 3),
        "durationMs": dur,
        "throttled": bool(row.get("throttled")),
    }

def is_spike(event, *, p95, floor_cu):
    cu = event.get("cuSeconds") or 0
    return (p95 is not None and cu > p95) or (floor_cu is not None and cu >= floor_cu)
```
- [ ] **Step 4:** run → PASS. **Step 5:** commit `feat(phase3): normalized event + spike definition (metric layer)`

### Task 2: Per-user spike history + counts
**Files:** Create `fabric_audit_agent/investigation/spike_history.py`; Test `tests/test_spike_history.py`
**Interfaces:** Consumes `events.py`, `baseline.compute_baseline`. Produces `user_spike_history(events, user, *, floor_cu=0) -> dict` → `{user, spikeCount, totalCuSeconds, peakCuSeconds, spikes:[{ts,item,operation,kind,cuSeconds}], topItems, byHour, interactiveVsRefresh}` — every spike event + counts + time-of-day + interactive/refresh split. Baseline p95 computed from the user's own events.
- [ ] Steps: TDD — inject a user's events (some above p95), assert `spikeCount`, the `spikes` list carries per-event ts/item/cost, `byHour` distribution, `interactiveVsRefresh` totals. Minimal impl computes baseline over the user's `cuSeconds`, filters `is_spike`, aggregates. Commit `feat(phase3): per-user spike history + counts + time-of-day + workload split`.

### Task 3: Interactive-vs-refresh split + refresh-collision
**Files:** Create `fabric_audit_agent/investigation/workload.py`; Test `tests/test_workload.py`
**Interfaces:** `split_workload(events) -> {"interactiveCuSeconds","refreshCuSeconds","interactivePct"}`; `refresh_collisions(events, *, peak_start, peak_end) -> [{item, ts, cuSeconds}]` (refresh events whose ts falls in the peak window). Closes the agent's own "not checked: interactive vs refresh" gap.
- [ ] TDD: events with mixed kinds → split totals correct; a refresh inside the peak window → surfaced by `refresh_collisions`. Commit `feat(phase3): interactive-vs-refresh split + refresh-collision detection`.

### Task 4: Expensive-query surfacing
**Files:** Create `fabric_audit_agent/investigation/expensive.py`; Test `tests/test_expensive.py`
**Interfaces:** `top_expensive(events, *, n=5) -> [{ts,user,item,cuSeconds,queryText}]` — the costliest individual query events, with `queryText` from EventText truncated to ~400 chars and never presented as an instruction (spotlight-safe). Read-only.
- [ ] TDD: ranks by cuSeconds desc, truncates queryText, top-n. Commit `feat(phase3): expensive-query surfacing (the specific costly DAX)`.

### Task 5: Temporal pattern engine (surge ↔ spike ↔ driver)
**Files:** Create `fabric_audit_agent/investigation/patterns.py`; Test `tests/test_patterns.py`
**Interfaces:** `capacity_patterns(events, capacity_series, *, bucket_minutes=15) -> [{windowStart, activeUsers, cuPeakPct, drivingItem, drivingUser, kind, narrative}]`. Buckets events by time; per bucket computes distinct active users (concurrency proxy) + the top driving item/user; joins to the capacity CU% series; emits a "coupled" narrative when an activity surge precedes/coincides with a CU spike. Deterministic (no ML). `capacity_series` = `[{ts, cuPct}]` from Capacity Events.
- [ ] TDD: inject a burst of many distinct users at bucket T + a CU spike at T (or T+1) → one pattern with `activeUsers` high, `drivingItem` set, and a narrative naming both. A quiet period → no pattern. Commit `feat(phase3): temporal pattern engine (activity surge ↔ CU spike ↔ driver)`.

### Task 6: Honesty hardening (from the live-output critique)
**Files:** Modify `fabric_audit_agent/detectors/severity.py` + `detectors/concentration.py` (or wherever the share strings render) + Create `fabric_audit_agent/investigation/sku.py`; Tests alongside.
**Interfaces:** `round_pct(x) -> float` (1 decimal) applied to all share/percent output (kills `49.213063380823705`); `sku_note(sku) -> str|None` → returns a "verify trial capacity — size-up may not apply" note when the SKU isn't a standard `F2..F2048` name (e.g. `FTL64`). Wire `sku_note` into the capacity verdict so a non-standard SKU is flagged. Do NOT let any tool emit a specific target SKU name it wasn't given.
- [ ] TDD: `round_pct(49.213063380823705) == 49.2`; `sku_note("FTL64")` returns a note; `sku_note("F64")` returns None. Wire + assert the verdict carries the note for FTL64. Commit `fix(phase3): round percentages + flag non-standard/trial SKU in the verdict`.

### Task 7: New MCP tools (user_spike_history, spike_events, capacity_patterns)
**Files:** Modify `fabric_audit_agent/tools.py` (+ `mcp_server.py` registration if needed); Test `tests/test_mcp_tools.py`
**Interfaces:** add read-only tools:
- `user_spike_history` — `{user, days}` → Task-2 output for that user (offline: from mock events).
- `spike_events` — `{days, topN}` → ranked spike events across the estate (Task-1/2), each with user/item/ts/cost (NOT averages).
- `capacity_patterns` — `{days}` → Task-5 patterns.
Each sources events from a `_events_or_mock()` helper (live event collector when configured, else a small mock event fixture). Handlers labeled `source: live|mock` (reuse `_has_live_source`).
- [ ] TDD: the three tools are defined with input_schema; offline handlers return the shaped output from mock events; existing tools unaffected. Commit `feat(mcp): user_spike_history + spike_events + capacity_patterns tools`.

### Task 8: Eval + runbooks
**Files:** Add golden cases to `eval/agent_cases.json` (+ scorer if needed); Create `docs/runbooks/` (`throttle-investigation.md`, `noisy-neighbor.md`, `refresh-collision.md`); Test `tests/test_eval_agent.py`.
**Interfaces:** golden agent cases that call the new tools and must ground the answer (a spike figure/entity traces to a tool result); the three runbooks (Goal → which tools to call → how to synthesize → what-would-confirm) that the agent/prompt can follow.
- [ ] TDD: `run_agent_suite` still all-pass with the new grounded case; the runbook files exist and name the new tools. Commit `feat(phase3): agent golden case + investigation runbooks for the new depth`.

---

# Part B — Live data + FUAM + the Job seam (runbook, work machine)

> Not offline-testable — run on the Databricks/Fabric side. Verify exact columns against the live schema before shipping.

- [ ] **B1 — LA event collector:** KQL over `PowerBIDatasetsWorkspace` returning the raw rows `normalize_event` expects — `project TimeGenerated, ExecutingUser, ArtifactName=ArtifactId lookup, OperationName, CpuTimeMs, DurationMs, EventText` filtered to a scope (user / item / window). `QueryEnd`→interactive, `CommandEnd`/`ProgressReportEnd`→refresh. **Targeted** (a user, a window) — never full-scan per request. Verify column names.
- [ ] **B2 — Capacity time-series:** the Capacity Events collector already yields CU% per 30s → shape as `[{ts, cuPct}]` for `capacity_patterns`.
- [ ] **B3 — FUAM authoritative-CU + owner track (high value):** read `capacity_metrics_by_item_by_operation_by_day` (authoritative per-item, interactive-vs-background CU) + the inventory table (Item→Owner) via the FUAM **Lakehouse SQL endpoint / OneLake**. When present, use it as the AUTHORITATIVE CU source (label "capacity CU", not "monitored") and add the **owner** to item findings — this is what removes the "it's only a proxy" caveat. Verify FUAM table/column names + read access.
- [ ] **B4 — Wire the live event collector** into the Task-7 tools (`_events_or_mock` → live). **Pre-compute Job seam:** heavy whole-capacity pattern mining runs in the scheduled read-only **Job** (writes a `capacity_patterns` Delta table); the MCP `capacity_patterns` tool serves that table fast. Live per-request tools stay targeted.
- [ ] **B5 — Deploy + verify:** redeploy the MCP; in the agent, confirm `user_spike_history`/`spike_events`/`capacity_patterns` return real event-level depth (not averages) and that the FUAM-sourced numbers are labeled authoritative. Confirm read-only.

---

## Self-Review
**1. Spec coverage:** event depth (T1-2), interactive/refresh + collision (T3), expensive query (T4), patterns (T5), honesty red flags from the live critique — rounding + trial-SKU (T6), new MCP tools (T7), eval + runbooks (T8), live wiring + FUAM authoritative-CU/owner + the Job seam (B1-5). The user's asks — per-user every-spike detail, counts, times, "what caused it," capacity-wide coupled patterns, drop the fixed 30% (→ baseline/floor spike definition) — all map to tasks. ✓
**2. Placeholder scan:** every Part-A step has real interfaces + tests; Part B is a runbook by design (live schema verified at deploy, explicitly flagged). No TBDs. ✓
**3. Type consistency:** the normalized event shape (`ts,user,item,workspace,operation,kind,cuSeconds,durationMs,throttled`) is identical across T1-5 and T7; `capacity_series` `[{ts,cuPct}]` consistent T5/B2; `source: live|mock` labeling reuses the Phase-1 `_has_live_source`. Reuses `baseline.compute_baseline` (no new percentile impl). ✓
**4. No overengineering:** one event shape, deterministic patterns (no ML), reuse baseline, FUAM optional, heavy mining deferred to the Job. Phase-3.5/4 items explicitly deferred. ✓

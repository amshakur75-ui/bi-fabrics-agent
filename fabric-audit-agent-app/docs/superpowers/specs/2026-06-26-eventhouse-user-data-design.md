# Design Spec — Bulletproof Workspace Monitoring (Eventhouse) per-user data + `user_activity` tool

**Date:** 2026-06-26 · **Status:** Proposed (design approved in conversation; pending final
build by the implementing session) · **Posture:** READ-ONLY, absolute.

## Goal
Reliably extract rich **per-user** activity + behavioral **patterns** from a Fabric **Workspace
Monitoring Eventhouse** (`SemanticModelLogs`), feed the 30% concentration detector, and expose it
to the Mosaic AI agent / AI Playground via a dedicated read-only MCP tool. Must not hard-fail on
schema differences (the user created a *new* monitoring Eventhouse in a separate workspace to test;
Real-Time Hub / Capacity Overview Events are NOT set up yet — this work is Eventhouse-only).

## Problems in the current code (must fix)
`fabric_audit_agent/adapters/collector_workspace_monitoring.py`:
1. **Wrong column** — default KQL groups by `ArtifactName`; Workspace Monitoring's `SemanticModelLogs`
   uses **`ItemName`** (`ArtifactName` is the *Log Analytics* schema). A missing column makes KQL
   **error** → empty result / hard fail.
2. **No `users[]`** — it emits only `items[]`, but `detectors/user_concentration.py` reads
   `facts["users"]`. So the per-user 30% alert never fires from Eventhouse data. Wiring incomplete.

## Approach (chosen: A — one shared rich-attribution engine)
Rebuild the Workspace Monitoring collector and **refactor the per-user rollup into a shared module**
that BOTH the Eventhouse collector and the Log Analytics collector
(`collector_log_analytics.py`) call → identical rich output from either source, one place to test.

### 1. "Cannot-fail" KQL
The query references **zero columns by bare name**:
- **Time window** via `ingestion_time()` — a Kusto built-in, always present, never errors regardless
  of schema (same trick `collector_capacity_events.py` already uses).
- **Every field** via `column_ifexists("Name", default)` with coalesced fallbacks, e.g.
  `item = coalesce(column_ifexists("ItemName",""), column_ifexists("ArtifactName",""))`,
  `ts = column_ifexists("Timestamp", ingestion_time())`,
  `throttleMs = toreal(parse_json(column_ifexists("ExecutionMetrics","")).capacityThrottlingMs)`.
- Result: even if the table's columns differ, the query **runs and returns rows** (worst case a field
  is blank) instead of erroring. Python does all aggregation, tolerant of missing keys.

### 2. Shared rollup module
`adapters/attribution_rollup.py` — a pure function `rows -> {"users": [...], "items": [...], "patterns": {...}}`.
Used by both `collector_workspace_monitoring` and `collector_log_analytics` (DRY, no drift).

### 3. Output shape
```
users[]:  user, cuSeconds, sharePct, opsCount,
          topItems[{name, cuSeconds, ops}], itemCount,
          operationMix{query, refresh, command, other},        # WHAT they do
          hourly[24], peakHourUtc, firstSeenUtc, lastSeenUtc,   # WHEN (patterns)
          maxDurationMs, slowOps, errorCount, throttleMs         # health / throttle
items[]:  workspace, name, cuSeconds, sharePct, topUsers[], userCount, attributionMode
patterns: peakHourUtc (estate), busiestUsers[], coactiveUsers[]  # cross-identify overlapping heavy users
```
`users[]` feeds `detect_user_concentration` (the 30% alert) **and** the new tool.

### 4. New MCP tool `user_activity` (read-only)
- `tools.py` → `create_tool_definitions` adds a second tool; `mcp_server.py` registers it beside `run_audit`.
- `input_schema`: optional `{"user": string}`. No arg → ranked top users + patterns ("who's heaviest,
  what are they doing"). With `user` → that person's full breakdown.
- `readOnlyHint=True` set explicitly (MCP default is `false`; annotations are untrusted hints, so keep
  the in-code read-only/write-free posture). Reuses `build_collector_from_env` (live) or mock.

### 5. Wiring
- `collector_merge.merge_facts_list` already carries `users[]`; add `patterns` passthrough.
- `job.build_collector_from_env` already switches the Eventhouse collector on via
  `FABRIC_KUSTO_CLUSTER` + `FABRIC_KUSTO_DB` — unchanged, just richer output.
- `severity.py` + `detectors/__init__.py` registration of `user_concentration` already landed.

### 6. Operation-mix classification
Map `OperationName` → bucket: `query` (e.g. `QueryEnd`/DAX/MDX), `refresh` (`CommandEnd` refresh /
`ProgressReportEnd`), `command` (other `CommandEnd`/discover), else `other`. Table-driven + tolerant.

## Testing (TDD — the "no room for error" core)
Test-first with an injected fake `query`:
- correct columns; **renamed/missing columns** (proves `column_ifexists` + Python tolerance);
- op-mix classification; peak-hour + 24-bucket histogram; throttle/error parsing;
- empty input → `{}` (merge keeps other sources);
- `user_activity` tool both modes (no arg / `user` arg);
- `merge` surfaces `users[]` + `patterns`;
- full existing suite stays green (`python -m pytest -q`).

## Constraints & open inputs
- **Cannot run against the live Eventhouse from a dev machine.** Safety net = tolerant KQL + thorough
  unit tests + a `getschema` round-trip with the user.
- **Needed from the user:** Eventhouse **Query URI** (`FABRIC_KUSTO_CLUSTER`) + **database name**
  (`FABRIC_KUSTO_DB`); the output of `SemanticModelLogs | getschema` to tighten exact column names.
- Conventions: camelCase data keys, snake_case identifiers, JS→Python traps (see `CLAUDE.md`).

## Acceptance criteria
1. Eventhouse collector returns rich `users[]` + `items[]` + `patterns` and never raises on a
   column-name mismatch. 2. `detect_user_concentration` fires from Eventhouse data. 3. `user_activity`
   tool works both modes. 4. LA + Eventhouse collectors share the rollup. 5. Full suite green.

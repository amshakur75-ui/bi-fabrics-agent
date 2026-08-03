# HANDOFF — Eventhouse per-user data build (read this first)

You are picking up the **bi-fabrics-audit-agent**: a **READ-ONLY** Microsoft Fabric / Power BI
capacity & performance audit agent. It detects throttling / oversized models / refresh contention,
gives an evidence-backed **optimize-vs-size-up** verdict, and runs a **30% concentration alert** that
names **User → Item → Owner** driving capacity. It runs on **Databricks** (a custom MCP server in a
Databricks App + a Mosaic AI agent using a Databricks-hosted Claude reasoner). **Read-only is
absolute** — it reads + advises + notifies; it NEVER writes/refreshes/scales/deletes anything.

## Your mission
Implement the design in **`docs/superpowers/specs/2026-06-26-eventhouse-user-data-design.md`**:
rebuild the **Workspace Monitoring (Eventhouse) collector** so it reliably returns rich **per-user
activity + patterns**, feeds the 30% detector, and is exposed via a new read-only MCP tool
**`user_activity`**. Build it **test-first**. It must NOT hard-fail on schema differences — the user
created a *new* monitoring Eventhouse in a separate workspace to test, and **there is no room for
error**. (Real-Time Hub / Capacity Overview Events are NOT set up yet — Eventhouse only for now.)

## Repo orientation
- **Git root is `C:/Users/shaku/corporate`**; the Python project lives under **`fabric-audit-agent-py/`**.
  So paths are `fabric-audit-agent-py/fabric_audit_agent/...`. Remote: `github.com/amshakur75-ui/bi-fabrics-agent` (**PUBLIC** — never commit real tenant/client IDs or secrets).
- Run tests from the project dir: `cd fabric-audit-agent-py && python -m pytest -q`.
- Architecture (see `fabric-audit-agent-py/CLAUDE.md`): functional core + **dict-style ports/adapters**
  (`{"collect": fn}` etc.). Collectors return a `facts` dict; the pipeline + detectors are pure.
  Conventions: **camelCase data keys** (`peakCuPct`, `sharePct`, `topUsers`), snake_case identifiers,
  documented JS→Python traps.

## What changed recently (already on `main`)
The user shipped the live per-user + capacity path (commits `23335ba`..`dd04ca3`):
- `adapters/collector_log_analytics.py` — per-user CPU by item from Azure **Log Analytics**
  (`PowerBIDatasetsWorkspace`, scope `https://api.loganalytics.io/.default`). Emits `items[]` + `users[]`.
- `adapters/collector_capacity_events.py` — live capacity CU%/throttle from Real-Time Hub Capacity
  Overview Events (custom Eventhouse). CU% = `capacityUnitMs/(baseCapacityUnits*1000*30)*100`.
- `detectors/user_concentration.py` — the per-user 30% detector. Reads `facts["users"]`.
- `adapters/clients.py` — adds `build_log_analytics_query` (Logs API). `job.py` wires both new
  collectors from env. `collector_merge.py` now carries `users[]`. `severity.py` scores the new flags.

## Review findings on that code (fix opportunistically; don't regress)
1. **`user_concentration.py` estimate mixes time bases** — `metric = window-avg user share × PEAK
   capacity util`. Multiplying a day-average share by a single 30s peak isn't a real quantity and
   drives the flagship alert's headline. Prefer: monitored-share as the trip metric + capacity util as
   secondary context (or compute share within the peak window). Keep the honest `estimated` flag.
2. **`collector_capacity_events.py` overstates throttling** — counts util≥100% windows as throttled,
   but smoothing/carryforward allows >100% without throttling. Real throttle = the
   `*RejectionThresholdPercentage`/`interactiveDelayThresholdPercentage` fields >100% (research file 16).
3. **30% threshold semantics shift** when capacity-events is wired (estimate is scaled down); and
   `severity.py`'s reason says "% of monitored CU" even when the number is the capacity estimate — align them.
4. **Owner leg missing** — detector names User + top Item but not Owner. Source = FUAM gold
   (`configuredBy`/`configuredById`) per research file 22, or items/scanner owner lookup.
5. **Stale default** — `build_databricks_claude_client(endpoint="databricks-claude-3-7-sonnet")` is
   retired; bump default to `databricks-claude-opus-4-7` (app.yaml already overrides it).

## Research library (use it — it's the source of truth for the docs/scopes)
**`fabric-audit-agent-py/research/00-INDEX.md`** indexes 22 deep, citation-rich files (~848 KB). Most
relevant here: **13** (Log Analytics schema), **14** (per-user CU methods — no SP-queryable *true*
per-user CU; use CpuTimeMs proxy + capacity-level CU%), **16** (Capacity Overview Events + Activator),
**22** (FUAM gold tables give Item→Owner+CU over OneLake). The `SemanticModelLogs` columns confirmed
in research: `CpuTimeMs`, `DurationMs`, `ExecutingUser`, `ItemName`/`ItemId`/`ItemKind`,
`WorkspaceName`/`WorkspaceId`, `Timestamp`, `OperationName`, `EventText`, `ExecutionMetrics` (JSON with `capacityThrottlingMs`).

## Known bugs to fix in the file you're rebuilding
`adapters/collector_workspace_monitoring.py`: (a) default KQL uses `ArtifactName` → must be `ItemName`
(Workspace Monitoring), and (b) it emits only `items[]`, not `users[]`. The spec rebuilds both.

## Bulletproofing technique (the "cannot fail" requirement)
- Window via **`ingestion_time()`** (Kusto built-in — never errors on schema).
- Every column via **`column_ifexists("Name", default)`** with coalesced fallbacks; aggregate in Python.
- Tolerant key resolution + None-safety everywhere; empty input → return `{}` (merge keeps other sources).
- Refactor the rollup into a shared `adapters/attribution_rollup.py` used by BOTH the Eventhouse and
  Log Analytics collectors so they can't drift.

## Constraints
- **You cannot run against the live Eventhouse from a dev machine.** Safety net = tolerant KQL +
  thorough unit tests + a `getschema` round-trip with the user.
- The MCP tool must be **write-free** (a Databricks App can't persist to `/Volumes`; the scheduled Job
  owns persistence). Set the new tool's `readOnlyHint=True` explicitly.

## Open inputs to request from the user
1. Eventhouse **Query URI** → `FABRIC_KUSTO_CLUSTER` (e.g. `https://<eventhouse>.kusto.fabric.microsoft.com`).
2. Eventhouse **database name** → `FABRIC_KUSTO_DB`.
3. Output of **`SemanticModelLogs | getschema | project ColumnName, ColumnType`** so exact column
   names are locked (the spec says deliver tolerant code now AND tighten to the real schema — "Both").

## Suggested first steps
1. Read the spec (`docs/superpowers/specs/2026-06-26-eventhouse-user-data-design.md`) and `CLAUDE.md`.
2. Read `adapters/collector_workspace_monitoring.py`, `collector_log_analytics.py`,
   `detectors/user_concentration.py`, `collector_merge.py`, `tools.py`, `mcp_server.py`.
3. Write failing tests for the shared rollup + the Eventhouse collector (incl. renamed/missing columns)
   and the `user_activity` tool. Then implement. Keep the full suite green.
4. Give the user a 3-line smoke-test KQL they can paste in the Eventhouse to validate before deploy.

There is auto-memory for this project (snapshot `project_bi-fabrics-audit-agent.md`) — if available,
it has the same constraints + the research findings.

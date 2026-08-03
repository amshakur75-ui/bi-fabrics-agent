# Source-Capability Layer — Design Spec

**Date:** 2026-07-02
**Status:** design approved; awaiting spec review → implementation plan. **Implemented 2026-07-07**
(Phase-4 plan, tasks 1-15, branch `feat/phase4-capability-deepening`).

## Goal

Turn the agent's data-source layer into a **capability-tiered, gracefully-degrading, pluggable**
system:

- A **tenant-level base** (Activity Events + Capacity Events + FUAM) that answers "who/what is
  driving the capacity" with **zero per-workspace telemetry**.
- **Optional per-workspace depth** (Log Analytics *or* Workspace Monitoring Eventhouse) that the
  agent **auto-detects and uses when present** — never removed, always flip-a-switch ready.
- Every answer grounded in an explicit **coverage report** so the agent states what it can and
  cannot see, and labels fidelity (live vs delayed, authoritative vs proxy, per-query vs
  operation-level).

## Motivation

- This tenant (currently) cannot enable Azure Log Analytics or per-workspace Workspace Monitoring
  Eventhouses.
- In Fabric, **CU and user-identity live in different sources** — no single live source has both.
- The agent must still attribute capacity pressure to **User → Item → Owner** from tenant-level
  sources, **degrade honestly** where per-query depth is unavailable, and **upgrade automatically**
  if per-workspace telemetry is switched on later.

## Non-destructive principle

Keep **all** existing collectors. Log Analytics (`collector_log_analytics`, `collector_events_la`)
and Workspace Monitoring (`collector_workspace_monitoring`) become **dormant Tier-2 plugins**,
activated by env vars. **No collector is deleted.** Turning one on later is a config change, not a
code change.

## What already exists (build on, don't rebuild)

- `adapters/collector_activity.py` — Admin **Activity Events** API (`.../admin/activityevents`);
  tenant-level user attribution; interactive-vs-background op split; enriches `facts["items"]` with
  `topUsers`/`userCount`/`owner`. **← Tier-1 user source, already built. No per-workspace dependency.**
- `adapters/collector_capacity_events.py` — Capacity Events (live CU% + throttle) + `capacity_series`.
  **← Tier-1 live CU.**
- `adapters/collector_merge.py` — composes collectors, authority-first (first-non-empty) precedence,
  already surfaces `sourcesFailed` for coverage gaps.
- `adapters/collector_log_analytics.py`, `collector_events_la.py`, `collector_workspace_monitoring.py`
  — Tier-2 per-query depth (dormant-ready).
- `tools.py` — `_has_live_source`, `_has_live_event_source`, `_events_or_mock`, and the existing
  `coverage`/`sourcesBlind` labeling on tool outputs.

The codebase already embodies the core insight (from `collector_activity.py`): *"No single source
has CU + user together, so we correlate by item + time window."* This spec formalizes that into an
explicit capability + coverage model.

## Capability model

### Capability set
`capacityCU`, `userAttribution`, `perItemCU`, `eventDepth`, `owner`.

### Source descriptor
Each source declares:
```
{ provides:  [subset of the capability set],
  liveness:  "live" | "near-live" | "daily",
  authority: "authoritative" | "proxy",
  scope:     "tenant" | "per-workspace",
  envGate:   [env vars whose presence means this source is configured] }
```

### Source registry — new `fabric_audit_agent/sources.py`
A central registry: `source-id → { descriptor, build(env) -> collector-port }`. Single source of
truth for *what sources exist, how to detect + build them, and what they provide.* Initial entries:

| source-id | provides | liveness | authority | scope | envGate |
|---|---|---|---|---|---|
| `csv` | capacityCU, perItemCU | (offline) | authoritative | tenant | `FABRIC_CSV_PATHS` |
| `capacity_events` | capacityCU | live | authoritative | tenant | `FABRIC_CAPACITY_EVENTS_CLUSTER` + `_DB` |
| `activity` | userAttribution, owner | near-live | authoritative | tenant | `FABRIC_CLIENT_ID` (+ admin API access) |
| `fuam` *(future)* | perItemCU, owner | daily | authoritative | tenant | `FABRIC_FUAM_SQL_HTTP_PATH` (+ warehouse) |
| `events_la` *(Tier-2)* | eventDepth, userAttribution | live | proxy(CU) | per-workspace | `FABRIC_LA_WORKSPACE_ID` |
| `workspace_monitoring` *(Tier-2)* | eventDepth, userAttribution | live | proxy(CU) | per-workspace | `FABRIC_KUSTO_CLUSTER` + `_DB` |

(eventDepth withheld from the registry until the event seam consumes WM — 2026-07-07 final review F1)

### Resolver — `resolve_sources(env) -> { collector, coverage }`
1. Determine **configured** sources (env gate satisfied).
2. Build them **authority-first**; compose with the existing `create_merged_collector`.
3. Compute **coverage**: for each capability, select the **best configured source** (highest
   authority, then liveness); if none, mark it missing.
4. Return `{ collector, coverage }` where:
```
coverage = {
  byCapability: { capacityCU: {source, liveness, authority} | null, ... },
  blind:    [capabilities with NO configured source],
  degraded: [human-readable notes, e.g. "per-item CU is a proxy (no FUAM)"],
}
```

### Best-source-per-capability (not a rigid ladder)
Per capability, pick the highest-authority-then-liveness source among those configured:

| Capability | Source order (best → fallback) | If none configured |
|---|---|---|
| capacity CU% (live) | `capacity_events` → `csv` | blind |
| user → item | `activity` → `events_la`/`workspace_monitoring` (richer) | blind |
| per-item CU | `fuam` (authoritative) → `events_la` (proxy) → `csv` | Capacity-events estimate / blind |
| owner | `fuam` → `activity` (initiator) | blind |
| per-query DAX + CPU (`eventDepth`) | `events_la` / `workspace_monitoring` **only** | **degrade** |

## Graceful degradation — the Phase-3 event tools

Replace the `_events_or_mock` seam with
`_resolve_event_sources(env, *, days, user, item) -> (events, capacity_series, coverage)`:

- **`eventDepth` present (LA / WM):** real per-query events (the current path). Full depth.
- **`eventDepth` absent, `userAttribution` present (Tier-1):** synthesize **operation-level**
  event records from Activity Events (`cuSeconds=None`, `queryText=None`, carry `operation`/`kind`/
  `user`/`item`/`ts`). Tools rank by **operation frequency**, annotated with **FUAM daily CU** when
  present, and **label** the result *"operation-level; enable Log Analytics or Workspace Monitoring
  for per-query cost."*
- **Neither:** offline mock (no live source) or honest abstain (live-but-blind), per existing rules.

Tool-by-tool:
- **`spike_events`** — Tier-2: exact expensive DAX + `cuSeconds`. Tier-1: top items/users by
  operation frequency in the window + FUAM daily CU annotation, labeled operation-level.
- **`user_spike_history`** — Tier-2: per-query spikes with cost. Tier-1: the user's **operation
  timeline** + counts + interactive/background split from Activity (still rich; no per-query cost).
- **`capacity_patterns`** — uses the Capacity Events series (always) × activity buckets. Activity
  Events supply the surge signal directly in **both** tiers (arguably a cleaner concurrency proxy
  than LA event-counts); the per-query **driver** detail is Tier-2 only.

## Coverage surfacing (decision)

Coverage is computed **once** by the resolver and:
- **(a)** used internally by the honesty gate to abstain / choose the tier / attach fidelity labels;
- **(b)** surfaced to the user as a **concise one-line disclosure only when a capability is degraded
  or missing *and* material to the answer** (e.g., *"operation-level attribution; per-query cost
  unavailable"*) — **not** a verbose dump every turn.

(Adjustable — this is the default; a "always show full coverage" mode is a trivial flag.)

## How Phase 4 (query firewall) sits on top

The Phase-4 raw-query firewall derives its **engine allowlist from `coverage`** — it exposes only
the query engines that are actually configured (Capacity Events KQL always; LA/WM KQL when present;
FUAM SQL when present). This capability layer is the firewall's foundation and must land first.

## Components

- **Create** `fabric_audit_agent/sources.py` — the registry, `resolve_sources`, and the coverage model.
- **Create** `adapters/collector_activity_events.py` — a dedicated, offline-testable Tier-1
  **activity → event-shaped** adapter (Activity operations → normalized-event-like records with
  `cuSeconds=None`, `queryText=None`) for the degraded event path.
- **Modify** `tools.py` — swap `_events_or_mock` / ad-hoc `_has_live_event_source` checks for
  `resolve_sources` / `_resolve_event_sources`; thread `coverage` into tool outputs; implement the
  Tier-1 vs Tier-2 behavior in the 3 event tools.
- **(Future / B3)** `adapters/collector_fuam.py` — slots into the registry when FUAM exists; absent
  today, so `perItemCU`/`owner` fall back and coverage says so.
- **Tests** — offline, injected fakes only:
  - resolver picks the correct source per capability across config permutations (all combos of
    csv/capacity/activity/LA/WM/fuam present-absent);
  - coverage report correctness (byCapability / blind / degraded);
  - each event tool's Tier-1 vs Tier-2 output shape + labels;
  - the all-mock path is unchanged; existing suite stays green.

## Phase-4 external-repo integrations (user-approved 2026-07-06; read-only preserving)

Two integrations of Microsoft OSS repos land IN Phase 4 (not the permission-gated Phase 5),
because neither adds a runtime dependency, connection, or permission:

1. **Skills harvest** — mine `microsoft/skills-for-fabric` + `microsoft/azure-skills`
   (MIT) *consumption/KQL* skill content as authoring input for our verified-query library +
   runbooks. Content is adapted into OUR files; nothing from the repos is executed. Skip all
   `*-authoring-*` skills (write-oriented). Security-review anything adapted (standing rule:
   external repo text is untrusted input).
2. **`fabric-rti-mcp` read-side absorption** — adapt the MIT-licensed Kusto plumbing
   (auth/connection/formatter + read-only query patterns) into our own adapters behind the
   query firewall. **Do NOT run their server and do NOT port the write tools** —
   `kusto_command` (`destructiveHint=True`: `.create/.alter/.drop`, `.set-or-append`, policies)
   and `kusto_ingest_inline_into_table` are excluded by construction; the agent must never
   carry write affordances even ones the SP's grants would reject. Line-by-line security audit
   before adoption. (MCP `readOnlyHint` annotations are untrusted hints — owning the code beats
   tool-filtering a live server.)

`azure-devops-mcp` (work-item creation = write + new outward channel) stays in Phase 5,
approval-gated. `fabric-rest-api-specs` (license NOASSERTION) is consult-only — never vendor.

**Harvest inventory:** the audited, file/line-level list of exactly what gets absorbed from each
MCP lives in `research/23-mcp-harvest-inventory.md`. Highlights folded into Phase 4: the
`_crp()` readonly-hardline + blocked-override pattern (the firewall's KQL control,
production-validated), `KustoFormatter` compact outputs (columnar/header_arrays — token-efficient
large results), schema/sample discovery tools (firewall allowlist grounding), `kusto_show_queryplan`
cost hints (the cost-guardrail seed), `kusto_get_shots` (verified-query-library prior art), and
query deeplinks ("verify in Fabric" links on quoted figures).

## Phase-4 capacity-diagnostics deepening (research 2026-07-07)

Three read-only additions, confirmed against Microsoft Learn + our own code, that extend the
capacity verdict without a new data source or permission. All three fold into this spec's existing
components — none need a separate design doc.

1. **Throttle decomposition** — `capacity_diagnostics` (Task 9) currently exposes the fixed
   `.show capacity/cluster/workload_groups/diagnostics` suite but stops short of *classifying* a
   throttle event. Microsoft's own admin troubleshooting runbook is a ready-made 3-stage
   procedure: **(1)** CU % over time > 100% (a gate we already partially apply); **(2)** which
   throttling signal actually fired — Interactive Delay / Interactive Rejection / Background
   Rejection, each its own 100%-referenced series — **this second gate is what our current verdict
   is missing**: CU% > 100% alone does not prove throttling happened, only that it was possible;
   **(3)** drill to the offending workspace/item by sorting timepoint operations by `Timepoint CU
   (s)` / `% of base capacity` descending — the same shape as our existing spike-ranking logic.
   Also surface the Metrics app's own `minutes to burndown` carryforward estimate verbatim (Kusto
   fields TBD — confirm whether stage-2/3 signals are present in the Capacity Events Eventhouse we
   already collect, or require the Capacity Metrics semantic model — see item 3 of the Phase-5
   sheet below).
   Source: [capacity-planning-troubleshoot-throttling](https://learn.microsoft.com/en-us/fabric/enterprise/capacity-planning-troubleshoot-throttling), [throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling).

2. **Query-plan dry-run (cost estimate)** — already named as a "cost-guardrail seed" above; make
   it concrete. Adapt `fabric-rti-mcp`'s `kusto_show_queryplan` shape (`.show query <kql> with
   (ShowPlan='True')`-class read-only plan retrieval — returns `PlanSize`, `RelopSize`, estimated
   row counts, per-shard scan info, **without executing the query**). Wire this ahead of any future
   ad-hoc/agent-authored KQL (the Phase-4 firewall's pre-flight check) and, immediately usable
   today, ahead of `capacity_diagnostics`'/`describe_source`'s own built queries as a defense-in-depth
   cost cap. Upgrades the existing `| take 0` dry-run (syntax-only) to a real cost estimate.
   Source: [microsoft/fabric-rti-mcp](https://github.com/microsoft/fabric-rti-mcp) (read-only tool
   only — the server itself remains excluded per the DO-NOT-PORT list above).

3. **Seasonal-naive / trend time-to-throttle forecast** — evaluated TimesFM (starred by the user)
   against the CU% series we already collect via `capacity_series`; rejected as over-engineered for
   this signal (foundation-model value is clearest on noisy multi-domain series with short
   history — our series is one clean metric with a known hard threshold and strong regular
   seasonality). Implement instead: a same-day-last-week baseline or robust linear trend on the
   existing series, projected to the 100% line, reported with an honest confidence range — no new
   dependency, no model download, fully explainable. Surface the Metrics app's own `minutes to
   burndown` field (item 1) verbatim where available, rather than re-deriving it independently.

4. **Refresh-failure classification** — `adapters/collector_rest.py` already pulls raw refresh
   history into `facts["refreshes"]` (via `FABRIC_REFRESHES_URL`), but **no detector reads it** —
   `detectors/pipeline.py::detect_pipelines` only looks at `facts["pipelines"]`'s pre-aggregated
   fail-rate. The REST refresh-history payload itself carries real decomposition for free: each
   `Refresh` has a `refreshAttempts[]` array, and each attempt carries `type` (`Data` vs `Query`),
   its own `startTime`/`endTime` (duration per phase), `executionMetrics` (AS engine metrics), and
   `serviceExceptionJson` (a structured error code, e.g. `ModelRefreshFailed_CredentialsNotSpecified`).
   Add a detector/tool that reads `facts["refreshes"]` and classifies failures by error code,
   surfaces which phase (data-load vs query/cache-warm) took the time, and flags multi-attempt
   refreshes (retry storms) — no new data source, no new permission, just reading a field we
   already collect and throw away. Per-table/partition detail (`ViaEnhancedApi` refreshes only)
   is a further-out stretch, conditional on the org triggering refreshes via the Enhanced Refresh
   API — not assumed available.
   Source: [Get Refresh History](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history).

5. **Dead-man's-switch alerting** — `job.py::main()`/`run_job()` currently has **no exception
   handling at all**. If the sweep crashes outright (expired SP secret — the pasted
   `FABRIC_CLIENT_SECRET` still needs rotation, a Kusto host timeout, any unhandled error from a
   collector/reasoner/delivery port), the only signal is whatever Databricks' native job-failure
   page shows — no Teams alert, nothing distinct from a normal findings card. An agent whose whole
   premise is "catch problems before someone notices" has no way to report its own silence. Wrap
   `main()` in a try/except; on any unhandled exception, post a minimal, visually distinct
   "sweep failed at `<time>`: `<error>`" card through the existing `delivery_teams`
   adapter (reuse, no new integration) before re-raising (so the Databricks Job still records
   the failure). Small, isolated, no dependency on anything else in this section.

6. **Eval golden-case coverage gap** — only 3 of the 12 MCP tools currently have any golden-case
   coverage (`investigate_capacity_spike`, `raw_events`, `spike_events`); the other 9 — including
   `capacity_diagnostics` and `capacity_patterns`, the two flagship tools built this session —
   have zero. For a system built around "never fabricate a figure" (Task 12's honesty labels,
   the grounded-gate in `score_investigations.py`), untested tools are precisely where a
   fabrication would go unnoticed. Add at least one grounded golden case per currently-uncovered
   tool to `eval/agent_cases.json` (mirroring the existing `windowed-raw-events-12to13` pattern —
   real tool input, an answer that cites a token genuinely present in the mock result, verified
   against both gates before committing). Testing debt, not a feature; sequenced here only so it's
   tracked and planned, not lost.

7. **Deterministic diagnostic engine (`diagnose` tool) — the capstone.** Today the diagnostic
   *processes* exist only as prose: `docs/runbooks/*.md` tell the LLM which tools to call in which
   order, and whether the chain actually runs depends on the model following the doc. Encode the
   runbooks (plus item 1's Microsoft 3-stage throttle runbook) as **executable decision trees** in
   `investigation/` — pure functions that call the same handlers/collectors internally, so one
   `diagnose(symptom, when=?)` tool runs the whole chain itself:
   - **Hypothesis elimination, not just confirmation** (differential diagnosis): each step is a
     test that confirms OR rules out a cause, and the output carries both — e.g. *"NOT throttling:
     CU% never exceeded 100% in the window (stage-1 gate)"* is as valuable as naming the culprit.
     The Microsoft runbook's explicit decision points (stages 1→2→3) are literally this shape.
   - **Causal chain output**: `{"chain": [ {step, hypothesis, verdict: confirmed|eliminated,
     evidence: {…figures from the actual tool result…}} … ], "rootCause": …, "eliminated": […],
     "confidence": …}` — the reasoner then *narrates* a completed investigation instead of
     improvising one; every quoted figure traces to a chain hop (extends the existing
     grounded-gate discipline).
   - **Auto-chain the orphaned analyzers**: `dax.analyze_dax` exists but is CLI-only — not one of
     the 12 tools, unreachable by the agent. Expose it as a tool AND auto-run it inside the chain
     on the `queryText` of top offenders that `spike_events` already returns (spike → item → user
     → query text → named DAX anti-pattern → specific coaching). Same for
     `workload.refresh_collisions` (built, used only inside one playbook): the refresh-collision
     runbook becomes an executed branch, and item 4's refresh-failure classification slots in as
     another branch (symptom "refresh failed/slow" → error-code class → phase timing → retry
     storms → collision check).
   - Deterministic, offline-testable (fixture in → exact chain out), pure orchestration of
     existing pieces — no ML, no new data source, no new permission. This is the item that moves
     the agent from *"here's what you should check"* to *"here's what I checked, what I ruled
     out, and what the root cause is."*

8. **Give the conversational agent memory — expose the run-history brain to the MCP surface.**
   The longitudinal intelligence already exists and is rich: `run_audit` (pipeline.py) wires
   `annotate_recurring`, `apply_escalation`, `annotate_accountability`, `assess_sla`,
   `build_digest`, `forecast_capacity`, `assess_outcomes` ("did our advice work?"), and
   `detect_anomalies` — but ONLY when a `store` is present. The MCP tool path runs with
   `store=None` (tools.py:111 — the App container can't *write* to /Volumes), so the
   conversational agent is a pure snapshot analyst: it cannot answer "what changed since last
   week?", "is this recurring?", "is this trending worse?", or "did last month's fix hold?" —
   even though the scheduled Job appends exactly this history on every sweep and the
   interpretation code already ships. Fix is a read-only seam, not new intelligence:
   - a **load-only store port** (`{"history": fn}` with no `append`) pointed at the same
     Volume/Delta path the Job writes — the App can read /Volumes; it just must never write
     (read-only preserved by construction: the port has no append);
   - one new tool, `whats_changed` (or `audit_history`): findings diff vs the previous sweep(s) —
     `new` / `recurring` / `resolved` / `worsening` — plus the trend series, digest, outcomes,
     and forecast the pipeline already computes, envelope-labeled with the history window and
     the Job's last-run timestamp (staleness disclosed, never implied-fresh);
   - gate on an env var (`FABRIC_HISTORY_PATH`); absent → the tool answers honestly that no
     history is configured (mock path mirrors the other tools).
   This is the "colleague who remembers" upgrade — highest value-per-line in this section, since
   every hard part (the history schema, the trend/recurrence/outcome logic) already exists and is
   tested; only the read seam + tool surface is new.

9. **`user_timeline` tool — "what did John do all day?"** The per-user chronological answer is
   split across two streams today, one of which is discarded before any tool can see it:
   - **Engine events** (LA/WM, monitored workspaces): already fully exposed —
     `raw_events(user=…, hours=24, order="recent")` gives every query/refresh with ts, item,
     CU-seconds, query text.
   - **Activity Events** (tenant-wide admin audit log): `collector_activity.py` already pulls and
     maps ViewReport / RefreshDataset / ExecuteNotebook / RunPipeline / exports etc. with
     `user`, `operation`, `item`, `workspace`, `time`, interactive-vs-background — then
     aggregates it into item attribution, discarding the per-event timeline. No tool exposes it.
   Add ONE tool that merges both streams chronologically for a single user + window:
   `{"timeline": [ {ts, source: "activity"|"engine", operation, item, workspace,
   cuSeconds|null, queryText|null} … ]}` — coverage-labeled (activity ops are tenant-wide but
   carry no CU; engine cost exists only for monitored workspaces), bounded (row cap + window
   like raw_events), spotlighted query text, camelCase. Reuses `map_activity_event` and the
   existing event collectors as-is; read-only; the same Tier-1/Tier-2 fusion the graceful-
   degradation section already prescribes, applied per-user. Note for deployment docs: this is
   admin audit-log data (the same log tenant admins already have) — per-person day-tracking is
   an org-policy question for the deployer, not a technical gate; the reasoner's name-sanitizer
   is unaffected (the tool returns data to the asking admin, not into LLM prompts).

**Checked and explicitly NOT pursued — with reasons, not left open:**

- **DAX Server Timings (storage-engine vs formula-engine split)** — confirmed this requires an
  active Profiler/trace-session subscription against the model (via DAX Studio/XMLA with elevated
  rights), not a stateless REST or KQL call. That's a materially different integration shape —
  a persistent session, not a query — from everything else this agent does. `dax.py::analyze_dax`
  already documents its own ceiling here (`"profile with Performance Analyzer / DAX Studio..."`).
  Real capability, but it needs its own design (session lifecycle, read-only trace scoping) before
  it's a task — not a Phase-4 line item.
- **Report/visual render time** — checked directly: visual display time is genuinely client-side
  only (Power BI Desktop/Service UI thread), with no server-side API exposing it — Performance
  Analyzer has no Service-side equivalent. The *other* leg of "why is this report slow" — DAX query
  duration — is **already covered**: it's exactly what `CpuTimeMs`/`DurationMs` on `QueryEnd`
  events (our existing Log Analytics / Workspace Monitoring collectors) capture today. Nothing
  missing to build here; the client-render leg is a permanent blind spot, not a missed opportunity.
  Source: [Performance Analyzer](https://learn.microsoft.com/en-us/power-bi/create-reports/performance-analyzer).
- **SKU cost delta** — explicitly out of scope per user direction (2026-07-07). `investigation/sku.py`
  knows SKU *names* only, no pricing; the Azure Retail Prices API (Phase-5 sheet item 7, public,
  no auth) remains the path in if this is ever wanted later.
- **ML anomaly detection / auto-remediation / streaming watchers** — the "even deeper" directions
  deliberately NOT taken (2026-07-07, "without overcomplicating"): ML root-causing duplicates what
  item 7's deterministic trees do explainably; auto-remediation is a write action (forbidden by
  the read-only absolute); an always-on streaming watcher is an architecture change the scheduled
  sweep + on-demand MCP already cover. Item 7 is the ceiling of *deduction* reachable without
  breaking read-only or determinism.

## Phase 5 — approval-gated adders (the complete sheet)

Each is a drop-in plugin to this capability layer once granted. FUAM is NOT here — it stays in
Phase 3 (B3), already pending approval.

| # | Adder | Access needed | What it unlocks |
|---|---|---|---|
| 1 | Entra sign-in logs | LA `SigninLogs` export or Graph `AuditLog.Read.All` | true logins/concurrency (vs activity proxy) |
| 2 | ADO integration (OUR mcp tools) | ADO identity: Work Items read-write + Build/Release/Code read | finding→ticket lifecycle + deployment/PR change-correlation ("what shipped before the spike") — confirm org ships PBI/Fabric via ADO first |
| 3 | Workspace Monitoring Eventhouse (Tier-2) | per-workspace enablement + CU cost | per-query depth where LA isn't wired |
| 4 | Spark/non-dataset event depth | `fabric-spark-monitoring` Eventhouse deploy | notebook/pipeline internals (else FUAM daily aggregate only) |
| 5 | Gateway monitoring (FPM module) | PS agents on gateway machines | refresh root-cause below the symptom — N/A if all-cloud |
| 6 | fabric-cost-analysis (FCA) | accelerator deploy + Cost Mgmt reads | real $ / reservations on the verdict |
| 7 | Azure Retail Prices API | none (public) — external-call approval only | list-price $ on size-up verdicts (cheap first step) |
| 8 | SemanticModelAudit | Semantic Link / notebook run rights | oversized-model evidence (unused columns, resident memory) |
| 9 | DAXPerformanceTunerMCPServer | XMLA execute on target models | tested DAX optimization coaching; port-vs-federate decided at approval |
| 10 | SemanticModelMCPServer | XMLA/semantic-link reads | model metadata collector |
| 11 | FabricIQ hosted MCP | Fabric AI Hub + user-identity federation | hosted PBI data exploration — overlaps our agent; lowest priority |
| 12 | (contingency) rti-mcp as gated Databricks App | new shared App approval | only if we ever want its eventstream/activator services live |

## Non-goals

- Building/deploying FUAM (deploy-time) — the registry just accommodates it.
- Enabling LA / Workspace Monitoring — kept **dormant**.
- The Phase-4 firewall internals (separate spec) — this only provides the coverage it consumes.
- Changing Node-parity core semantics or the existing `run_audit` pipeline behavior.

## Open decisions (defaults chosen; flag if you disagree)

1. **Coverage surfacing verbosity** — default: concise one-liner only when material (above).
2. **Tier-1 `spike_events` ranking** — default: rank by operation frequency; annotate with FUAM CU
   when FUAM is configured (rather than requiring FUAM to produce any ranking).
3. **Descriptor location** — default: central registry in `sources.py` (vs. co-locating a
   `CAPABILITIES` const in each collector module).

## Global constraints (carry into the plan)

- **Read-only absolute.** No writes / refreshes / scale.
- **camelCase data keys / snake_case identifiers. stdlib-only core** (prod deps opt-in extras).
- **Nullish** is `x if x is not None else default` — never falsy `or` (a real `0`/`""` must survive).
- **Non-destructive** — no collector removed; LA/WM stay as dormant plugins.
- **Offline tests with injected fakes** — never hit a live endpoint from the suite.
- **Full suite green after every task** (`cd fabric-audit-agent-py && python -m pytest -q`; baseline
  460 passed, 1 skipped).

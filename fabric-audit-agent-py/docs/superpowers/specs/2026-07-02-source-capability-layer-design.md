# Source-Capability Layer — Design Spec

**Date:** 2026-07-02
**Status:** design approved; awaiting spec review → implementation plan.

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

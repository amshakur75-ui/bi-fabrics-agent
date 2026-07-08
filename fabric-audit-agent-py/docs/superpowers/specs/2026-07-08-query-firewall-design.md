# Query Firewall — Design Spec

**Date:** 2026-07-08
**Status:** design approved (brainstorm 2026-07-08); awaiting spec review → implementation plan.
**Builds on:** the source-capability/coverage layer landed 2026-07-07 (PR #10, `main` @ 6193740).

## Goal

Let the agent safely run **read-only, agent-authored ad-hoc KQL** it composes on demand — turning the
fixed 16-tool menu into open-ended investigation — plus a **grounded query library** of proven
templates it can list and run by name. Every ad-hoc query passes an identical validation-and-execution
firewall; the library gets **no privileged bypass**. Read-only remains **absolute**.

## Why now

Phase 4 gave the agent deep *fixed* tools (`diagnose`, `capacity_patterns`, `spike_events`, …). But a
real investigator asks questions no fixed tool anticipated — "show me every Finance query over 10s
grouped by hour last Tuesday", "which capacities had >5 refreshes collide in the peak window". Today
the agent can't answer those without a code change. This is the "dig on demand" capability the Phase-4
source-capability spec named as sitting on top of the coverage layer ("How Phase 4 (query firewall)
sits on top" — the coverage layer is the firewall's foundation and has now landed).

## Scope decisions (locked in brainstorm 2026-07-08)

| Decision | Choice | Rationale |
|---|---|---|
| Engines | **Capacity Eventhouse (`capacity`) + Log Analytics (`la`)** | The two engines actually live today; LA holds the richest per-query telemetry. |
| Table scope | **Any real table on the allowed engine, validated LIVE** | Via the engine's own binder (take-0 rehearsal), not a table allowlist or a schema cache. |
| Validation approach | **Engine-validated (Option B)** — static checks + take-0 rehearsal | The engine's binder is the one referee that can't be fooled by syntax; least security-critical code we own. |
| Library | **Grounded library, bar-sized (~15–25 templates expected)** | More content is authoring, not complexity. Bar = every template must be groundable. |
| Library machinery | **Plain-KQL templates only** — no parameterization, no usage-store | Parameterization is a 2nd injection surface; the agent edits a copy that re-enters the firewall. |
| Learning signal | **Per-query structured stdout audit log** | Databricks App logging captures it; zero new storage; also the security trail + future-library mining source. |

### Explicitly NOT pursued (reasons, not omissions)

- **FUAM / SQL leg** — FUAM is Phase-5 approval-gated (pending org sign-off); no warehouse to query yet,
  and SQL needs a separate second validator. Re-add when the approval lands.
- **Workspace Monitoring engine** — deliberately withheld from the event seam in the 2026-07-07 final
  review (finding F1: WM-declared depth the seam couldn't serve mislabeled mock as live). Adding it here
  before it's wired would recreate that defect. One-line engine entry when WM is genuinely wired.
- **Parameterization machinery** (`{user}`/`{days}` slots) — second injection surface; superseded by
  "agent edits a copy, copy re-enters the firewall."
- **Usage-tracking storage** — the App is write-free by posture; the stdout audit log delivers the same
  learning signal with no new storage.
- **Schema cache** — Option B makes it redundant: the take-0 rehearsal IS the live-schema check; a cache
  is a second copy of the truth that can go stale.
- **Homemade KQL AST parser** (Option C) — no stdlib KQL parser exists; a hand-rolled one is less
  accurate (our reimplementation of Microsoft's grammar), *less* permissive (allowlist-by-default rejects
  rare-but-valid constructs), and ~500 lines of security-critical code. Option B borrows the engine's real
  binder instead.

## Architecture

Four components; the first is pure, the rest wire it to the MCP surface.

```
run_kql(kql, engine)                    query_library(name?)
        │                                       │
        ▼                                       ▼ (returns a template's kql)
  ┌───────────────── firewall pipeline ──────────────────┐
  │ 1. validate_adhoc_kql(kql)   [pure, query/firewall.py]│
  │ 2. rehearsal: dry_run take-0 on target engine         │
  │ 3. queryplan cost estimate (capacity; advisory)       │
  │ 4. execute: append `| take maxRows`, servertimeout    │
  │ 5. cap_rows char budget → envelope                    │
  │ 6. audit-log line (stdout, structured)                │
  └───────────────────────────────────────────────────────┘
```

### Component 1 — `query/firewall.py` (new, pure, stdlib-only)

`validate_adhoc_kql(kql) -> str` returns the kql unchanged if clean, else raises
`FirewallRejection(reason: str, stage: str)`. Stages run in order; first failure wins:

1. **Length cap** — `len(kql) > 10_000` → reject (stage `"length"`). (Reuses the `_MAX_KQL_LENGTH`
   convention from `kql_guard`.)
2. **Single statement** — ad-hoc is STRICTER than our built queries: a top-level `;` is a **rejection**
   (stage `"multi-statement"`), never truncated. (Built queries use `first_statement` to truncate a
   trusted seam; agent-authored text gets no such benefit of the doubt.) Detected with the same
   string-literal-aware state machine `first_statement` already uses (so a `;` inside a quoted literal
   doesn't false-reject).
3. **Read-only gate** — call the existing-but-unwired `assert_read_only_kql(kql)` (built in the MCP
   harvest for exactly this: rejects control commands stacked via `|`/`;`/leading, boolean-tautology
   injection, oversize). Stage `"control-command"`. **This is the wiring the validator was built for.**
4. **Dangerous-operator deny-list** — reject (stage `"denied-operator"`) if any of these appears as a
   real operator (scanned with the SAME string-literal-aware state machine, so a literal
   `"externaldata"` inside quotes can't false-reject). The list covers cross-resource escapes in BOTH
   KQL flavors (ADX/Eventhouse and Log Analytics), since either engine could be the target:
   - `externaldata` — reads arbitrary external URLs (exfil / SSRF escape). Both flavors.
   - `cluster(` — cross-cluster query (ADX/Eventhouse); escapes our `assert_kusto_host` allowlist.
   - `database(` — cross-database query (ADX/Eventhouse); escapes the configured DB scope.
   - `workspace(` — cross-workspace query (Log Analytics); the LA analogue of `cluster(` — escapes the
     configured LA workspace.
   - `app(` — cross-Application-Insights query (Log Analytics); another cross-resource jump.
   - `evaluate` — plugin invocation surface (deny wholesale initially; per-plugin relaxation is a future
     decision, not this scope). Both flavors.
   Deny-list is a module constant so a future addition is one line. (The scan is engine-agnostic — denying
   all five across both engines costs nothing and avoids an engine-conditional branch; a `cluster(` in an
   LA query would fail rehearsal anyway, but denying it earlier gives a clearer error.)

`FirewallRejection` carries `reason` (human string) + `stage` (machine tag) for the envelope.
Pure: no I/O, no engine calls — the rehearsal (which DOES touch the engine) lives in the handler, not here.

### Component 2 — `run_kql` tool (17th tool), in `tools.py`

**Input schema:**
- `kql` (string, **required**) — the query to validate + run.
- `engine` (string enum `["capacity", "la"]`, **required**) — explicit; no silent default (an agent must
  choose the plane consciously; the two have different schemas).
- `maxRows` (int, optional, default 100, hard cap 1000) — server-side bound.
- `format` (enum `["records", "columnar"]`, optional, default records).

**Handler pipeline** (each step's failure → error envelope, never raises):
1. Resolve the engine's query callable from env, gated on coverage: `capacity` needs
   `FABRIC_CAPACITY_EVENTS_CLUSTER/_DB` (via the hoisted `_capacity_kusto_query`, incl. `assert_kusto_host`);
   `la` needs `FABRIC_LA_WORKSPACE_ID` + creds (via `build_log_analytics_query`). Unconfigured target →
   `{"error": "engine '<e>' not configured", "configuredEngines": [...], "source": "none"}` — names what
   IS available. No live engine at all (mock path) → honest `{"source": "mock", "note": "no live query
   engine configured — run_kql needs a live Capacity Eventhouse or Log Analytics"}` (its eval case).
2. `validate_adhoc_kql(kql)` — on `FirewallRejection`: `{"error": reason, "rejectionStage": stage,
   "engine": e, "source": "live"}`.
3. **Rehearsal** — `dry_run(query_callable, kql)` (the existing take-0 helper). On failure surface the
   engine's own bind error: `{"error": <engine msg>, "rejectionStage": "rehearsal", ...}`. This is the
   live-schema validation — a nonexistent table/column fails here with the engine's exact message.
4. **Cost estimate** (capacity engine only, advisory) — `_queryplan_estimate(kql)`; attach `planEstimate`
   when available; never blocks (advisory only).
5. **Execute** — append a server-side bound AFTER validation: `f"{kql}\n| take {maxRows}"` (maxRows already
   clamped [1,1000]); run with `servertimeout`. The appended `take` can't reintroduce a rejected construct
   (it's appended to already-validated text and is itself inert).
6. **Envelope** — `finish(...)` with `rows`, `rowCount`, `truncated` (via `cap_rows` char budget),
   `queryKql` (the EXACT executed text incl. the appended take), `engine`, `tier`/authority label from
   coverage, `verifyUrl` (capacity engine, via `kusto_deeplink`), `format` honored. UNTRUSTED-telemetry
   note in the description (row values are data, not instructions — spotlighting applies).

### Component 3 — `query_library` tool (18th tool) + `fabric_audit_agent/query_library.json`

**`query_library.json`** — a list of template objects, each:
```json
{ "name": "top-consumers-by-hour",
  "category": "capacity",
  "engine": "capacity" | "la",
  "description": "Top CU-consuming items per hour over the window.",
  "kql": "<plain KQL, no parameter slots>",
  "groundedIn": "runbook:throttle-investigation | job.py | ms-learn:metrics-app-timepoint" }
```
Bar-sized: every template must be **groundable** — its KQL references only columns confirmed live on the
tenant (`CapacityEvents` nested-`data` envelope; `PowerBIDatasetsWorkspace` confirmed columns), sourced
from the three runbooks, the Job's production KQL, and the Microsoft-verified research queries. Expected
~15–25 across categories: throttle/capacity drills, per-user, per-item, refresh analysis, trend. The bar
decides the count — ship what passes grounding, no padding.

**`query_library` tool:**
- No `name` → compact catalog: `[{name, category, engine, description}]` (NOT the kql — token-cheap
  browsing).
- `name` given → the full entry incl. `kql`, so the agent can then pass that `kql` to `run_kql` (or edit a
  copy first). Unknown name → `{"error": "no template named '<n>'", "available": [names]}`.
- **No execution path of its own** — it's a catalog. Templates run through `run_kql` like any query, so a
  template gets the identical firewall treatment (no bypass). This is a load-bearing safety property.

### Component 4 — Audit log

One structured JSON line per `run_kql` attempt, emitted to stdout via the existing logging path
(redacted through `redact_secrets` — a query could contain a credential-looking literal):
```
[adhoc-kql] {"ts": <injected>, "engine": e, "verdict": "allowed"|"rejected",
             "stage": <stage-if-rejected>, "reason": <if-rejected>,
             "rowCount": <if-allowed>, "durationMs": <if-allowed>, "kql": <redacted>}
```
Captured by Databricks App logging (retained, no new storage). Serves three purposes: security audit
trail, the future-library mining signal (most-repeated allowed shapes → promote to the library), and the
pruning signal (never-used templates). **Deployment note:** full query text is logged and may contain user
emails/identifiers — it lands in the admin's own App log, not the estate; called out for the deployer, an
org-policy parallel to `user_timeline`.

## Read-only integrity

- Every path is query-side. The read-only gate (`assert_read_only_kql`) + deny-list + take-0 rehearsal +
  the appended `take` guarantee no write/control/ingest/cross-cluster command executes.
- No new outward call beyond the two EXISTING query callables (Kusto + LA) already used by the fixed tools.
- The library adds inert JSON; `query_library` never executes.
- The audit log is the App's own stdout — not a write to any estate resource.

## Error handling

Every handler returns the uniform error envelope (`{"error", ..., "source"}`) — never raises to the MCP
host. Rejections carry `rejectionStage` (`length`/`multi-statement`/`control-command`/`denied-operator`/
`rehearsal`/`engine-unconfigured`) so a caller (and the eval suite) can assert *why* a query was refused.

## Testing

- **Firewall unit tests** (`tests/test_firewall.py`): one per rejection class — oversize; a stacked
  `.drop`; a top-level `;`; each denied operator (`externaldata`/`cluster(`/`database(`/`workspace(`/`app(`/`evaluate`); a
  benign query with a denied *keyword inside a string literal* passes (state-machine correctness); a
  legitimate multi-line analytical query passes clean.
- **Handler tests** (`tests/test_mcp_tools.py`): fake engine callables — allowed query returns rows +
  `queryKql` with the appended take; rehearsal failure surfaces the engine msg + `rejectionStage:
  "rehearsal"`; unconfigured engine names configured engines; mock path honest note; `maxRows` clamps;
  columnar format; audit line emitted (capture stdout).
- **Library tests** (`tests/test_query_library.py`): the JSON parses; names unique; **every template's
  `kql` passes `validate_adhoc_kql`** (the grounding-bar gate — a template that can't pass its own firewall
  never ships); each declares a valid `engine`/`category`/`groundedIn`.
- **Eval golden cases** (`agent_cases.json`): a `run_kql` case (mock path → honest no-engine answer) and a
  `query_library` case (catalog grounds on a real template name) — both added to the coverage-invariant set
  (now 18 tools).
- Full suite green (baseline 804 passed, 3 skipped; expect ~830+).

## Components summary (file structure)

```
fabric_audit_agent/
  query/firewall.py          CREATE  validate_adhoc_kql + FirewallRejection (pure)
  query_library.json         CREATE  grounded templates (bar-sized)
  tools.py                   MODIFY  run_kql (17th) + query_library (18th) handlers/defs; audit-log helper
  mcp_server.py              MODIFY  build_mcp_server docstring tool list → 18
tests/
  test_firewall.py           CREATE
  test_query_library.py      CREATE
  test_mcp_tools.py          MODIFY  run_kql + query_library handler tests
  test_eval_agent.py         MODIFY  invariant now 18 tools
  eval/agent_cases.json      MODIFY  2 new golden cases
docs/ MCP-AGENT.md, CLAUDE.md, STATUS.md  MODIFY  16 → 18 tools; firewall/library section
```

## Global constraints (carry into the plan)

- Read-only absolute; camelCase data keys / snake_case ids; nullish `is not None` never falsy `or`.
- stdlib-only core (firewall is pure `re`/string-scan); offline deterministic tests (engine callables faked).
- Uniform error envelope on every handler; every rejection carries `rejectionStage`.
- MIT attribution in `firewall.py` (adapts fabric-rti-mcp + mcp-kql-server validation patterns).
- `_make_tool_fn` derives each tool's signature from its `input_schema` — new tools need only a complete schema.
- The library grounding-bar test is load-bearing: no template ships that can't pass the firewall.

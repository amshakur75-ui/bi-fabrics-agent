# Fabric Audit Agent (Python) — Deployment & Permissions

This package targets **Databricks** (Python-first): a **Python-wheel Job** for the scheduled
sweep and a **Python MCP server** for the conversational pull surface. It is self-contained —
`pip install` the wheel, set secrets, swap the mock adapters for real ones, and schedule.

> **Posture:** the agent is **read-only** on the data plane. It reads telemetry/metadata and
> *advises*. The only outward "writes" are (a) its own findings into its own store, and (b)
> notifications it sends. It never edits, refreshes, scales, or deletes anything in the estate.

> **Exact scope/field names evolve.** Treat the scope names and REST/Activity/Log-Analytics
> field names below as the current shape; confirm against Microsoft Learn at setup, and tune the
> representative URLs/field mappings in `collector_rest.py` / `collector_activity.py`.

> **New here (or a fresh session)? Start with [STATUS.md](STATUS.md)** — current state, the 4-phase
> rollout, and where everything lives. This file is the deploy *reference*; the per-phase runbooks
> ([PHASE2-SP-TEST.md](PHASE2-SP-TEST.md), [PHASE3-DATABRICKS.md](PHASE3-DATABRICKS.md)) are the
> step-by-step.

---

## 1. What you deploy (swap mocks → real)

The functional core is untouched. You wire real ports (same dict-style interface as the mocks):

| Port (mock today) | Replace with |
|---|---|
| `adapters/collector_mock` | `adapters/collector_rest.create_rest_collector(http, config)` over Power BI Admin / Fabric REST, plus `adapters/collector_activity.create_activity_collector(http, config, base_collector=)` for per-user attribution |
| `adapters/reasoner_stub` | `adapters/reasoner_claude.create_claude_reasoner(client, ...)` (Anthropic / Databricks-hosted; sanitizes first; KB fallback on error) |
| `adapters/delivery_file` | `adapters/delivery_teams.create_teams_delivery(http, webhook_url)` and/or `adapters/ticketing.create_ticketing_delivery(client, ...)` |
| `adapters/store_local` / `lifecycle_store` | a Unity Catalog / Delta-backed store implementing `{history, append}` and `{load, save}` |

`job.py` (`run_job` / `main`) already wires all of these from environment / secret-scope config;
every port is injectable so it's unit-testable without the SDKs. `mcp_server.py` (`build_mcp_server`)
serves the `run_audit` tool; `data_agent.build_data_agent_manifest` is the host manifest.

Concrete clients (`adapters/clients.py`): `EntraHttp` (SP bearer auth via `build_entra_token_provider`,
MSAL client-credentials), `build_anthropic_client` (optional `base_url` for a Databricks-hosted
Claude endpoint), `PlainJsonHttp` (unauthenticated Teams incoming webhook).

### Environment / secret-scope config `job.py` reads
| Variable | Purpose |
|---|---|
| `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID`, `FABRIC_CLIENT_SECRET` | Entra SP client-credentials (REST collector) |
| `FABRIC_CAPACITY_URL`, `FABRIC_REFRESHES_URL`, `FABRIC_DATASETS_URL`, `FABRIC_REPORTS_URL`, `FABRIC_PIPELINES_URL`, `FABRIC_LINEAGE_URL`, `FABRIC_ACCESS_URL`, `FABRIC_USAGE_URL` | per-domain REST endpoints (unset → that domain is skipped) |
| `ANTHROPIC_API_KEY` | Claude reasoner (omit → KB-only reasoning) |
| `TEAMS_WEBHOOK_URL` | Teams push (one-way alerts/digests) |
| `AUDIT_HISTORY_PATH` | run-history JSON path (swap for a Delta store adapter) |
| `FABRIC_AUDIT_CONFIG` | optional JSON to `merge_config` over the detection thresholds |

On Databricks, hold these in a **secret scope** and surface them to the job as env vars (or read
via `dbutils.secrets` in a thin launcher).

---

## 2. Permissions you need (read-only; phase it)

### A. Identity — one Entra App Registration (service principal)
- `Fabric-PowerBI-Audit-Agent`; **client-credentials** flow (no user login). Client ID + Tenant
  ID + secret/cert → secret scope. **A Managed Identity cannot call the Power BI/Fabric APIs —
  only an Entra SP in an approved security group can.**

### B. Fabric & Power BI (read) — authorized via tenant settings, not just scopes
A **Fabric/Power BI admin** must, in Admin portal → Tenant settings:
- Enable **"Service principals can use Fabric APIs"** and **"… can use Power BI APIs."**
- Add the SP to the **approved security group** those settings reference.
- For tenant-wide auditing: **"Service principals can access read-only admin APIs"** (+ metadata
  scanning if used). Grant the SP **Viewer** on in-scope workspaces for non-admin reads.

Read scopes (standard APIs): `Tenant.Read.All`, `Workspace.Read.All`, `Report.Read.All`,
`Dataset.Read.All`, `Dashboard.Read.All`, `Dataflow.Read.All`, `Capacity.Read.All`, `Item.Read.All`.
**Capacity CU telemetry** comes from the **Fabric Capacity Metrics** app/dataset (or the
capacities metrics API / CSV export) — grant the SP access. *Note: the Capacity Metrics semantic
model is not supported for programmatic SPN query, so the CSV export importer is the supported
path for that data.*

### C. Azure Monitor / Log Analytics (read) — for the 30% feature's cost-weighted attribution
- Azure RBAC: **Reader**, **Monitoring Reader**, **Log Analytics Reader** on the relevant
  resources + **Log Analytics workspace(s)**.
- **Prerequisite:** Power BI/Fabric **diagnostic settings must route logs to Log Analytics**
  (the `ExecutingUser` + CPU/duration fields `collector_activity.fetch_log_analytics` reads).

### D. OneLake / Lakehouse (read) — **Storage Blob Data Reader** (or OneLake read role).
### E. Microsoft Graph (minimal, only if resolving names) — `User.ReadBasic.All`, `Group.Read.All`,
`Team.ReadBasic.All`. Do not request broad Graph permissions.
### F. Reasoner — an **Anthropic API key** in the secret scope (only sanitized, identifier-stripped
data is sent — see `sanitize.py`). Swap `base_url`/the adapter for a Databricks-hosted or
Azure-OpenAI model to keep everything in-tenant.
### G. Secrets — the secret scope holds: SP secret/cert · Anthropic key · Teams webhook · store
connection · any ITSM token. Nothing hardcoded.

---

## 3. Runtime on Databricks

- **Sweep (push):** a **Python-wheel Job task** whose entry point is `fabric_audit_agent.job:main`,
  on a schedule. It builds the real adapters from the secret scope and runs one read-only audit,
  posting findings to Teams and appending run history.
- **Pull (conversational):** run `fabric_audit_agent.mcp_server:main` (a **Databricks App** or a
  small always-on service) to expose `run_audit` over MCP for Copilot Studio / an MCP host.
- **Store:** persist findings/history/lifecycle in **Unity Catalog / Delta** (implement the
  `{history, append}` + `{load, save}` ports against a table). Network egress to the Microsoft
  APIs + Anthropic must be allowed by the workspace's egress policy.

---

## 4. The 30% concentration alert (User → Item → Owner) + Teams two-way

1. `collector_activity` pulls per-user activity for the window: **Activity Events**
   (`GetActivityEvents` → frequency ranking; interactive ops name the consumer, background ops
   name the owner) and optionally **Log Analytics** (`ExecutingUser` + CPU ms → cost-weighted
   ranking). It groups events per item and calls `attribution.enrich_items`.
2. `detectors/concentration` flags any item ≥ `config.capacity.concentrationPct` (30%) and writes
   **User-first** text — named users, else the owner for background-dominated load, else "pending
   correlation".
3. **Outbound:** `conversation.build_concentration_alert(finding)` → a Teams card (User → Item →
   Owner + Acknowledge / Snooze / Contact actions), posted via `delivery_teams`.
4. **Inbound (two-way):** `conversation.answer_question(text, envelope)` answers from the latest
   audit (verdict / who's driving CU / top fixes / health).
   > **Bot endpoint:** a **Databricks App cannot be the Bot Framework messaging endpoint** —
   > inbound Teams posts require the Bot Service OAuth handshake. Front the bot with an **Azure Bot
   > Service / Function** or a **Copilot Studio** topic that forwards user text to `answer_question`
   > and posts replies + alerts to the channel.

---

## 5. Setup order

1. **Global Admin:** register the Entra SP; secret/cert → Databricks secret scope.
2. **Fabric Admin:** enable the SP-for-Fabric/Power-BI tenant settings; add SP to the approved
   security group; enable read-only admin APIs; grant Viewer on in-scope workspaces; ensure
   Capacity Metrics access.
3. **Azure:** Reader / Monitoring Reader / Log Analytics Reader; route Power BI → Log Analytics
   diagnostics (for cost-weighted attribution); OneLake read.
4. **Provision Databricks:** secret scope, the Delta store, the wheel Job (`job:main`), egress
   policy; (pull) the MCP App.
5. **Wire adapters:** set the env vars in §1; confirm `collector_rest`/`collector_activity` field
   mappings against the live APIs.
6. **Surfaces:** Teams webhook (push) + (optional) Bot Service / Copilot Studio (two-way pull).
7. **Schedule + pilot:** run against **one workspace first**, confirm diagnoses match a known
   incident, then widen scope.

---

## 6. Rollout phases (smallest first)

- **Phase 1 — local engine test (no cloud):** run `import`/`inspect` on a real Capacity Metrics
  CSV export. No SP, no tenant changes — validates the engine on real numbers.
- **Phase 2 — single-workspace SP connectivity test (local, read-only):** register the SP, scope
  it to **one** workspace (tenant setting for a 1-SP security group + Viewer on that workspace),
  then run `python -m fabric_audit_agent.connectivity <workspaceId>` to prove authentication +
  workspace read — **no Databricks yet.** See **PHASE2-SP-TEST.md**.
- **Phase 3 — Databricks deployment (scheduled sweep):** secret scope, wheel, Job (`job:main`),
  Teams delivery — still scoped to the pilot workspace(s). See **PHASE3-DATABRICKS.md** (step-by-step)
  and §§1, 3, 5.
- **Phase 4 — widen + interactive:** read-only admin APIs (estate-wide) + Azure Monitor/Log
  Analytics (cost-weighted attribution) + OneLake read; the MCP/Copilot pull surface + Bot Service
  two-way; ITSM ticketing.

Never request Contributor/Owner, write/delete, or infra-modification permissions. One SP, scoped,
read-only — that is what gets enterprise sign-off.

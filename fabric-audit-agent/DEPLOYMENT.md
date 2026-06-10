# Fabric Audit Agent — Deployment & Permissions

This package is **self-contained**: copy this folder into your environment, `npm install`, swap the mock adapters for real ones, and deploy. It has no dependency on any external build system — only Node ≥ 20 and `@anthropic-ai/sdk`.

> **Posture:** the agent is **read-only** on your data plane. It reads telemetry/metadata and *advises*. The only outward "writes" are (a) its own findings into its own store, and (b) notifications it sends. It never edits, refreshes, scales, or deletes anything in your estate.

> **Note on exact scope names:** Microsoft's API surface evolves. Treat the scope/role names below as the current shape and confirm against Microsoft Learn at setup time.

---

## 1. What you deploy (swap mocks → real)

The functional core in `core/` is untouched. You implement four real adapters against the same port interfaces the mocks use:

| Port (mock today) | Replace with |
|---|---|
| `adapters/collector.mock.js` | `adapters/collector.rest.js` — real Power BI Admin / Fabric / Azure Monitor REST + the remaining domain mappers in `core/mappers/` |
| `adapters/reasoner.stub.js` | `adapters/reasoner.claude.js` — Claude via `@anthropic-ai/sdk` (sanitizes before any call) |
| `adapters/delivery.file.js` | `adapters/delivery.teams.js` (Teams webhook) and/or `adapters/ticketing.js` (ITSM) |
| `adapters/store.local.js` / `lifecycle.store.js` | a real store adapter (Azure SQL / Cosmos DB / Azure DB for PostgreSQL / Fabric Lakehouse) |

`mcp.config.json` holds the MCP server config for the conversational pull surface; `core/data-agent.js` produces the tool manifest for Copilot Studio / MCP.

---

## 2. Permissions you need (request read-only; phase it)

### A. Identity — one Entra App Registration (service principal)
- App name e.g. `Fabric-PowerBI-Audit-Agent`; **client-credentials** flow (no user login).
- Client ID + Tenant ID + a **client secret or certificate** → stored in **Azure Key Vault**.

### B. Fabric & Power BI (read) — *authorized via tenant settings, not just scopes*
A **Fabric/Power BI admin** must, in the Admin portal → Tenant settings:
- Enable **"Service principals can use Fabric APIs"** and **"Service principals can use Power BI APIs."**
- Add the service principal to the **approved security group** those settings reference.
- For tenant-wide auditing: enable **"Service principals can access read-only admin APIs"** (and the metadata-scanning setting if you use the scanner). *Admin read is granted by these settings + the security group — not by consenting granular scopes.*
- Grant the SP **Viewer** on the specific workspaces in scope (for non-admin reads).

Relevant Power BI/Fabric read scopes (when using the standard, non-admin APIs):
`Tenant.Read.All`, `Workspace.Read.All`, `Report.Read.All`, `Dataset.Read.All`, `Dashboard.Read.All`, `Dataflow.Read.All`, `Capacity.Read.All`, `Item.Read.All`.

**Capacity CU telemetry** (the throttling data) comes from the **Fabric Capacity Metrics** app/dataset or the capacities metrics API — grant the SP access to that source.

### C. Azure Monitor / logs (read)
- Azure RBAC: **Reader**, **Monitoring Reader**, **Log Analytics Reader** on the relevant subscription(s)/resource group(s) and **Log Analytics workspace(s)**.
- **Prerequisite:** Power BI/Fabric **diagnostic settings must be routing logs to a Log Analytics workspace** — the agent can't query logs that aren't exported. Turn this on per workspace (capacity feature) if you want KQL-based analysis.

### D. OneLake / Lakehouse (read)
- **Storage Blob Data Reader** (or the equivalent OneLake read role) for lake volume/metadata/freshness checks.

### E. Microsoft Graph (minimal, only if needed)
- `User.ReadBasic.All`, `Group.Read.All`, `Team.ReadBasic.All` — to resolve readable names for output + routing. Do **not** request broad Graph permissions.

### F. Delivery
- **Push (Teams):** a **Teams incoming webhook** URL for the target channel — *no Graph permission, no bot needed* for alerts/digests.
- **Pull (conversational):** if surfacing via Power BI/Copilot, enable the **"Users can use the Power BI MCP endpoint (preview)"** tenant setting and/or build a **Copilot Studio** agent that calls this backend. (See §4.)
- **Ticketing (optional):** a service account / token for Jira / Azure DevOps / ServiceNow.

### G. Reasoner (Claude)
- An **Anthropic API key** in Key Vault. Only **sanitized, summarized** data is sent (identifiers stripped — see `core/sanitize.js`). If you must keep everything in-tenant, the reasoner is a port — you can swap in **Azure OpenAI** instead.

### H. Secrets — Azure Key Vault holds:
SP client secret/cert · Anthropic API key · Teams webhook URL · store connection string · any ITSM token. Nothing hardcoded.

---

## 3. Runtime — pick one

- **Inside Fabric (most native):** a scheduled **Notebook** (Python) + a **Data pipeline** that runs the audit and writes findings to a **Lakehouse** table; a Power BI report sits on top.
- **In Azure (most flexible):** a timer-triggered **Azure Function** for the scheduled sweep, or a **Container App** if you also host the always-on conversational endpoint. Use a **Managed Identity** + Key Vault.

Storage for findings/history/lifecycle: **Azure SQL / Cosmos DB / Azure DB for PostgreSQL**, or a **Fabric Lakehouse/Warehouse** table.

---

## 4. Surfaces

- **Push → Teams:** wire `delivery.teams.js` to the incoming webhook. Posts critical alerts immediately + scheduled digests.
- **Pull → Power BI / Copilot:** simplest is a **Power BI report over the findings table** + the built-in Copilot. For a chatbot, build a **Copilot Studio** agent (lives in Teams/M365) or **expose this backend via MCP** so Copilot invokes `run_audit`. (`core/data-agent.js` generates the manifest.)

---

## 5. Setup order

1. **Global Admin:** register the Entra app (SP); create secret/cert → Key Vault.
2. **Fabric Admin:** enable the SP-for-Fabric/Power-BI tenant settings; add SP to the approved security group; enable read-only admin APIs; (optional) Power BI MCP + Copilot tenant settings; grant Viewer on in-scope workspaces.
3. **Azure:** grant Reader / Monitoring Reader / Log Analytics Reader; configure Power BI → Log Analytics diagnostics; grant OneLake read.
4. **Graph:** admin-consent the minimal read scopes (§E).
5. **Provision:** Key Vault, the store, and the runtime (Function/Container App or Fabric notebook + Managed Identity).
6. **Wire adapters:** `collector.rest` (auth via SP), `reasoner.claude` (key), `delivery.teams` (webhook), real `store`. Complete the remaining `core/mappers/` against the live API field names.
7. **Surfaces:** set up the Teams webhook (push) and/or Copilot Studio agent / MCP (pull).
8. **Schedule + pilot:** run against **one workspace first**, confirm the diagnoses match a known incident, then widen scope.

---

## 6. Phased access request (easier approval)

- **Phase 1 (prove it):** SP + Viewer on 1–2 workspaces + Capacity Metrics read + Anthropic key + a Teams webhook. Validate diagnoses on one real incident.
- **Phase 2 (estate-wide):** read-only admin APIs + Azure Monitor/Log Analytics + OneLake read.
- **Phase 3 (interactive + ops):** Power BI MCP / Copilot Studio pull surface; ITSM ticketing.

Never request Contributor/Owner, write/delete, or infra-modification permissions. One SP, scoped, read-only — that's what gets enterprise sign-off.

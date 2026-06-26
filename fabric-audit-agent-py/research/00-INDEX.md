# Research Library — Master Index

**Project:** bi-fabrics-audit-agent — a **read-only** Microsoft Fabric / Power BI capacity &
performance audit agent. Detects throttling / oversized models / refresh contention; gives an
evidence-backed **optimize-vs-size-up** verdict; runs the **30% concentration alert** (names
**User → Item → Owner**). Built in the user's env, runs in the company's Databricks. *Read-only is absolute.*

**What this is:** 22 deep, citation-rich research files (~848 KB, ~800+ unique official-doc URLs)
produced by parallel research agents over four waves, **2026-06-22 / 06-23** (the project's
reference date). Each file is self-contained: per-item TITLE · URL · summary · exact
identifiers/scopes/queries · a "how it helps the audit agent" note · a flat URL list at the end.
This index ties them together, records the **architecture that emerged**, and lists the
**concrete changes** the research surfaced for the build. (Parts 1–3 — the earlier scopes/telemetry/
REST/Databricks-hosting/Data-Agent reference — sit alongside this in the project chat.)

---

## 1. The architecture that emerged (synthesis)

The research converges on a concrete target design. Each claim links to the file that backs it.

- **Identity** — Entra **service principal** (client-credentials/MSAL). Distinct token audiences:
  Power BI `analysis.windows.net/powerbi/api/.default`, Fabric `api.fabric.microsoft.com/.default`,
  ARM `management.azure.com/.default`, Kusto `{queryServiceUri}/.default`. Managed Identity **cannot**
  call Power BI. **Workload Identity Federation** can make Databricks→Azure auth *secretless* (08).
- **Collection (layered, read-only):**
  1. **FUAM gold Lakehouse over OneLake** — *primary* source for **Item → Owner + per-item historical CU**
     and the **User** leg (`activities`). Read it; don't re-collect. (22)
  2. **Workspace Monitoring `SemanticModelLogs`** — per-user **ranking** via `CpuTimeMs` by
     `ExecutingUser` (a CPU *proxy*, SP-queryable via KQL). Column is **`ItemName`**, not `ArtifactName`. (14, 13)
  3. **Capacity Overview Events → Eventhouse** — authoritative **live capacity CU% + throttle state** (30s). (15, 16)
  4. **Activity Events** — tenant-wide operation **counts** to corroborate the user leg (28-day window). (14)
  5. **Capacities REST** — SKU / state metadata (already wired; the current "thin" result). (Part 3)
  6. **(Optional) sempy SP-in-Fabric notebook** — programmatic **VertiPaq + BPA + DAX** (SP auth ≥0.12.0, Fabric-only). (21, 17)
- **Storage** — **Eventhouse** (KQL) for capacity time-series; **Lakehouse** (Delta) for run-history +
  curated reporting; **OneLake** single-copy + shortcuts so Databricks *and* Power BI read the same bytes. (15)
- **Reasoning** — Claude via the in-tenant **Databricks-hosted endpoint** (`databricks-claude-opus-4-7`,
  fall back to `-opus-4-8`/`-sonnet-4-6`). Prompt-cache the system prompt; sanitize (no names) before any call. (18)
- **Surfaces** —
  - **MCP server** in a Databricks **App** (name must start `mcp-`; streamable-HTTP `/mcp`; ephemeral FS → write-free tool). (Part 3, 18)
  - **Mosaic AI agent** connects to it as a tool; the MCP connector also lets Claude call `run_audit` directly. (05, 18)
  - **Near-real-time alerting = Capacity Overview Events → Eventhouse → Activator** (fires natively on a
    CU%/throttle threshold; Reflex REST + SP). Fabric capacities expose **no Azure Monitor metrics**, so this is the path. (16, 09)
  - **Teams** — one-way now via Power Automate **Workflows** webhook + **Adaptive Cards**; two-way later via
    **Azure Bot Service / M365 Agents SDK** (Bot Framework SDK is archived). (12, 09)
- **Verdict economics** — live **Azure Retail Prices API**: PAYG **$0.18/CU/hr**, F64 ≈ **$11.52/hr (~$8,410/mo)**,
  1-yr reservation **~40.5% off**, overage at **3× PAYG** → size-up wins above ~⅓ spike duty cycle. (20)
- **Runtime choice** — Databricks today; a **Fabric notebook/Spark-job** runtime is viable and the auth code
  is portable, but in-Fabric SP tokens give only ~7 item scopes (admin reads still need MSAL+SP). (19)

---

## 2. File index

**Databricks — compute & data**
- [01-databricks-sql.md](01-databricks-sql.md) — SQL Warehouses, **Statement Execution API**, databricks-sql-connector, AI Functions (`ai_query`/`ai_forecast`), SQL alerts/dashboards.
- [02-databricks-federation-connections.md](02-databricks-federation-connections.md) — Lakehouse Federation, `CREATE CONNECTION`, foreign catalogs, supported sources, Fabric/PBI federation reach.
- [03-databricks-jobs-pipelines.md](03-databricks-jobs-pipelines.md) — Jobs/Workflows task types, triggers (file_arrival/periodic/table), serverless, params, notifications, Jobs REST, Lakeflow DLT.
- [04-delta-lake.md](04-delta-lake.md) — Delta tables: CREATE/MERGE, time travel, schema evolution, Liquid clustering, Python/SQL writes — for `run_history` + reporting tables.
- [05-databricks-aiml.md](05-databricks-aiml.md) — MLflow 3 tracing/eval, UC model registry, Agent Evaluation/Monitoring, Vector(AI) Search, Genie, AI/BI dashboards, Unity AI Gateway.
- [06-databricks-governance-system-tables.md](06-databricks-governance-system-tables.md) — UC privilege model (GRANT), SPs + OAuth M2M, **system tables** (billing/access/query/lakeflow), Lakehouse Monitoring, IP access lists.
- [07-databricks-azure-fabric-onelake-integration.md](07-databricks-azure-fabric-onelake-integration.md) — Databricks↔OneLake (abfss/shortcuts), Fabric mirroring, Power BI connector, **egress/NCC/Private Link** to reach MS + Anthropic APIs.

**Azure — identity, platform, observability**
- [08-azure-identity-rbac-keyvault.md](08-azure-identity-rbac-keyvault.md) — app registration, secret/cert/**federated credentials (WIF, secretless)**, RBAC roles for `Microsoft.Fabric/capacities`, Key Vault.
- [09-azure-monitor-functions-bot-networking.md](09-azure-monitor-functions-bot-networking.md) — **Fabric capacities have NO Azure Monitor metrics**; PowerBIDedicated does; action groups; Functions/Logic Apps; **Azure Bot Service** for two-way Teams; Private Link.
- [13-azure-log-analytics-deep.md](13-azure-log-analytics-deep.md) — **`PowerBIDatasetsWorkspace`** table full schema (`ExecutingUser`/`CpuTimeMs`/`Artifact*`), `ExecutionMetrics` JSON, KQL patterns, cost/retention, can't-have-both vs Workspace Monitoring, `azure-monitor-query`.

**Microsoft Fabric — platform & real-time**
- [10-fabric-onelake-eventhouse-capacity-git-governance.md](10-fabric-onelake-eventhouse-capacity-git-governance.md) — OneLake security/RBAC/shortcuts, **capacity ARM mgmt** (suspend/resume/scale, F2–F2048), Git/deployment pipelines, admin REST (Tenant Settings/Domains/External Data Shares), workspace identity.
- [15-eventhouse-lakehouse-warehouse-storage.md](15-eventhouse-lakehouse-warehouse-storage.md) — **Eventhouse vs Lakehouse vs Warehouse** decision matrix, OneLake availability (≤3h latency, tunable), retention/caching, KQL/T-SQL/Spark examples.
- [16-fabric-rti-eventstream-activator.md](16-fabric-rti-eventstream-activator.md) — **Activator fires on CU% threshold natively** (exact tutorial); Eventstream sources/destinations; Reflex REST + SP; CI/CD gotchas; Real-Time dashboards.
- [19-fabric-data-engineering-runtime.md](19-fabric-data-engineering-runtime.md) — Fabric notebooks/NotebookUtils, Spark Job Definitions, Job Scheduler REST, Data Factory pipelines, Environments; **Fabric-vs-Databricks** runtime comparison + token-scope limits.
- [20-fabric-capacity-economics-sku-sizing.md](20-fabric-capacity-economics-sku-sizing.md) — F-SKU ladder + CU/sec, **Azure Retail Prices API**, PAYG vs reservation, F64 cliff, overage 3×, CU-seconds math — the **$ model for the verdict**.

**Power BI**
- [11-powerbi-rest-embedding-gateways-governance.md](11-powerbi-rest-embedding-gateways-governance.md) — non-admin REST (datasets/refreshes/gateways), Embedded + embed tokens, deployment pipelines, Dataflows Gen2/datamarts, gateways, sensitivity labels/endorsement, SKU/autoscale.
- [17-semantic-model-dax-vertipaq.md](17-semantic-model-dax-vertipaq.md) — VertiPaq encoding/cardinality, **`.vpax` structure**, VertiPaq Analyzer metrics, **~30 BPA rules + `BPARules.json`**, DAX anti-patterns, Direct Lake guardrails — a **detector cheat sheet**.

**Per-user CU% (the crux) & prior art**
- [14-per-user-cu-attribution-methods.md](14-per-user-cu-attribution-methods.md) — **all 8 methods ranked**; no SP-queryable *true* per-user CU; best = capacity-level true CU% + per-user `CpuTimeMs` proxy + Activity Events + FUAM. Comparison matrix.
- [22-fuam-prior-art-accelerators.md](22-fuam-prior-art-accelerators.md) — **FUAM gold tables give Item→Owner+CU + activities over OneLake**; Chargeback app; integrate-vs-reimplement verdict; this agent's differentiation.
- [21-semantic-link-labs-sempy.md](21-semantic-link-labs-sempy.md) — `sempy`/`sempy_labs` programmatic VertiPaq/BPA/DAX/TOM/admin; **SP auth works ≥0.12.0 but only inside Fabric**.

**Agent surface & reasoner**
- [18-mcp-protocol-anthropic-claude-api.md](18-mcp-protocol-anthropic-claude-api.md) — MCP spec (tools/resources/prompts, `readOnlyHint`, streamable-HTTP, OAuth), FastMCP patterns, Anthropic tool-use loop, **prompt caching**, structured outputs, MCP connector.
- [12-teams-delivery-bot-adaptivecards-graph.md](12-teams-delivery-bot-adaptivecards-graph.md) — Workflows webhook (O365 connector retired), Adaptive Cards schema, Azure Bot Service/proactive messaging, Graph chatMessage perms — one-way now, two-way later.

---

## 3. Concrete changes the research surfaced (actionable)

**Quick / config:**
1. **Rename the App** `fabric-audit-mcp` → **`mcp-fabric-audit`** so the AI Playground recognizes it as an MCP server. (Part 3, 18)
2. **Fix the Workspace Monitoring KQL** in `collector_workspace_monitoring`: `ArtifactName` → **`ItemName`** (Workspace Monitoring schema; `ArtifactName`/`PowerBIDatasetsWorkspace` is the *Log Analytics* path). (13, 14)
3. **Set `readOnlyHint=True`** explicitly on the `run_audit` MCP tool (default is `false`); keep the in-code read-only enforcement (annotations are untrusted hints). (18)

**High-leverage / architectural (design decisions for the project chat):**
4. **Integrate FUAM** — read its gold `FUAM_Lakehouse` (`Load_Inventory_E2E` item→owner, `capacity_metrics_by_item_by_operation_by_day`, `activities`) over OneLake as the primary User→Item→Owner+CU source; keep the live collector as fallback. *Biggest single unlock.* (22, 14)
5. **Adopt Capacity Overview Events → Eventhouse → Activator** as the native near-real-time alerting layer (provision the Reflex rule via REST with the SP); the agent's brain adds who/why/verdict. (16, 09, 15)
6. **Add `top_users` + `capacity_usage` MCP tools** so the agent answers "who used what / total CU today" directly (backed by Workspace Monitoring + FUAM). (14, 18)
7. **Wire the Azure Retail Prices API** for live $ in the optimize-vs-size-up verdict. (20)
8. **Consider an SP-triggered Fabric notebook** running `sempy_labs` for VertiPaq/BPA/DAX (the off-Fabric `.vpax` parse stays the Databricks-side fallback). (21, 17)
9. **Prompt-cache the Claude system prompt**; consider structured-output for findings. (18)
10. **Honor the user-masking tenant setting** when naming users in the concentration alert (governance posture). (22)

---

## 4. Diminishing returns — what's left (low value)

The high-value space is saturated. Remaining veins are marginal and mostly *implementation glue*, not
discovery: GitHub Actions CI/CD for the Asset Bundle deploy; Databricks **Lakebase** (OLTP Postgres) for
app state; Purview **lineage/DLP** detail; Power Automate connector minutiae; Fabric **SQL database** (new
transactional item). Say the word and I'll mine any of these — otherwise the research phase is complete.

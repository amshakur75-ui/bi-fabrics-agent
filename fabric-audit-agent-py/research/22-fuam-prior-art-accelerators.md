# 22 — FUAM + Microsoft/Community Accelerators & Prior Art

**Research focus:** Map the prior art (FUAM, Fabric Toolbox accelerators, the Capacity Metrics / Chargeback / legacy Premium apps, community FinOps & capacity-audit tooling) so the **bi-fabrics-audit-agent** INTEGRATES/reuses rather than reinvents — and to sharpen the agent's differentiation.

**Date:** 2026-06-23 · **Reference snapshot:** 2026-06-22

**Scope guard (already covered elsewhere — referenced, NOT re-derived here):** Capacity Metrics app internals (file ~11/20), Workspace Monitoring, Activity Events, Real-Time Hub Capacity Overview Events, and the per-user CU% method comparison (file 14 — FUAM's role is referenced below, the matrix is not redone).

**Headline answer (item→owner):** **YES — FUAM gives the agent item→owner data it can read directly from FUAM's Lakehouse over OneLake, without re-collecting.** FUAM's `Load_Inventory_E2E` (Scanner API) persists item metadata including `configuredBy` / `configuredById` (and `createdById` / a `users[]` access array) into the gold `FUAM_Lakehouse` delta tables (`semantic_models`, `reports`, `dataflows`, `warehouses`, etc.), and FUAM's `Load_Capacity_Metrics_E2E` persists **per-item CU** in `capacity_metrics_by_item_by_operation_by_day`. Joining those two on item id yields **Item → Owner + CU** out of FUAM's gold layer. The User→Item leg (who drove the operations) comes from FUAM's `activities` table and/or the Microsoft Fabric **Chargeback app** (User-level CU, drill workspace→item, hover→user). What FUAM does **NOT** provide — and what the agent adds — is the reasoning layer: throttling/oversize verdicts, the optimize-vs-size-up call, alerting, and the 30% User→Item→Owner concentration alert.

---

## 1. FUAM — Fabric Unified Admin Monitoring

**TITLE:** Fabric Unified Admin Monitoring (FUAM) — `microsoft/fabric-toolbox/monitoring/fabric-unified-admin-monitoring`
**URLs:**
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/README.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Architecture.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_deploy_FUAM.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_update_FUAM.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Core_Report.md
- https://deepwiki.com/microsoft/fabric-toolbox/3.1-fabric-unified-admin-monitoring-(fuam)
- https://deepwiki.com/microsoft/fabric-toolbox/3.1.3-fuam-usage-and-capabilities

**Summary:** FUAM is a free, community-driven (Fabric CAT) solution that delivers **holistic, tenant-wide monitoring** on top of Microsoft Fabric. It collects monitoring signals via Fabric REST/Admin APIs, the M365 audit log, and the Capacity Metrics App semantic model, lands them in a medallion-architecture Lakehouse (raw + Delta Parquet), and surfaces them through **DirectLake** semantic models and pre-built Power BI reports. It is Fabric-native: built entirely from Pipelines, Notebooks, Lakehouses, Semantic Models, and Reports, and is intentionally **modular/extensible**. Latest releases observed: **2026.2.1** (prior 2026.1.1, 2025.9.1).

### 1.1 What it collects — modules (`Load_*_E2E`, orchestrated by `Load_FUAM_Data_E2E`)

| Module | Source | What it lands |
|---|---|---|
| `Load_Capacities_E2E` | Admin API | `capacities`, `capacity_users` (capacity config + admin/user assignments) |
| `Load_Workspaces_E2E` | Admin API | `workspaces` (excludes personal workspaces) |
| `Load_Activities_E2E` | Activity Events / audit log (2–28 day configurable window) | `activities`, `aggregated_activities_last_30days` |
| `Load_Active_Items_E2E` | derived | `active_items` |
| `Load_Inventory_E2E` | **Scanner API (`workspaces/getInfo`)** | ~15 metadata tables: `semantic_models`, `reports`, `dashboards`, `dataflows`, `lakehouses`, `warehouses`, `notebooks`, `pipelines`, etc. — **with owner fields** (`configuredBy`/`configuredById`, `createdById`, `users[]` access rights) |
| `Load_Capacity_Metrics_E2E` | **Capacity Metrics App** semantic model (DAX via `sempy`/Semantic Link Labs) | `capacity_metrics_by_timepoint`, `capacity_metrics_by_item_kind_by_day`, **`capacity_metrics_by_item_by_operation_by_day`** |
| `Load_Capacity_Refreshables_E2E` | Refreshables API | `capacity_refreshables`, `capacity_refreshable_days`, `capacity_refreshable_details` (scheduled semantic-model refresh history/telemetry) |
| `Load_Tenant_Settings_E2E` | Admin API | `tenant_settings` |
| `Load_Delegated_Tenant_Settings_Overrides_E2E` | Admin API | `delegated_tenant_settings_overrides` |
| `Load_Git_Connections_E2E` | Fabric API | `git_connections` |
| `Generate_Calendar_Table` (notebook) | derived | `calendar` (time-intelligence helper) |

**Notebooks use Semantic Link Labs / `sempy`** to call the APIs and to query the Capacity Metrics App semantic model via DAX (this is the same well-known mechanism the agent's per-user CU file 14 discusses).

### 1.2 Data model / Lakehouses

Medallion architecture across **four** Lakehouses:
- **`FUAM_Lakehouse`** — gold; all curated Delta-Parquet tables (the join target for the agent).
- **`FUAM_Staging_Lakehouse`** — intermediate, no retention.
- **`FUAM_Config_Lakehouse`** — deployment config.
- **`FUAM_Backup_Lakehouse`** — backup of raw + parquet.

Layers: **Bronze** (raw API files) → **Gold** (curated Delta in `FUAM_Lakehouse`), staging in between.

**Semantic models (DirectLake-only):**
- **`FUAM_Core_SM`** — primary, time-based; relates fact tables (`activities`, `capacity_metrics_by_timepoint`, `tenant_settings`) to `calendar`.
- **`FUAM_Item_SM`** — item-centric model behind the Item Analyzer.
- Engine-level analyzer models (semantic-model metadata, SQL endpoint).

### 1.3 Reports / pages

- **`FUAM_Core_Report`** — flagship. Pages/sections: Core (capacity count + regional distribution), **Capacities** (operations 14d, cancelled/failed ops, avg CU 14d/30d, trends), **Capacity Compute** (matrix per item on the capacity; usage + **throttling analysis** for the selected capacity), **Refreshables** (median semantic-model refresh duration), **Users** (workspace/item accesses, unique active users 14d/30d), Connections, Widely Shared Objects, Report Usage, External Applications, Items & Workspaces, Domains, Tenant Settings.
- **`FUAM_Item_Analyzer_Report`** (on `FUAM_Item_SM`) — per-item performance trends, item-specific CU consumption, refresh patterns, query patterns.
- **`FUAM_Semantic_Model_Meta_Data_Analyzer_Report`** — VertiPaq-style model structure deep-dive.
- **`FUAM_SQL_Endpoint_Analyzer_Report`** — warehouse/SQL endpoint query analysis.
- **`FUAM_Gateway_Monitoring_Report_From_Files`** — on-prem gateway diagnostics.

### 1.4 Deployment, prerequisites, cadence

- **Deploy** by importing/running **`Deploy_FUAM.ipynb`**; it auto-creates the workspace items (pipelines, notebooks, lakehouses, semantic models, reports) and two cloud connections (credentials added after via Manage connections and gateways). Configure Capacity Metrics App workspace + semantic-model names, then run `Load_FUAM_Data_E2E`, refresh `FUAM_Core_SM`/`FUAM_Item_SM`, and **schedule the pipeline for daily incremental loads**.
- **Prereqs:** a **Fabric/Power BI capacity (F or P SKU)** for the FUAM workspace; a **Service Principal (client secret)** in groups with "Service principals can use Fabric APIs" + "…access read-only admin APIs"; a **permanent Fabric Administrator** identity; tenant settings "Users can create Fabric items" and **XMLA endpoint** enabled (Capacity Metrics App must sit on an F/P capacity with XMLA on; compatible Metrics App versions noted v65/v53/v47/v44 or earlier). Optional Azure Key Vault for secrets.
- **Execution-identity constraint:** notebooks run as the **notebook owner's identity**; the deployer should be the pipeline scheduler.
- **Cadence:** initial load uses a larger window (≈14 days of activity/metrics); incremental runs ≈2 days. **Update** = re-run the deployment notebook (overwrites items by name).
- **Cost:** "FUAM items only consume your capacity in CUs" — driven by pipeline frequency and report viewers (i.e., FUAM itself adds CU load to the monitored/au­diting capacity).

### 1.5 What FUAM already solves (reusable for the agent)

- **Per-item historical CU** (`capacity_metrics_by_item_by_operation_by_day`, `_by_item_kind_by_day`, `_by_timepoint`) — the agent does NOT need to re-pull the Metrics App via DAX if FUAM is deployed.
- **Item→owner** via Scanner inventory (`configuredBy`/`configuredById`, `createdById`, `users[]`) — joinable to per-item CU on item id. **This is the item→owner leg the agent needs.**
- **Refresh telemetry** (`capacity_refreshables*`) — for refresh-contention/oversize-model signals.
- **Activity history** (`activities`, `aggregated_activities_last_30days`) — the User→Item leg of the concentration alert.
- **Throttling surfacing** (Capacity Compute page) and capacity operation/CU aggregates.
- Tenant settings, domains, git, gateways — governance context.
- It is **OneLake-resident Delta** → the agent can read it via OneLake/SQL endpoint with read-only credentials (matches the agent's read-only posture).

### 1.6 Gaps (what FUAM does NOT do)

- **No advisory verdicts / recommendations** — it visualizes; it does not say "optimize vs size up," nor rank fixes.
- **No alerting** — periodic batch, daily-refreshed; no proactive notifications.
- **No concentration / blast-radius logic** — no built-in "X% of CU concentrated in one User→Item→Owner" rule.
- **Capacity Utilization Events mode "coming in the future"** — i.e., its throttling/CU detail today is Metrics-App-derived, not the newer raw utilization-events stream (see file 16 / RTI).
- **User-level CU attribution is not a first-class FUAM model** — owner is in inventory and user is in activities, but FUAM does not pre-compute a CU-by-user fact (the **Chargeback app** is the better source for User-level CU; see §3).
- It is a **deployed footprint** that itself consumes CU on a capacity, plus SP/admin setup overhead.

### 1.7 Integrate vs reimplement — **INTEGRATE (read FUAM's gold Lakehouse over OneLake)**

If FUAM is present in the tenant, the agent should treat `FUAM_Lakehouse` as a primary read source: per-item CU + inventory(owner) + refreshables + activities are all there in Delta. The agent **reimplements only the intelligence layer** on top (verdict, concentration alert, advisory), and keeps a fallback live-collector path (file 14 method) for tenants without FUAM. This avoids re-deriving the per-item CU pipeline and the Scanner inventory join.

---

## 2. Fabric Cost Analysis (FCA) — sibling accelerator (FinOps)

**TITLE:** Fabric Cost Analysis (FCA) — `microsoft/fabric-toolbox/monitoring/fabric-cost-analysis`
**URLs:**
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-cost-analysis/README.md
- https://deepwiki.com/microsoft/fabric-toolbox/3.3-fabric-cost-analysis-(fca)
- https://www.jamesserra.com/archive/2025/12/fabric-cost-analysis-fca/
- https://blog.robsewell.com/blog/fca-fabric-cost-analysis-for-finops/
- https://sqlyard.com/2026/01/12/fabric-cost-analysis-explained-bringing-clarity-to-microsoft-fabric-costs/

**Summary:** Free/open-source FinOps accelerator (built by Fabric CAT FinOps experts, led by Romain Casteres). Brings Fabric **cost** signals into one explainable model using the **FOCUS** standard (FinOps Open Cost and Usage Specification) exported from **Azure Cost Management → ADLS Gen2**, consumed into Fabric via a OneLake shortcut.

**What it collects:** Fabric meter consumption + **$ cost** (Fabric CU, Power BI, Spark, Data Warehouse meters), **reservation** usage/savings, capacity utilization by region, YTD cost. Tables: `focus_fabric` (enriched fact), `meters` (dim), `calendar`, `audit_latest_available_fca_version`. DirectLake semantic model. Pages: Home/Summary (cost by capacity/region), Capacity Usage, Reservation, Cost Detail; supports **chargeback/showback** and **cost by workspace / SKU / billing meter** over time.

**Deployed via** `00_Deploy_FCA.ipynb`; requires the Azure Cost Management FOCUS export + a OneLake shortcut to the ADLS export.

**Gaps:** Adds the **$ / billing** dimension FUAM lacks, but is cost-centric — it does **not** do per-item CU operation detail, item→owner attribution, throttling verdicts, or alerting. Granularity is capacity/meter/workspace (item/owner-level allocation not documented).

**Integrate vs reimplement:** **OPTIONAL INTEGRATE** for the agent's "size-up costs $X/mo" framing — if the agent's verdict recommends sizing up, FCA's `focus_fabric` gives real $ to attach to the recommendation (and reservation context for the buy-vs-PAYG nuance, see file 20). Reuse, don't rebuild, the cost/FOCUS layer.

---

## 3. Microsoft Fabric Chargeback app (first-party)

**TITLE:** Microsoft Fabric Chargeback app
**URLs:**
- https://learn.microsoft.com/en-us/fabric/enterprise/chargeback-app
- https://github.com/MicrosoftDocs/fabric-docs/blob/main/docs/enterprise/chargeback-app.md

**Summary:** First-party installable app that shows **which teams, users, and workloads drive capacity usage** to support fair chargeback. Visuals: capacity utilization % by **Workspace / Item / Domain** tabs; **Utilization (CU) by date**; a **Utilization (CU) details matrix with user details — hover a workspace/item to see CU broken down by user**. Drill-through to Workspace / Item / Domain detail pages. Data export to matrix (slicer can add Item name, user, etc.).

**What it uses:** Capacity Metrics data, **refreshed daily** (not real-time). Honors the tenant "**Show user data in the Fabric Capacity Metrics app**" setting — when disabled, users show as "**Masked user**" (counted as one); SP/unattributed ops show as "**Power BI Service**."

**Gaps:** First-party visualization only — **no advisory, no alerting, no concentration rule**, no programmatic API; it's a report, not a service. Export hits Power BI memory limits at item granularity.

**Integrate vs reimplement:** This is the **closest prior art to the agent's User→Item→Owner concentration alert** — it already exposes CU-by-user-by-item-by-workspace. The agent should **reuse the concept and the underlying Metrics-App data path** but adds: (a) the **Owner** dimension (Chargeback shows the *user who ran* ops, the agent additionally maps to the **item owner** via inventory `configuredBy`), and (b) the **30% concentration alert + verdict** that Chargeback does not compute. Note the same **user-masking** caveat applies to the agent (cite in agent's limitations).

---

## 4. Microsoft Fabric Capacity Metrics app + legacy Premium app

**TITLE:** Microsoft Fabric Capacity Metrics app (and deprecated "Premium Capacity Utilization And Metrics")
**URLs:**
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-health-page
- https://learn.microsoft.com/en-us/fabric/enterprise/throttling
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-install
- https://marketplace.microsoft.com/en-us/product/power-bi/pbi_pcmm.microsoftpremiumfabricpreviewreport

**Summary (deltas only — internals covered in file ~11):** The **Fabric Capacity Metrics app** is the single first-party tool for all PBI/Fabric SKUs. Health page = high-level overview across capacities, flags top consumers / throttling / rejections; **Compute page** = 14-day compute, ribbon/utilization/operations matrix, **Throttling chart** (key: utilization >100% ≠ throttling — must read the Throttling chart; the Throttling(s) column reads 0 when throttling is disabled even if overloaded); **Storage page** = 30-day storage by workspace. It is the data source FUAM and Chargeback pull from.

**Legacy "Premium Capacity Utilization And Metrics":** **deprecated and replaced** (deprecation message targeted ~Oct 1) by the unified Fabric Capacity Metrics app. **Relevant to the agent only as a fallback-it-may-still-find** in older tenants — treat as legacy; do not build against it.

**Gaps / integrate:** Metrics app is a **report, not an API/service** — no alerting, no verdict, semantic model "not supported for external use." The agent reuses its **data** (directly via DAX/`sempy`, file 14, or indirectly via FUAM) but provides the missing reasoning. **Do not reimplement** the metrics computation — read it.

---

## 5. Other Fabric Toolbox monitoring/governance accelerators

**TITLE:** `microsoft/fabric-toolbox` (Fabric CAT) — monitoring suite
**URLs:**
- https://github.com/microsoft/fabric-toolbox
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/workspace-monitoring-dashboards
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/workspace-monitoring-dashboards/documentation/Workspace_Monitoring_RTI_Dashboard.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/workspace-monitoring-dashboards/documentation/Workspace_Monitoring_PBI_Report.md
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
- https://deepwiki.com/microsoft/fabric-toolbox/3-monitoring-solutions

- **Workspace Monitoring Dashboards** — pre-built **RTI (KQL) dashboard + Power BI report** on top of Fabric **Workspace Monitoring** (Eventhouse logs: query/refresh/engine telemetry at workspace scope). Workspace-scoped, not tenant-wide; complements FUAM. *Workspace Monitoring itself is covered in prior files — listed here only as the toolbox accelerator wrapping it.* **Integrate:** optional drill-down source for the agent's per-workspace deep dives. **Reimplement: no.**
- **Fabric Platform Monitoring** — accelerator to stand up an in-Fabric monitoring solution. Overlaps FUAM; **FUAM is the more complete choice** for the agent's needs.
- **Fabric Cost Analysis (FCA)** — see §2.
- **Semantic Link Labs** (`microsoft/semantic-link-labs`, https://github.com/microsoft/semantic-link-labs) — the Python library FUAM (and the agent's collectors) use to call Fabric/PBI APIs and run Metrics-App DAX from notebooks. **Integrate as a dependency**, not a competitor — it is the plumbing for read-only collection.

---

## 6. Community / open-source capacity-planning, monitoring & FinOps prior art

**TITLES & URLs:**
- **GT-Analytics / `fuam-basic`** — community precursor/variant of FUAM. https://github.com/GT-Analytics/fuam-basic
- **`microsoft/Fabric-metadata-scanning`** — sample app for the Scanner API (the owner/inventory source). https://github.com/microsoft/Fabric-metadata-scanning
- **`klinejordan/fabric-tenant-admin-notebooks`** — community admin notebooks incl. a Scanner API notebook. https://github.com/klinejordan/fabric-tenant-admin-notebooks
- **Semantic Link Labs Scanner walkthrough (fabric.guru)** — https://fabric.guru/scan-fabric-workspaces-with-scanner-api-using-semantic-link-labs
- **"Monitoring for Power BI Admins" (Evaluation Context)** — landscape of admin-monitoring options. https://evaluationcontext.github.io/posts/AdminMonitoring/
- **Telefónica Tech FUAM series** — https://telefonicatech.uk/blog/fabric-unified-admin-monitoring-part2/
- **David Alzamendi — deploy FUAM** — https://davidalzamendi.com/fabric-unified-admin-monitoring/
- **The Blue Owls — managing capacity with the Metrics app** — https://theblueowls.com/blog/monitoring-and-managing-fabric-capacity-with-the-metrics-app/
- **Rihab Feki — Fabric capacities (Medium)** — https://rihab-feki.medium.com/fabric-capacities-everything-you-need-to-know-2d1f9c46c7ed
- **edudatasci — "Before the Capacity Fire Starts: Why FUAM…"** — argues FUAM as a *baseline*, leaving room for an advisory layer. https://edudatasci.net/2026/04/23/before-the-capacity-fire-starts-why-fuam-belongs-in-every-fss-fabric-baseline/
- **edudatasci — FUAM + Purview lineage for governance** — https://edudatasci.net/2026/03/12/from-telemetry-to-trust-using-fuam-purview-lineage-to-make-fabric-governance-pay-off/

**Pattern across community work:** everyone **collects and visualizes**; the consistent gap is **automated interpretation + action** (verdicts, alerts, owner-targeted recommendations). `fuam-basic` and the admin notebooks are collection helpers the agent can reference but supersedes by reading FUAM gold. **Azure FinOps/cost** pattern for Fabric = the **FOCUS** standard via FCA / Azure Cost Management (§2) — the agent should speak FOCUS/FinOps language when it recommends sizing changes.

**Integrate vs reimplement (community):** reference for collection patterns only; **reimplement none** — they validate the agent's "read FUAM, add brains" strategy.

---

## 7. Differentiation — what the agent does that the prior art does NOT

| Capability | Capacity Metrics app | Chargeback app | FUAM | FCA | **bi-fabrics-audit-agent** |
|---|---|---|---|---|---|
| Per-item historical CU | ✔ | ✔ (by user) | ✔ (`*_by_item_by_operation_by_day`) | partial | **reads** (FUAM/Metrics) |
| Item→owner mapping | ✖ | ✖ (user-of-op only) | ✔ (Scanner `configuredBy`) | ✖ | **reads + joins** |
| Throttling visibility | ✔ (chart) | ✖ | ✔ (Compute page) | ✖ | **reads + interprets** |
| Refresh contention signal | partial | ✖ | ✔ (refreshables) | ✖ | **reads + interprets** |
| **Verdict: optimize vs size-up** | ✖ | ✖ | ✖ | ✖ | **✔ (Claude-reasoned)** |
| **30% User→Item→Owner concentration alert** | ✖ | ✖ (no rule) | ✖ | ✖ | **✔** |
| Proactive alerting | ✖ | ✖ | ✖ | ✖ | **✔** |
| $ cost framing | ✖ | ✖ | ✖ | ✔ (FOCUS) | **reads FCA** |
| Read-only / no footprint to deploy | report | report | deployed pipeline | deployed pipeline | **✔ read-only advisory** |

**Net:** the prior art is **collection + visualization**; the agent is the **reasoning + advisory + alerting layer** that sits *on top of* it. Specifically, the agent's three unreplicated moves are:
1. **Claude-reasoned verdict** — throttling/oversized-model/refresh-contention detection rolled into a single **optimize-vs-size-up** recommendation (with $ context from FCA), which no first-party or community tool computes.
2. **30% User→Item→Owner concentration alert** — fuses the *user-of-operation* leg (Chargeback/`activities`) with the *item-owner* leg (FUAM inventory `configuredBy`) into a blast-radius/ownership signal and fires when one chain owns ≥30% of CU. No prior art chains all three.
3. **Read-only advisory posture** — adds zero deployed footprint and consumes no monitored-capacity CU of its own (FUAM/FCA both deploy pipelines that consume CU); it reads existing gold Lakehouses + Metrics App over OneLake/DAX with least-privilege read scopes.

**Integration verdict for the build:** **READ FUAM (and optionally FCA) over OneLake; do not re-collect.** Keep a live-collector fallback (file 14 DAX/`sempy` path + Scanner API) for tenants without FUAM. Honor the **user-masking** tenant setting in any per-user output.

---

## Flat URL list

- https://github.com/microsoft/fabric-toolbox
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/README.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Architecture.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_deploy_FUAM.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_update_FUAM.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Core_Report.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Engine_Level_Analyzer_Reports.md
- https://deepwiki.com/microsoft/fabric-toolbox/3.1-fabric-unified-admin-monitoring-(fuam)
- https://deepwiki.com/microsoft/fabric-toolbox/3.1.3-fuam-usage-and-capabilities
- https://deepwiki.com/microsoft/fabric-toolbox/3-monitoring-solutions
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-cost-analysis/README.md
- https://deepwiki.com/microsoft/fabric-toolbox/3.3-fabric-cost-analysis-(fca)
- https://www.jamesserra.com/archive/2025/12/fabric-cost-analysis-fca/
- https://blog.robsewell.com/blog/fca-fabric-cost-analysis-for-finops/
- https://sqlyard.com/2026/01/12/fabric-cost-analysis-explained-bringing-clarity-to-microsoft-fabric-costs/
- https://learn.microsoft.com/en-us/fabric/enterprise/chargeback-app
- https://github.com/MicrosoftDocs/fabric-docs/blob/main/docs/enterprise/chargeback-app.md
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-health-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-install
- https://learn.microsoft.com/en-us/fabric/enterprise/throttling
- https://marketplace.microsoft.com/en-us/product/power-bi/pbi_pcmm.microsoftpremiumfabricpreviewreport
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/workspace-monitoring-dashboards
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/workspace-monitoring-dashboards/documentation/Workspace_Monitoring_RTI_Dashboard.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/workspace-monitoring-dashboards/documentation/Workspace_Monitoring_PBI_Report.md
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
- https://github.com/microsoft/semantic-link-labs
- https://github.com/GT-Analytics/fuam-basic
- https://github.com/microsoft/Fabric-metadata-scanning
- https://github.com/microsoft/Fabric-metadata-scanning/blob/main/README.md
- https://github.com/klinejordan/fabric-tenant-admin-notebooks/blob/main/Fabric%20Scanner%20API.ipynb
- https://fabric.guru/scan-fabric-workspaces-with-scanner-api-using-semantic-link-labs
- https://learn.microsoft.com/en-us/fabric/governance/metadata-scanning-run
- https://learn.microsoft.com/en-us/fabric/governance/metadata-scanning-overview
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/items/get-item
- https://evaluationcontext.github.io/posts/AdminMonitoring/
- https://telefonicatech.uk/blog/fabric-unified-admin-monitoring-part2/
- https://davidalzamendi.com/fabric-unified-admin-monitoring/
- https://theblueowls.com/blog/monitoring-and-managing-fabric-capacity-with-the-metrics-app/
- https://rihab-feki.medium.com/fabric-capacities-everything-you-need-to-know-2d1f9c46c7ed
- https://edudatasci.net/2026/04/23/before-the-capacity-fire-starts-why-fuam-belongs-in-every-fss-fabric-baseline/
- https://edudatasci.net/2026/03/12/from-telemetry-to-trust-using-fuam-purview-lineage-to-make-fabric-governance-pay-off/
- https://blog.robsewell.com/blog/fuam-fabric-unified-admin-monitoring/

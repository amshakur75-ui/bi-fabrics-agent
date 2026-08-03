# Per-User CU% / Capacity-Consumption Attribution in Microsoft Fabric / Power BI

**Research item 14 — the crux question for `bi-fabrics-audit-agent`.**

> Agent context: READ-ONLY Microsoft Fabric / Power BI capacity audit agent. Authenticates as an **Entra service principal (SP)**, runs unattended in **Databricks**. Flagship feature = **30% concentration alert**: surface the single USER (then item, then owner) driving capacity consumption. So the operative constraint on every method below is: **must be queryable by an SP, unattended, read-only, and ideally expose true CU (Capacity Units) per user — not just a CPU/volume proxy.**

Date compiled: 2026-06-23. Sources: learn.microsoft.com (primary), Microsoft Fabric blog, microsoft/fabric-toolbox GitHub, and reputable Fabric community blogs. Full URL list at the bottom.

---

## TL;DR — the answer

- **There is no single SP-queryable surface that gives true per-user CU(s).** The only place true CU(s) is attributed down to the user is the **Fabric Capacity Metrics app's Timepoint Item Detail page** — and its semantic model is **officially unsupported for any use outside the app**, and **service principals are not supported** against it. So it is great for a human to confirm a finding, but a poor automation primitive.
- The only **true-CU, SP-friendly, automatable** surface is **capacity-/workspace-/item-level** (Capacity Metrics model read via SP-capable DAX is unsupported; the SP-clean route to capacity-level true CU is **Real-Time Hub Capacity Overview Events → Eventhouse**, but that has **no user dimension**).
- The only **per-user, SP-friendly, automatable** surfaces give a **proxy**, not CU(s):
  - **Workspace Monitoring `SemanticModelLogs`** → `CpuTimeMs` by `ExecutingUser` (engine CPU ms — best proxy, queryable by SP via KQL/SQL on the Eventhouse).
  - **Azure Log Analytics** for Power BI → same AS-engine `CpuTimeMs`/`ExecutingUser` (proxy; queryable by SP via Azure Monitor).
  - **Activity Events / audit log** → operation **counts** per `UserId` (volume proxy only; no CPU and no CU; but SP-supported and tenant-wide).
- **Recommended architecture (combination):** capacity-level **true CU%** from one source + per-user **ranking** from a CPU-proxy source, with an explicit "proxy, not billed CU" caveat in the alert. Details in the Ranked Recommendation.

---

## Important conceptual distinction: CU(s) vs CPU vs operation count

Three different units appear across these surfaces; conflating them is the #1 accuracy trap for this agent:

1. **CU(s) — Capacity Units seconds.** The *billed* unit Fabric throttles on. Computed by Microsoft's internal smoothing/normalization across workload types. **Only the Capacity Metrics app (and the Capacity Overview Events) expose true CU(s).** The 30% concentration threshold is most defensible when expressed in CU(s).
2. **CPU time (`CpuTimeMs`) — Analysis Services engine CPU milliseconds.** A *proxy*. Correlates with CU for query/refresh-heavy semantic-model work but is **not** the billed unit, has no cross-workload normalization, and only covers the AS engine (semantic models) — not Spark, Warehouse, Dataflows, Pipelines, etc. Available per `ExecutingUser`.
3. **Operation count.** Pure *volume* proxy from Activity Events. No time, no CPU, no CU. Cheapest to get and the only fully tenant-wide SP surface, but the weakest signal for "who is burning capacity."

---

## Per-method matrix

### Method 1 — Fabric Capacity Metrics app: Timepoint Item Detail page (true CU per user) ⭐ best data, worst automatability

- **Data source / table:** The app's semantic model, surfaced on the **Timepoint Item Detail page**. The "Interactive and background records for time range" table fetches the **top 100,000 records by CUs** within a 30-second timepoint window.
- **True CU vs proxy:** **TRUE CU(s).** Default, non-removable columns include **`User`** ("The name of the user that triggered the interactive or background operation"), **`Total CUs`** ("number of CUs used by interactive or background operation"), **`Timepoint CUs`**, **`% Of Base capacity`**, `Duration (s)`, `Throttling (s)`, `Status`, `Operations`. Optional columns: `Operation ID`, `Billing type` (Billable/Nonbillable), smoothing start/end.
- **Per-USER granularity:** **YES** — explicit `User` column + a **User** slicer + Operation ID/CU-threshold filters. This is the single richest per-user CU surface Microsoft ships.
- **SP-accessible & automatable:** **NO (the killer).** Two hard blocks:
  1. *Officially unsupported for any external use:* "The semantic model used by the Microsoft Fabric Capacity Metrics application is only supported for use by the reports provided in the application. Any consumption from, usage of, or modification of the semantic model isn't supported." (metrics-app limitations).
  2. *SP not supported:* community + Q&A consistently report "Service principals aren't supported for the Fabric Capacity Metrics semantic model yet, so it must use a user account with Fabric access." You can technically connect with an **interactive capacity-admin OAuth2** account via DAX/XMLA/SemPy (see Method 6), but not cleanly with an SP.
- **Latency:** ~**10–15 min** after activity (general app latency). Dimensions (capacities/workspaces/items) refresh on a **scheduled midnight refresh**.
- **Retention:** **14 days** of compute detail (Compute page is a 14-day view; matrix CU(s) is cumulative over 14 days). Storage page = 30 days.
- **Setup/permission cost:** Must be **capacity admin** to install/view. App auto-creates a workspace. To "Show user data," the tenant **"Show user data in the Fabric Capacity Metrics app and reports"** setting must be on (else emails are hidden — directly affects the agent's ability to name a user).
- **Accuracy caveats:** **Sampling** above 100,000 records per timepoint (conditional formatting flags it; narrow via Bucket Start/End, User/Operation slicers, or CU threshold). 0.01–0.05% CU% drift between heartbeat visual and operation tables. CSV/Export: only via Power BI visual "Export data" (max ~30k–150k rows, and "Sampling might occur while exporting") — not an automation API.
- **Verdict:** The **ground-truth reference** for a human/analyst to confirm a concentration finding, but **not a primary automation source** for an SP agent.

### Method 2 — Workspace Monitoring `SemanticModelLogs` (CPU proxy per user) ⭐ best SP-friendly per-user signal

- **Data source / table:** **`SemanticModelLogs`** table in the **Workspace Monitoring Eventhouse / KQL database** (auto-provisioned per workspace; queryable via **KQL or SQL**).
- **True CU vs proxy:** **PROXY.** Key columns: **`CpuTimeMs`** ("Amount of CPU time (in milliseconds) used by the event"), **`DurationMs`**, **`ExecutingUser`** ("The user running the operation"), `OperationName`, `OperationDetailName`, `EventText` (e.g., the DAX query), `CapacityId`, `WorkspaceId`, `ItemId`/`ItemName`/`ItemKind`, `Status`, `Timestamp`, plus an `ExecutionMetrics` event for richer per-request metrics. **No CU(s) field.** `CpuTimeMs` is engine CPU, not billed CU.
- **Per-USER granularity:** **YES** — `ExecutingUser` per operation; rank users by `sum(CpuTimeMs)`.
- **SP-accessible & automatable:** **YES (best of the per-user options).** The monitoring Eventhouse is a **read-only KQL DB**; access requires **workspace Contributor+** role — grant the SP that role. Queryable unattended via Kusto/KQL or the SQL endpoint. **Not throttled** by capacity state (monitoring Eventhouse queries keep working even when the capacity is overloaded).
- **Latency:** Near-real-time streaming (seconds–low minutes).
- **Retention:** **30 days** (fixed; not configurable).
- **Setup/permission cost:** Enable **per workspace** (workspace setting "Log workspace activity"). **Cannot coexist with Log Analytics** in the same workspace. Consumes capacity CU (Eventhouse/Eventstream billing). Scaling to many workspaces = enable on each + add SP as contributor on each.
- **Accuracy caveats:** **Semantic models only** (AS engine) — does not capture Spark/Warehouse/Pipeline/Dataflow CPU. `CpuTimeMs` ≠ billed CU and has no cross-workload normalization. "User data operation logs aren't available even though the table is present." Per-workspace enablement = blind spots on workspaces you didn't enable.
- **Verdict:** **Primary per-user ranking source** for the agent (semantic-model-heavy capacities especially).

### Method 3 — Azure Log Analytics for Power BI (CPU proxy per user)

- **Data source / table:** **Azure Monitor / Log Analytics** workspace, table **`PowerBIDatasetsWorkspace`**. Same AS-engine diagnostic events as Method 2 (Command/Query/Discover + the **`ExecutionMetrics`** event), exposing `CpuTimeMs`, `DurationMs`, `ExecutingUser`, `XmlaRequestId`, etc.
- **True CU vs proxy:** **PROXY** — identical AS-engine CPU semantics to Method 2.
- **Per-USER granularity:** **YES** (`ExecutingUser`).
- **SP-accessible & automatable:** **YES** — Log Analytics is in Azure; an SP with **Log Analytics Reader** on the LA workspace can query via the **Azure Monitor Query API / Logs API** (KQL) unattended. (Note: this SP is an *Azure RBAC* principal on the LA workspace, slightly different plumbing from a Fabric workspace role.)
- **Latency:** Data available in LA in **~5 minutes**.
- **Retention:** **Configurable** (Azure Monitor retention — can keep far longer than 30 days, the key advantage over Method 2; subject to LA ingestion/retention cost).
- **Setup/permission cost:** **Premium/Fabric capacity workspaces only**; configured **per workspace**; requires an **Azure subscription + Log Analytics workspace** and admin to wire it up. **Mutually exclusive with Workspace Monitoring** per workspace.
- **Accuracy caveats:** Same as Method 2 (semantic models only; CPU≠CU). Only V2 Premium workspaces; activities captured only for models **physically hosted** in the logged workspace; CSV-uploaded models don't log; paginated reports unsupported.
- **Verdict:** **Equivalent per-user proxy to Method 2**, preferred when you need **>30-day retention** or already run on Azure Monitor. Otherwise Method 2 is more Fabric-native.

### Method 4 — Activity Events / Audit log (operation COUNT per user — volume proxy)

- **Data source / endpoint:** **`GET /admin/activityevents`** (Power BI REST), or `Get-PowerBIActivityEvent` cmdlet, or Purview unified audit log. JSON activity records.
- **True CU vs proxy:** **NEITHER CU nor CPU — operation COUNT only.** Records who did what on which item (`UserId`, `Activity`, `ItemName`, timestamps). **No CU(s), no `CpuTimeMs`, no duration-of-compute.** Strictly a volume proxy.
- **Per-USER granularity:** **YES** — per `UserId`, filterable by user/activity type.
- **SP-accessible & automatable:** **YES, tenant-wide.** SP supported if **"Allow service principals to use Power BI APIs"** (Developer settings) is on; **important constraint:** the SP's app registration must have **no admin-consent-required Power BI permissions** set in Azure, or the call fails. Best fully-tenant-wide SP surface here.
- **Latency:** Events appear within ~**30 min** (up to ~60 min lag).
- **Retention:** **~30 days** via the API (Purview audit log retains longer, 90+ days / per license).
- **Setup/permission cost:** Low. Caller must be Fabric admin **or** an approved SP. Limits: **200 requests/hour**; **1 day per request**; ~5,000–10,000 entries/page + continuation token.
- **Accuracy caveats:** Volume ≠ consumption — a user running one giant Spark job looks "smaller" than someone clicking 100 reports. Use only as a **tie-breaker / corroboration / owner-identification** signal, never as the CU number.
- **Verdict:** **Supporting signal** (tenant-wide reach, identifies item owners and viewers), **not** a CU source.

### Method 5 — Real-Time Hub: Fabric Capacity Overview Events (true CU%, capacity-level, NO user) ⭐ best SP-friendly true-CU source

- **Data source / table:** **Capacity Overview Events** in **Real-Time Hub** — **Capacity Summary** (smoothed CU + throttling %, emitted every **30 s**) and **Capacity State** (paused/overloaded, event-driven). Can be streamed into an **Eventhouse** for historical/granular query.
- **True CU vs proxy:** **TRUE CU%** (smoothed, the same way throttling is evaluated) — capacity-level.
- **Per-USER granularity:** **NO. Confirmed capacity-level only.** No user, workspace, or item dimension. "The summary table contains aggregated CU data at the **capacity level** in a granularity of 30-second windows."
- **SP-accessible & automatable:** **YES** — once routed to an Eventhouse/KQL DB, an SP with the workspace/DB role queries it via KQL/SQL unattended. (Best-effort delivery: rare drops/dupes possible.)
- **Latency:** ~30 s (near real-time).
- **Retention:** Whatever the destination Eventhouse retains (you control it).
- **Setup/permission cost:** Set up the event stream → Eventhouse; preview feature (2025–2026).
- **Accuracy caveats:** No per-user/item breakdown by design. Best-effort delivery. Smoothed (not raw).
- **Verdict:** **Best SP-clean source for capacity-level true CU% and the throttling/overload state** that gates the 30% alert. Pair with a per-user proxy.

### Method 6 — executeQueries (DAX REST) / XMLA against the Capacity Metrics model

- **Data source / endpoint:** `POST /datasets/{id}/executeQueries` (DAX), or **XMLA endpoint**, or **Semantic Link `evaluate_dax()`** (SemPy in a Fabric notebook), pointed at the **Capacity Metrics semantic model**.
- **True CU vs proxy:** Would be **TRUE CU(s)** *if it worked* — it's reading the same model as Method 1.
- **Per-USER granularity:** **YES** (same model).
- **SP-accessible & automatable:** **Effectively NO for this model.** Mechanics: executeQueries requires tenant setting **"Dataset Execute Queries REST API"** on, scope `Dataset.Read.All`/`ReadWrite.All`, dataset **read+build** perms, dataset on a capacity; **SP allowed in general** if "Allow service principals to use Power BI APIs" is on (and not RLS/SSO). XMLA requires the capacity **XMLA endpoint = Read** (and moving the metrics workspace to a dedicated capacity). **BUT** the Capacity Metrics model is **explicitly "not supported for any consumption/usage/modification" outside the app**, and **SPs are reported as not supported** against it. Community guidance gets it working only with an **interactive capacity-admin (OAuth2)** account (DAX Studio / Tabular Editor / SemPy), not an SP. Also note: `/executeQueries` supports **DAX only — not INFO/DMV/MDX**.
- **Latency / retention:** Inherits the model (10–15 min latency, 14-day window).
- **Setup/permission cost:** High and brittle (XMLA Read, build perms, tenant settings) — and still unsupported/fragile.
- **Accuracy caveats:** Unsupported surface ⇒ schema can change without notice; risk of breakage; not a defensible production dependency for an unattended SP agent.
- **Verdict:** **Do not build the agent on this.** Acceptable only as an *optional, interactive-user, best-effort* "deep confirm" path, clearly flagged as unsupported.

### Method 7 — Chargeback / showback, FUAM, billing/usage exports

- **Capacity Metrics "Chargeback":** GA feature in the app. **Allocates cost by WORKSPACE** (and SKU / workload type) — *"view usage by workspace and assign costs proportionally."* **No per-user attribution.** Capacity-/workspace-level true CU. SP access inherits Method 1's limits (unsupported model).
- **FUAM (Fabric Unified Admin Monitoring, microsoft/fabric-toolbox):** Open-source accelerator; ingests activity events + capacity/inventory data into a Fabric lakehouse + Power BI model you own. Surfaces **CU per item / capacity / workspace** and **unique-user counts** — but **no native per-user CU breakdown** in the shipped Core Report. *However,* because you own the lakehouse, the SP can query its Delta tables directly (good for item/owner attribution and tenant inventory). Per-user CU is **not** provided out of the box.
- **Azure billing / Cost Management export:** Capacity SKU cost only — **no user, no item granularity**. Irrelevant for concentration.
- **Verdict:** Chargeback/FUAM are excellent for **item/owner/workspace** attribution (the agent's secondary "then item, then owner" drill-down) and FUAM is **SP-queryable on its own lakehouse**, but **none give per-user CU**.

### Method 8 — Newer / adjacent (2025–2026)

- **Timepoint Item Detail "down to user ID where available"** (Method 1) is the main 2025–2026 advancement for per-user CU — still app-bound, SP-blocked.
- **Operations Agent / AI Functions consumption reporting (Preview, 2026):** new CU categories in the Metrics app/compute page (AI Functions tracked separately). Capacity/item-level, not per-user.
- **Data Warehouse billing & utilization reporting:** Warehouse-specific CU views (`queryinsights`), per-query/per-user for Warehouse only — narrow, not a general per-user CU surface, but useful if Warehouse dominates the capacity.
- **Purview audit:** same data as Method 4 with longer retention; still counts, not CU.
- **Verdict:** No new general-purpose SP-queryable per-user-CU surface has shipped as of mid-2026.

---

## Comparison table

| # | Method | Data source / table-endpoint | Unit (CU / CPU / count) | Per-user? | SP-accessible & automatable | Latency | Retention | Setup/permission cost | Key accuracy caveat |
|---|--------|------------------------------|-------------------------|-----------|------------------------------|---------|-----------|-----------------------|---------------------|
| 1 | Capacity Metrics – Timepoint Item Detail | App semantic model (`User`,`Total CUs`,`Timepoint CUs`) | **True CU(s)** | **Yes** | **No** (model unsupported externally; SP not supported) | 10–15 min | 14 days | Capacity admin; "show user data" setting | Sampling >100k/timepoint; UI/export only |
| 2 | Workspace Monitoring `SemanticModelLogs` | Eventhouse KQL DB; `CpuTimeMs`,`ExecutingUser` | CPU ms (proxy) | **Yes** | **Yes** (SP = workspace Contributor+; KQL/SQL) | sec–min | 30 days | Enable per workspace; bills CU | Semantic models only; CPU≠CU |
| 3 | Azure Log Analytics (Power BI) | `PowerBIDatasetsWorkspace` (`CpuTimeMs`,`ExecutingUser`,`ExecutionMetrics`) | CPU ms (proxy) | **Yes** | **Yes** (SP = LA Reader; Azure Monitor Query API) | ~5 min | Configurable (>30d) | Azure sub + LA workspace; per-workspace; Premium only | Semantic models only; CPU≠CU |
| 4 | Activity Events / audit log | `GET /admin/activityevents`; Purview audit | Operation **count** | **Yes** | **Yes**, tenant-wide (SP allowed) | ~30–60 min | ~30d API (90+ Purview) | Low; 200 req/hr, 1 day/req | Volume ≠ consumption |
| 5 | Real-Time Hub Capacity Overview Events | RTH Capacity Summary/State → Eventhouse | **True CU%** | **No** (capacity-level) | **Yes** (SP queries Eventhouse) | ~30 s | Eventhouse-controlled | Set up eventstream→Eventhouse (preview) | No user/item dim; best-effort delivery |
| 6 | executeQueries/XMLA vs Metrics model | `POST /datasets/{id}/executeQueries`; XMLA; SemPy | True CU(s) (if it worked) | **Yes** | **No** for this model (unsupported; SP not supported) | 10–15 min | 14 days | High/brittle (XMLA Read, build perms) | Unsupported; schema can break |
| 7 | Chargeback / FUAM / billing | Metrics app Chargeback; FUAM lakehouse; Azure Cost Mgmt | True CU (workspace/item) | **No** (workspace/item) | Chargeback: no. **FUAM: yes (own lakehouse)** | varies | varies (FUAM: you own) | FUAM deploy; chargeback = app | No per-user CU anywhere here |
| 8 | DW billing / AI Functions / Purview (2025–26) | `queryinsights`; Metrics app categories | CU (workload-scoped) / count | Partial (DW per-user) | Mixed | varies | varies | varies | Workload-specific / not general |

---

## RANKED RECOMMENDATION (for an SP-based, unattended, read-only Databricks agent)

### Ranking of individual methods, weighted for *SP-automatability first*, then *CU fidelity*, then *coverage*

1. **Workspace Monitoring `SemanticModelLogs`** (Method 2) — best **SP-friendly per-user** signal. True per-user `ExecutingUser` ranking via `CpuTimeMs`, fully unattended KQL/SQL, not throttled. (CPU proxy + semantic-model-only are its limits.)
2. **Real-Time Hub Capacity Overview Events** (Method 5) — best **SP-friendly true-CU%** + throttling/overload state to gate the alert. (No user dim.)
3. **Azure Log Analytics** (Method 3) — equal to #2 on per-user proxy; pick over Method 2 when you need **long retention** or are already on Azure Monitor.
4. **Activity Events** (Method 4) — tenant-wide SP corroboration + **owner/item** identification; counts only.
5. **FUAM** (Method 7) — SP-queryable on your own lakehouse for **item/workspace/owner** attribution + inventory.
6. **Capacity Metrics Timepoint Item Detail** (Method 1) — richest **true-CU per user**, but **human-in-the-loop only** (SP-blocked, unsupported model). Use as the analyst "confirm" reference, not an automation input.
7. **executeQueries/XMLA vs Metrics model** (Method 6) — avoid for production; unsupported + SP-blocked.

### Best SINGLE method
**Workspace Monitoring `SemanticModelLogs`** — it is the only surface that is simultaneously (a) **SP-queryable & unattended**, (b) **per-user**, and (c) Fabric-native with no Azure-side plumbing. Caveat the output as a **CPU proxy** and scope it to semantic-model workloads.

### Best COMBINATION (recommended architecture)
A two-layer design that separates the *true-CU capacity verdict* from the *per-user ranking*:

- **Layer A — Capacity verdict (true CU%):** **Real-Time Hub Capacity Overview Events → Eventhouse** (Method 5). SP reads smoothed capacity CU% + throttling/overload state via KQL. This is the authoritative number for "is the capacity hot, and by how much."
- **Layer B — Per-user concentration (proxy ranking):** **Workspace Monitoring `SemanticModelLogs`** (Method 2; or **Azure Log Analytics**, Method 3, if long retention is required). SP ranks `ExecutingUser` by `sum(CpuTimeMs)` to find the dominant user. Compute the user's **share of total `CpuTimeMs`** in the window and apply the **30% threshold to the share**, not to a raw CU number.
- **Layer C — Corroboration & owner drill-down:** **Activity Events** (Method 4, tenant-wide SP) + **FUAM**/inventory (Method 7) to confirm the user, identify the **item**, and resolve the **owner** — the agent's "then item, then owner" path.
- **Layer D — Human confirm (optional, no SP):** point an analyst at the **Capacity Metrics Timepoint Item Detail page** (Method 1) for the timepoint the agent flagged, to read **true `Total CUs` by `User`**.

**Why this split:** true per-user CU(s) is only in an SP-blocked surface, so the agent must *derive* concentration from a proxy. Expressing the alert as **"User X accounts for ~N% of semantic-model CPU during the window in which the capacity hit M% CU"** is honest, automatable, and SP-clean. Always label Layer-B numbers as a **proxy, not billed CU**, and recommend the Layer-D confirmation for any action with cost consequences.

**Critical configuration prerequisites the agent depends on:**
- Tenant setting **"Show user data in the Fabric Capacity Metrics app and reports"** = ON (else user identities are masked everywhere downstream).
- SP added as **workspace Contributor+** on each monitored workspace (Method 2) **or** **Log Analytics Reader** on the LA workspace (Method 3).
- Tenant setting **"Allow service principals to use Power BI APIs"** = ON, and the SP app registration carries **no admin-consent-required Power BI permissions** (Method 4).
- Decide Method 2 **vs** Method 3 per workspace — **they are mutually exclusive** on the same workspace.

---

## Flat URL list (sources)

- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-summary-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-item-detail-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-calculations
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-install
- https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations
- https://learn.microsoft.com/en-us/fabric/get-started/workspace-monitoring-overview
- https://learn.microsoft.com/en-us/fabric/fundamentals/workspace-monitoring-overview
- https://learn.microsoft.com/en-us/fabric/real-time-intelligence/real-time-intelligence-consumption
- https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview
- https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure
- https://powerbi.microsoft.com/en-us/blog/new-executionmetrics-event-in-azure-log-analytics-for-power-bi-semantic-models/
- https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-admin-auditing
- https://learn.microsoft.com/en-us/rest/api/power-bi/admin/get-activity-events
- https://learn.microsoft.com/en-us/fabric/admin/operation-list
- https://learn.microsoft.com/en-us/power-bi/guidance/admin-activity-log
- https://learn.microsoft.com/en-us/fabric/real-time-hub/explore-fabric-capacity-overview-events
- https://learn.microsoft.com/en-us/fabric/real-time-hub/fabric-events-capacity-consumption.md
- https://blog.fabric.microsoft.com/en-US/blog/fabric-capacity-events-in-real-time-hub-preview/
- https://learn.microsoft.com/en-us/fabric/real-time-hub/tutorial-monitor-capacity-threshold
- https://learn.microsoft.com/en-us/fabric/real-time-hub/set-alerts-fabric-capacity-overview-events
- https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries
- https://learn.microsoft.com/en-us/power-bi/developer/execute-dax-queries-arrow/overview
- https://learn.microsoft.com/en-us/power-bi/developer/execute-dax-queries-arrow/best-practices
- https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-connect-tools
- https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/troubleshoot-xml-analysis-endpoint
- https://bits2bi.com/2025/03/15/tools-and-tricks-to-connect-and-extract-data-from-the-fabric-capacity-metrics-app/
- https://bits2bi.com/2025/03/15/extracting-semantic-model-size-from-the-fabric-capacity-metrics-app/
- https://community.fabric.microsoft.com/t5/Service/Microsoft-Fabric-Capacity-Metrics-Semantic-Model-Error/m-p/4871410
- https://learn.microsoft.com/en-us/answers/questions/5776805/read-fabric-capacity-metric-semantic-model-using-d
- https://community.fabric.microsoft.com/t5/Fabric-Updates-Blogs/Providing-more-insights-amp-tools-Capacity-health-timepoint/ba-p/5176753
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/README.md
- https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Core_Report.md
- https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/workspace-monitoring-dashboards
- https://github.com/microsoft/fabric-samples/tree/main/workspace-monitoring
- https://blog.fabric.microsoft.com/en-US/blog/understanding-operations-agent-capacity-consumption-usage-reporting-and-billing/
- https://learn.microsoft.com/en-us/fabric/data-warehouse/usage-reporting
- https://learn.microsoft.com/en-us/fabric/enterprise/capacity-planning-troubleshoot-consumption
- https://fabric.guru/analyzing-semantic-model-logs-using-fabric-workspace-monitoring
- https://daxnoob.blog/2025/06/18/identifying-semantic-model-capacity-spikes-using-workspace-monitoring/

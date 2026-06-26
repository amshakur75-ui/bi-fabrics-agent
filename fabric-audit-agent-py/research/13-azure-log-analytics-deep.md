# 13 — Azure Log Analytics / Azure Monitor Logs (Deep) for Power BI & Fabric

> Research focus: the **Azure Log Analytics mechanism itself** as a per-user / per-item
> telemetry source for a READ-ONLY Fabric/Power BI capacity-audit agent. How to wire a
> Power BI workspace to a Log Analytics (LA) workspace, the exact table + schema Power BI
> emits, the event categories, KQL patterns to attribute CPU to WHO and to WHICH item,
> retention/cost knobs, diagnostic-settings landscape, the Log-Analytics-vs-Fabric-Workspace-
> Monitoring decision, and how to query LA from Python.
>
> Companion files (out of scope here, referenced for contrast): Fabric Workspace Monitoring +
> `SemanticModelLogs` Eventhouse; the separate per-user-CU-method comparison agent.
>
> Date captured: 2026-06-23. All sources are `learn.microsoft.com` unless noted.

---

## TL;DR for the agent

- Power BI/Fabric streams **Analysis Services engine trace events** into **one** Azure Log
  Analytics table: **`PowerBIDatasetsWorkspace`** (NOT `PowerBIDatasetsTenant` — that table
  is intentionally left empty to avoid duplication). Resource type:
  `microsoft.powerbi/tenants/workspaces`. Latency ≈ **5 minutes**, sent continuously.
- That table is the agent's richest **WHO + CPU + per-item** source: every row carries
  `ExecutingUser`, `CpuTimeMs`, `DurationMs`, `ArtifactId`/`ArtifactName`/`ArtifactKind`,
  `PowerBIWorkspaceId`/`PowerBIWorkspaceName`, `OperationName` (e.g. `QueryEnd`,
  `CommandEnd`, `ProgressReportEnd`), and an `ExecutionMetrics` event with `totalCpuTimeMs`,
  `capacityThrottlingMs`, etc.
- Setup is **two-sided**: Azure side (register `microsoft.insights` RP, assign
  **Log Analytics Contributor**) + Power BI side (tenant setting *Azure Log Analytics
  connections for workspace administrators* → per-workspace **Settings ▸ Azure connections ▸
  Log Analytics**). **Premium / PPU / Fabric-capacity workspaces only** (no Pro, no v1).
- **Mutual exclusivity**: a workspace can have **either** Log Analytics **or** Fabric
  Workspace Monitoring — **not both**. Choose deliberately.
- Query from Python with **`azure-monitor-query`** `LogsQueryClient.query_workspace(...)`
  using `DefaultAzureCredential` — read-only, perfect for the audit agent.

---

## Item 1 — Overview: what Power BI sends to Log Analytics, and latency

- **TITLE:** Using Azure Log Analytics in Power BI (overview)
- **URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview
- **SUMMARY:** LA is the Azure Monitor service Power BI uses to save activity logs. The
  integration **exposes events from the Analysis Services (AS) engine**, derived from the
  same diagnostic logs available for Azure Analysis Services. Once connected, **data is sent
  continuously and is available in LA in ~5 minutes**.
- **KEY IDENTIFIERS / FACTS:**
  - For the LA feature, **Power BI only sends data to the `PowerBIDatasetsWorkspace` table
    and does NOT send data to `PowerBIDatasetsTenant`** ("avoids storing duplicate data").
  - You can fan many Power BI workspaces into one LA workspace; each entry is tagged with its
    Power BI Workspace ID (see FAQ).
- **CONSIDERATIONS / LIMITATIONS (verbatim points):**
  - **Only Premium workspaces are supported.** **Only Workspace v2** supports LA connections.
  - LA **doesn't support tenant migration**.
  - Activities are captured **only for semantic models physically hosted in the Premium
    workspace where logging is configured** — to capture a shared semantic model, configure
    logging on the workspace that *contains the model*, not the one that contains the report.
    (Important for the agent's per-item attribution: log on the model's home workspace.)
  - Semantic models created on the web by uploading a CSV **don't generate logs**.
  - **Paginated reports are NOT supported** via LA — use Azure audit logs instead.
  - Sovereign cloud support limited to US DoD and US Gov Community Cloud High.
  - **Blob Store / Event Hubs destinations are NOT supported** (workspace-level LA only).
- **HOW IT HELPS:** Tells the agent that LA = AS-engine telemetry only (query / refresh /
  command), scoped to model-hosting Premium workspaces, ~5 min fresh — ideal for near-real-time
  per-user/per-item CPU attribution, but it will NOT cover Pro workspaces, paginated reports,
  or CSV-uploaded models. The agent must reason about those gaps when computing coverage.

---

## Item 2 — Configure Azure Log Analytics in Power BI (admin + per-workspace setup)

- **TITLE:** Configure Azure Log Analytics in Power BI
- **URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure
- **SUMMARY:** The canonical how-to + the **full event/schema reference** (see Item 3). Setup
  has two halves: configure Azure, then enable in the Power BI Admin portal + per workspace.

### Prerequisites (Azure side) — exact requirements
1. **Create a Log Analytics workspace** in the Azure portal first.
2. **Contributor access to the Azure subscription.**
3. **Register the `microsoft.insights` resource provider** in the subscription where you
   collect Power BI log data (Azure portal ▸ Subscription ▸ *Settings ▸ Resource providers* ▸
   search `microsoft.insights` ▸ **Register**).
4. The user who sets up the integration must hold the **Log Analytics Contributor** role on
   the LA workspace. (Verify via *Access control (IAM) ▸ Role assignments*.)

### Tenant setting (Power BI Admin portal) — exact name/path
- **Power BI Admin portal ▸ Tenant Settings ▸ Audit and usage settings ▸ expand
  "Azure Log Analytics connections for workspace administrators"** → set slider **Enabled**
  and specify security groups under **Apply to**. This setting controls *which* workspace
  admins may connect a workspace to an LA workspace.

### Per-workspace configuration (Premium workspace owner)
- In the **Premium workspace ▸ Settings ▸ Azure connections ▸ expand Log Analytics** →
  select **Azure subscription, Resource group, Log Analytics workspace** → **Save**.
- **Disconnect:** Workspace Settings ▸ Log Analytics ▸ **Disconnect from Azure ▸ Save**.
  Disconnect is **non-destructive** — existing logs remain under LA retention policy.

### Error conditions worth surfacing to users (from the doc's table)
- "You need write permissions on this Log Analytics workspace to connect it to Power BI."
- "You don't have access to any Azure subscriptions… grant you contributor access or higher."
- "Ask your tenant admin to grant workspace admins permission to connect Log Analytics
  workspaces." (tenant setting disabled)

- **HOW IT HELPS:** Gives the agent a precise **readiness checklist** to detect/coach: is the
  RP registered, does the connecting principal have Log Analytics Contributor, is the tenant
  setting enabled for the right security group, is the workspace Premium/PPU/Fabric. The agent
  can map common failures to the exact remediation strings above.

---

## Item 3 — `PowerBIDatasetsWorkspace` table: EXACT schema (the agent's core source)

- **TITLE:** Azure Monitor Logs reference — PowerBIDatasetsWorkspace
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/powerbidatasetsworkspace
- **TABLE ATTRIBUTES:**
  - **Resource types:** `microsoft.powerbi/tenants/workspaces`
  - **Categories:** Azure Resources · **Solutions:** LogManagement
  - **Basic log:** **Yes** (the table can be put on the Basic table plan — see Item 8)
  - **Ingestion-time DCR support:** Yes · **Lake-only ingestion:** Yes

### Full column list (name · type · description) — reproduce exactly
| Column | Type | Description |
| --- | --- | --- |
| `ApplicationContext` | dynamic | Unique identifiers about the operation context — e.g. **Report ID, Dataset ID**. |
| `ApplicationName` | string | Client application name that created the connection (app-supplied, optional). |
| `ArtifactId` | string | **Unique ID of the resource (item) logging the data.** |
| `ArtifactKind` | string | **Type of artifact** logging the operation, e.g. Dataset / semantic model. |
| `ArtifactName` | string | **Name of the Power BI artifact** logging this operation. |
| `_BilledSize` | real | Record size in bytes (excluded from billed size calc). |
| `CorrelationId` | string | Event ID to correlate events across tables. |
| `CpuTimeMs` | long | **CPU time (ms) used by the operation.** |
| `CustomerTenantId` | string | Unique identifier of the Power BI tenant. |
| `DatasetMode` | string | Import / DirectQuery / Composite. |
| `DurationMs` | long | **Wall-clock time (ms) taken by the operation.** |
| `EventText` | string | Verbose info — **e.g. the DAX query text**; for `ExecutionMetrics` it's a JSON blob. |
| `ExecutingUser` | string | **The user executing the operation** (the WHO). |
| `Identity` | dynamic | Information about user and claims. |
| `_IsBillable` | string | Whether ingestion is billable. |
| `Level` | string | Severity: Success / Informational / Warning / Error. |
| `LogAnalyticsCategory` | string | Event category (Audit/Security/Request…). |
| `OperationDetailName` | string | Subcategory of `OperationName` (maps to AS EventSubclass). |
| `OperationName` | string | **The AS trace event** associated with the record (QueryEnd, CommandEnd, …). |
| `PowerBIWorkspaceId` | string | **Unique ID of the Power BI workspace** containing the artifact. |
| `PowerBIWorkspaceName` | string | **Name of the Power BI workspace.** |
| `PremiumCapacityId` | string | **Unique ID of the Premium capacity** hosting the artifact. |
| `ProgressCounter` | long | Progress counter (rows processed during refresh, via ProgressReportEnd). |
| `ReplicaId` | string | QSO replica id ('AAA' = read-write, 'AAB'+ = read-only replicas). |
| `_ResourceId` | string | Unique resource identifier. |
| `SourceSystem` | string | Agent type that collected the event (Azure). |
| `Status` | string | Status of the operation. |
| `StatusCode` | int | Status code (covers success + failure). |
| `_SubscriptionId` | string | Azure subscription id. |
| `TenantId` | string | The **Log Analytics workspace ID** (note: NOT the Power BI tenant). |
| `TimeGenerated` | datetime | UTC timestamp the log entry was generated. |
| `Type` | string | Table name. |
| `User` | string | The user the operation runs **on behalf of** (impersonation / effective identity). |
| `XmlaObjectPath` | string | Comma-separated parent path of the object. |
| `XmlaProperties` | string | Properties of the XMLA request. |
| `XmlaRequestId` | string | **Unique ID of the request** (join key across events). |
| `XmlaSessionId` | string | AS session identifier (SPID). |

> **Mapping note (legacy AS column → LA column)** from the configure page: `EventClass_s`→
> `OperationName`, `EventSubclass_s`→`OperationDetailName`, `CPUTime_s`→`CpuTimeMs`,
> `Duration_s`→`DurationMs`, `EffectiveUsername_s`→`ExecutingUser`, `User_s`→`User`,
> `DatabaseName_s`→`ArtifactName`, `TextData_s`→`EventText`, `RootActivityId_g`→`XmlaRequestId`,
> `SPID_s`→`XmlaSessionId`, `ApplicationContext_s`→`ApplicationContext`, `Severity_s`→`Level`.

- **HOW IT HELPS:** This is the literal data contract for the agent. Per-user CPU =
  `summarize sum(CpuTimeMs) by ExecutingUser`; per-item CPU = `… by ArtifactId, ArtifactName`;
  per-workspace = `… by PowerBIWorkspaceId`. `XmlaRequestId` is the join key to stitch a
  request's `QueryEnd`/`CommandEnd` to its `ExecutionMetrics` (true CPU + throttling).

---

## Item 4 — Event categories + `ExecutionMetrics` (the precise CPU/throttle numbers)

- **TITLE:** Configure Azure Log Analytics in Power BI ▸ "Events and schema"
- **URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure#events-and-schema
- **EVENT CATEGORIES logged** (the `LogAnalyticsCategory` values; underlying AS trace events):
  `AggregateTableRewriteQuery`, `Command`, `Deadlock`, `DirectQuery`, `Discover`, `Error`,
  **`ProgressReport`**, **`Query`**, `Session Initialize`, `VertiPaqSEQuery`, `Notification`.
  → For full event/subclass meaning see **Analysis Services Trace Events**:
  https://learn.microsoft.com/en-us/analysis-services/trace-events/analysis-services-trace-events
- **KEY `OperationName` values the agent will filter on:** `QueryEnd` (a DAX/MDX query
  finished — has `CpuTimeMs`/`DurationMs`), `CommandEnd` (a command finished — refreshes show
  here, `EventText` contains `<Refresh`), `DiscoverEnd`, **`ProgressReportEnd`** (per-step
  refresh progress; `ProgressCounter` = rows), and **`ExecutionMetrics`**.
- **`ExecutionMetrics` event:** For **every Discover, Command and Query request**, an
  `ExecutionMetrics` event is emitted at request end, correlated to the nearest
  `[Discover|Command|Query]End` by **`XmlaRequestId`**. `EventText` is JSON. Documented props
  (not all present every time):
  - `timeStart`, `timeEnd`, `durationMs`
  - **`totalCpuTimeMs`** (total CPU of the request), `vertipaqJobCpuTimeMs` (VertiPaq CPU),
    `mEngineCpuTimeMs` (Power Query/M engine CPU), `queryProcessingCpuTimeMs`
  - `approximatePeakMemConsumptionKB`, `mEnginePeakMemoryKB`
  - `executionDelayMs` (thread-pool wait), **`capacityThrottlingMs`** (delay due to capacity
    throttling — *the* signal for capacity pressure), `datasourceConnectionThrottleTimeMs`
  - DirectQuery: `directQueryConnectionTimeMs`, `directQueryIterationTimeMs`,
    `directQueryTotalTimeMs`, `directQueryRequestCount`, `directQueryTotalRows`
  - `refreshParallelism`, `vertipaqTotalRows` (refresh row count), `queryResultRows`,
    `errorCount`, `qsoReplicaVersion`
  - `intendedUsage`: 0=Default, 1=Scheduled/API refresh, 2=On-Demand refresh, 3=Dashboard
    tile/Query-cache refresh
  - `commandType`, `discoverType`, `queryDialect` (−1 Unknown, 0 MDX, 1 DMX, 2 SQL, 3 DAX, 4 JSON)
- **HOW IT HELPS:** `ExecutionMetrics.totalCpuTimeMs` is the **authoritative per-request CPU**
  (more accurate than the row's `CpuTimeMs`, which is per-event). `capacityThrottlingMs > 0`
  lets the agent attribute throttling to a **specific user + item + request**, and
  `intendedUsage` separates user-driven queries from scheduled refreshes for fair CPU billing.

---

## Item 5 — KQL patterns: per-user CPU, per-item CPU, slow queries, refresh, throttling

Source for all of these: the configure page (Item 2/3/4 URL) "Sample Log Analytics KQL
queries" + "ExecutionMetrics event" sections.

```kql
// (a) Per-WORKSPACE rollup: query count, distinct users, avg CPU, avg duration — 30d
PowerBIDatasetsWorkspace
| where TimeGenerated > ago(30d)
| where OperationName == "QueryEnd"
| summarize QueryCount=count(),
            Users=dcount(ExecutingUser),
            AvgCPU=avg(CpuTimeMs),
            AvgDuration=avg(DurationMs)
  by PowerBIWorkspaceId
```

```kql
// (b) Per-USER CPU attribution (the WHO) — adapt grouping to per-item by adding ArtifactId
PowerBIDatasetsWorkspace
| where TimeGenerated > ago(7d)
| where OperationName == "QueryEnd"
| summarize TotalCpuMs=sum(CpuTimeMs), Queries=count(), AvgDurMs=avg(DurationMs)
  by ExecutingUser, ArtifactName, PowerBIWorkspaceName
| order by TotalCpuMs desc
```

```kql
// (c) Slow queries (p50/p90 duration in 1h bins for a day)
PowerBIDatasetsWorkspace
| where TimeGenerated >= todatetime('2021-04-28') and TimeGenerated <= todatetime('2021-04-29')
| where OperationName == "QueryEnd"
| summarize percentiles(DurationMs, 0.5, 0.9) by bin(TimeGenerated, 1h)
```

```kql
// (d) Refresh durations by workspace + model (CommandEnd, service-driven refresh)
PowerBIDatasetsWorkspace
| where TimeGenerated > ago(30d)
| where OperationName == "CommandEnd"
| where ExecutingUser contains "Power BI Service"
| where EventText contains "refresh"
| project PowerBIWorkspaceName, DatasetName = ArtifactName, DurationMs
```

```kql
// (e) Refresh ExecutionMetrics for ONE model — true CPU + memory via XmlaRequestId join
let commands = PowerBIDatasetsWorkspace
    | where TimeGenerated > ago(1d)
    | where ArtifactId =~ "[Semantic Model Id]"
    | where OperationName in ("CommandEnd")
    | where EventText contains "<Refresh"
    | project TimeGenerated, ArtifactId, XmlaRequestId, CorrelationId, CommandText = EventText;
let executionMetrics = PowerBIDatasetsWorkspace
    | where OperationName == "ExecutionMetrics"
    | project TimeGenerated, XmlaRequestId, CorrelationId, EventText;
commands | join kind=leftouter executionMetrics on XmlaRequestId
```

```kql
// (f) THROTTLED requests by workspace, item, user (capacity pressure attribution)
let executionMetrics = PowerBIDatasetsWorkspace
    | where TimeGenerated > ago(1d)
    | where OperationName == "ExecutionMetrics"
    | extend eventTextJson = parse_json(EventText)
    | extend capacityThrottlingMs = toint(eventTextJson.capacityThrottlingMs)
    | where capacityThrottlingMs > 0;
let commands = PowerBIDatasetsWorkspace
    | where OperationName in ("CommandEnd", "QueryEnd", "DiscoverEnd")
    | project TimeGenerated, ExecutingUser, ArtifactId, PowerBIWorkspaceId,
              CommandOperationName = OperationName, XmlaRequestId, CorrelationId,
              CommandText = EventText;
commands
| join kind=inner executionMetrics on XmlaRequestId
| summarize countThrottling = count(), avgThrottlingDuration = avg(capacityThrottlingMs)
  by PowerBIWorkspaceId, ArtifactId, ExecutingUser, CommandOperationName
```

```kql
// (g) Daily ingestion sanity / volume baseline
PowerBIDatasetsWorkspace
| where TimeGenerated > ago(30d)
| summarize count() by format_datetime(TimeGenerated, 'yyyy-MM-dd')
```

- **HOW IT HELPS:** These are drop-in, read-only queries the agent can parameterize for its
  WHO/CPU-per-item verdicts. (b) = per-user chargeback; (e)/(f) = the most accurate CPU and the
  throttling root-cause join via `XmlaRequestId`. Microsoft also ships an open-source
  `.pbit` template + report at https://github.com/microsoft/PowerBI-LogAnalytics-Template-Reports.

---

## Item 6 — FAQ: roles workarounds, regions, multi-workspace, retention, cost, gotchas

- **TITLE:** Azure Log Analytics in Power BI — FAQ
- **URL:** https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-faq
- **KEY ANSWERS:**
  - **Scope of LA integration:** only **semantic-model activity logs (AS engine traces)** today.
  - **When to use:** engine logs are **high volume / large (~3–4 KB each** for complex models).
    Recommended for **performance investigations, scale/load testing, pre-release validation** —
    i.e. not necessarily always-on for every workspace. (Cost driver — see Items 7/8.)
  - **No Owner role? Workarounds:** (1) Azure admin grants you **Owner on the LA workspace just
    for initial config**, then downgrades you to Contributor; (2) add an Azure admin as a Power
    BI **workspace admin**, have them configure logging, then remove their access.
  - **Many Power BI workspaces → one LA workspace:** supported; **each entry is tagged with the
    Power BI Workspace ID** (`PowerBIWorkspaceId`) so you can differentiate. (LA is NOT
    strictly one-to-one; the agent can centralize many workspaces into one LA workspace.)
  - **Non-Premium?** No — Premium only.
  - **Latency:** typically **within 5 minutes**; sent continuously.
  - **Retention:** **default 31 days**, adjustable in Azure portal up to **730 days (2 years)**.
  - **Tenant admin disables workspace-level logging:** no new configs possible; existing
    connected workspaces keep sending.
  - **Choose which events to log?** No — you can't filter events.
  - **Move workspace out of Premium capacity:** config not deleted, but logs stop; resume when
    back on Premium.
  - **Workspace v1:** not configurable.
  - **Cost:** LA bills **storage, ingestion, and analytical queries independently**, varies by
    region; **an average Premium capacity generates ~35 GB of logs/month** (heavier for busy
    capacities). Use the Azure pricing calculator.
  - **Table not visible?** Expected — `PowerBIDatasetsWorkspace` is **created only once data is
    streamed** (e.g. after a semantic-model refresh/query).
- **HOW IT HELPS:** Answers the operational questions the agent's onboarding/coaching will hit:
  the Owner-role workaround, the ~35 GB/month cost baseline for budgeting, the 31-day default
  retention to extend for trend analysis, and the "table appears only after first activity"
  gotcha (so "no table" ≠ "misconfigured").

---

## Item 7 — Azure Monitor Logs cost model (per-GB, commitment tiers, retention, restore)

- **TITLE:** Azure Monitor Logs cost calculations and options
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/logs/cost-logs
- **PRICING MODEL:** Default = **pay-as-you-go**, billed on **ingested data volume (GB, 10^9
  bytes)** + **data retention**. Priced **regionally**. Each LA workspace bills separately.
- **BILLED-SIZE NUANCE:** Billed size ≈ **~25% less** than incoming JSON event size on average
  (up to 50% smaller for small events). **Standard columns excluded from billed size:**
  `_ResourceId`, `_SubscriptionId`, `_ItemId`, `_IsBillable`, `_BilledSize`, `_TenantId`, `Type`.
- **COMMITMENT TIERS:** Save **up to ~30%** vs pay-as-you-go for Analytics Logs. **Start at
  100 GB/day**; overage billed at the same per-GB tier rate. **31-day commitment period**;
  can move up (resets period) but not down/PAYG until period ends (6-hour grace to lower after
  config). Concrete tier prices live on the Azure Monitor pricing page (linked from doc).
- **RETENTION COST:** Charged per GB for retention. **Long-term (archive) retention** = reduced
  per-GB charge, **plus a charge to retrieve via a search job**. Billed daily (UTC). Deleting a
  table or purging data does **not** reduce retention cost — lower the retention period instead.
- **SEARCH JOBS:** Asynchronous; billed by **GB scanned** per day accessed.
- **DATA RESTORE:** Billed by **GB restored × time kept active**; **minimums 2 TB and 12 hours**,
  pro-rated above that.
- **DATA EXPORT:** Billed by GB exported (JSON byte size) to Storage/Event Hubs. (Note: Power BI
  itself can't pick Blob/Event Hubs as a *destination* — see FAQ — but the LA workspace can
  export downstream.)
- **HOW IT HELPS:** With the ~35 GB/month/capacity baseline (Item 6), the agent can estimate LA
  ingestion cost, recommend a **commitment tier only for large estates**, and advise putting the
  high-volume `PowerBIDatasetsWorkspace` table on the **Basic plan** + short interactive
  retention + long-term retention for cheap compliance (Item 8).

---

## Item 8 — Table plans (Analytics vs Basic vs Auxiliary) — directly relevant to PBI logs

- **TITLE:** Select a table plan based on data usage in a Log Analytics workspace
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-table-plans
- **PLANS:**
  - **Analytics** (default): full KQL, **free interactive query**, extendable interactive
    retention; best for data you query often / alert on.
  - **Basic:** **significantly reduced ingestion charge**, but **you pay per GB scanned** to
    query; **fixed 30-day** query window; reduced KQL surface; good for high-volume verbose logs
    queried occasionally. **`PowerBIDatasetsWorkspace` declares `Basic log: Yes`** (Item 3) — so
    the agent can recommend Basic for cost control on chatty estates.
  - **Auxiliary:** cheapest ingestion; **queryable for the full retention period** but with the
    most limited query capability; only settable on **custom DCR tables at creation** (built-in
    Azure tables don't support Auxiliary), and can't be changed afterward.
- **SWITCHING:** All tables support Analytics; you can **switch Analytics↔Basic** (takes effect
  immediately; **once a week** limit). Changing Analytics→Basic treats data >30 days as
  long-term retention.
- **PERMISSIONS:** View plan = `Microsoft.OperationalInsights/workspaces/tables/read`
  (Log Analytics Reader); set plan = `…/workspaces/write` + `…/workspaces/tables/write`
  (Log Analytics Contributor). Settable via Portal, REST (`Tables - Update` API,
  `properties.plan = Analytics|Basic`), CLI
  (`az monitor log-analytics workspace table update --plan Basic`), or PowerShell
  (`Update-AzOperationalInsightsTable -Plan Basic`).
- **Supporting cost detail:** https://learn.microsoft.com/en-us/azure/azure-monitor/logs/cost-logs
  (Basic/Auxiliary query billed by GB scanned; long-term retention + search jobs same across plans).
- **HOW IT HELPS:** The agent can advise the cost/queryability tradeoff: keep
  `PowerBIDatasetsWorkspace` on **Analytics** if it queries the data frequently for live audits,
  or move to **Basic** for big estates that only need periodic forensic queries — and it knows
  the exact roles/APIs to recommend (without doing the write itself, staying read-only).

---

## Item 9 — Retention configuration (interactive vs long-term/archive)

- **TITLE:** Manage data retention in a Log Analytics workspace
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-retention-configure
- **SUMMARY:** Set **interactive retention** (workspace default or per-table; Analytics tables
  up to **730 days / 2 years**) and **total retention** (interactive + long-term/archive) up to
  **~12 years (4,383 days)**. Power BI's LA connection defaults to **31 days** interactive
  (Item 6). Data beyond interactive retention rolls into **long-term retention** (cheaper, query
  via **search job** or **restore**).
- **HOW IT HELPS:** Lets the agent recommend, e.g., 31–90 days interactive for live analysis +
  cheap long-term retention for year-over-year capacity-trend audits and compliance, and explain
  that historical queries beyond interactive retention need a search job (cost from Item 7).

---

## Item 10 — `PowerBIDatasetsTenant` (why it's empty) + table-reference index

- **TITLE:** Azure Monitor Logs reference — table index / PowerBIDatasetsTenant
- **URLs:**
  - Tenant table ref: https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/powerbidatasetstenant
  - Category index: https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables-category
  - Azure Monitor ↔ Power BI integration: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/log-powerbi
- **SUMMARY:** Two Power BI dataset tables exist in the schema — **`PowerBIDatasetsWorkspace`**
  (workspace-level, the one that gets data) and **`PowerBIDatasetsTenant`** (tenant-level).
  For the workspace-level LA connection, **Power BI writes ONLY to
  `PowerBIDatasetsWorkspace`**; `PowerBIDatasetsTenant` stays empty to avoid duplication
  (Item 1). The `log-powerbi` Azure Monitor page covers the *reverse* direction too (exporting
  LA query results into Power BI via **Export ▸ Power BI (M query)** or **Power BI (new
  Dataset)**), and the required reader/contributor permissions for that export.
- **`log-powerbi` permissions to query/export LA:**
  - Export query to .txt (M query) → `Microsoft.OperationalInsights/workspaces/query/*/read`
    (**Log Analytics Reader**).
  - Create dataset directly → `Microsoft.OperationalInsights/workspaces/write`
    (**Log Analytics Contributor**).
- **HOW IT HELPS:** Confirms the agent should target **`PowerBIDatasetsWorkspace`** exclusively
  and never expect data in the tenant table; and that a **read-only** audit agent only needs
  **Log Analytics Reader** (`…/query/*/read`) to run KQL — important for least-privilege design.

---

## Item 11 — Diagnostic Settings: Microsoft.Fabric / Microsoft.PowerBIDedicated / PBI Embedded

- **TITLES / URLs:**
  - Supported categories (PBI Embedded/Dedicated):
    https://learn.microsoft.com/en-us/azure/azure-monitor/platform/resource-logs-categories
  - Power BI Embedded diagnostics overview:
    https://learn.microsoft.com/en-us/power-bi/developer/embedded/monitor-power-bi-embedded-reference
  - Diagnostic settings concept:
    https://learn.microsoft.com/en-us/azure/azure-monitor/platform/diagnostic-settings
- **SUMMARY / KEY POINTS:**
  - The **per-workspace Power BI ▸ Azure connections ▸ Log Analytics** flow (Items 1–3) is the
    *intended* path; it is **distinct from** classic Azure **Diagnostic Settings**.
  - **`Microsoft.PowerBIDedicated/capacities`** (A-SKU Power BI Embedded capacities, an Azure
    resource) supports classic **Diagnostic Settings → Log Analytics** with category
    **`Engine`** (same AS-engine traces) plus **`AllMetrics`** — useful when auditing
    A-SKU Embedded capacities that live as Azure resources.
  - **`Microsoft.Fabric` / Power BI Premium (P-SKU) and Fabric (F-SKU)** capacities surface
    their semantic-model engine telemetry through the **workspace-level LA connection** (this
    file) and/or **Fabric Workspace Monitoring** (Item 12) — not through a standalone
    `microsoft.fabric` diagnostic-settings engine category in the same way A-SKUs do.
  - Diagnostic Settings route resource logs to **Log Analytics, Storage, Event Hubs, or
    Marketplace partners**; for Power BI the supported destination via the PBI connection is
    **Log Analytics only** (no Blob/Event Hubs — Item 6).
- **HOW IT HELPS:** Tells the agent there are **two doors** to the same AS-engine telemetry:
  (1) the Power BI workspace LA connection (P/F-SKU + PPU), and (2) **Diagnostic Settings on a
  `Microsoft.PowerBIDedicated` capacity** (A-SKU Embedded). For A-SKU Embedded estates the agent
  should look for an `Engine`-category diagnostic setting; for Premium/Fabric it should look for
  the workspace LA connection or Workspace Monitoring.

> NOTE: exact A-SKU `Engine` category wording should be re-verified against
> `resource-logs-categories` for `Microsoft.PowerBIDedicated` at audit time, as category names
> evolve.

---

## Item 12 — Log Analytics vs Fabric Workspace Monitoring (when to use which; can't have both)

- **TITLES / URLs:**
  - Workspace Monitoring overview:
    https://learn.microsoft.com/en-us/fabric/fundamentals/workspace-monitoring-overview
  - Semantic model operation logs (`SemanticModelLogs` schema):
    https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations
- **MUTUAL-EXCLUSIVITY RULE (verbatim):** "You can only enable **either** workspace monitoring
  **or** log analytics in a workspace. You **can't enable both** at the same time. To enable
  workspace monitoring in a workspace that already has log analytics enabled, **delete the log
  analytics configuration and wait for a few hours** before enabling workspace monitoring."
- **WHERE THE DATA LANDS:**
  - **Log Analytics** → Azure Monitor table **`PowerBIDatasetsWorkspace`**, queried with KQL via
    Azure (LA workspace lives in *your Azure subscription*; ~5-min latency; you own retention/cost).
  - **Workspace Monitoring** → a **read-only Eventhouse (KQL DB) inside the Fabric workspace**,
    table **`SemanticModelLogs`** (+ Eventhouse/GraphQL/mirrored/item-job tables), queried with
    **KQL or SQL**; billed against **Fabric capacity**; **30-day fixed retention**; accessible to
    workspace **contributors** (no Azure RBAC needed); private links **not** supported.
- **SCHEMA DIFFERENCES — `SemanticModelLogs` (Workspace Monitoring) vs
  `PowerBIDatasetsWorkspace` (Log Analytics)** — same AS-engine events, **renamed item/workspace
  columns**:
  | Concept | Log Analytics (`PowerBIDatasetsWorkspace`) | Workspace Monitoring (`SemanticModelLogs`) |
  | --- | --- | --- |
  | Item id | `ArtifactId` | **`ItemId`** |
  | Item name | `ArtifactName` | **`ItemName`** |
  | Item type | `ArtifactKind` | **`ItemKind`** |
  | Workspace id | `PowerBIWorkspaceId` | **`WorkspaceId`** |
  | Workspace name | `PowerBIWorkspaceName` | **`WorkspaceName`** |
  | Capacity id | `PremiumCapacityId` | **`CapacityId`** |
  | Timestamp | `TimeGenerated` | **`Timestamp`** |
  | Request id | `XmlaRequestId` | `XmlaRequestId` (+ alias **`OperationId`**) |
  | CPU / duration / user | `CpuTimeMs`, `DurationMs`, `ExecutingUser`, `User` | **identical names** |
  | Op | `OperationName`, `OperationDetailName` | **identical names** |
  | Category | `LogAnalyticsCategory` | **`Category`** |
  | Extra in WM | — | `CallerIpAddress`, `Region`, `WorkspaceMonitoringTableName` |
  - **`OperationName` values are the same** in both (`QueryEnd`, `CommandEnd`, `DiscoverEnd`,
    `ProgressReportEnd`, `ExecutionMetrics`, …) — the `ExecutionMetrics` JSON is shared
    (both docs point to the same "Events and schema" reference, Item 4).
- **WHEN TO USE WHICH (agent guidance):**
  - **Log Analytics** — when telemetry must live in **Azure** (central SIEM/governance, cross-
    service KQL with other Azure logs), you want **>30-day / up-to-2-year retention**, Azure RBAC
    control, and Premium/PPU coverage; cost is **separate Azure spend** (~35 GB/mo/capacity).
  - **Workspace Monitoring** — when you want **in-Fabric, no-Azure-subscription** monitoring,
    **SQL or KQL**, contributor-level access, and you're on **Fabric capacity**; accept **fixed
    30-day retention** and **Fabric CU** consumption; not for >30-day history or private links.
- **HOW IT HELPS:** This is the **decision the agent must respect per workspace**: it cannot
  read both sources for one workspace, so its collector must **detect which is enabled** and
  **switch column names** (`Artifact*`/`PowerBIWorkspace*` vs `Item*`/`Workspace*`,
  `TimeGenerated` vs `Timestamp`). The shared `OperationName`/`ExecutionMetrics` contract means
  the agent's CPU/throttle logic is portable across both once column aliases are normalized.

---

## Item 13 — Querying Log Analytics from Python (`azure-monitor-query` — read-only)

- **TITLE:** Azure Monitor Query client library for Python
- **URLs:**
  - Readme/overview: https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-query-readme?view=azure-python
  - `LogsQueryClient` class: https://learn.microsoft.com/en-us/python/api/azure-monitor-query/azure.monitor.query.logsqueryclient?view=azure-python
- **INSTALL / AUTH:** `pip install azure-monitor-query` (+ `azure-identity`; +`aiohttp` for async,
  +`pandas` for DataFrames). Python 3.9+. Read-only client. Auth via **`DefaultAzureCredential`**
  → the agent's service principal needs **Log Analytics Reader** (`…/query/*/read`) on the LA
  workspace (Item 10). **v2.0.0+**: metrics moved out — use `azure-monitor-querymetrics` for
  metrics; logs stay in `azure-monitor-query`.
- **CORE CALL (`query_workspace`):**
  ```python
  from datetime import timedelta
  import os, pandas as pd
  from azure.identity import DefaultAzureCredential
  from azure.monitor.query import LogsQueryClient, LogsQueryStatus
  from azure.core.exceptions import HttpResponseError

  client = LogsQueryClient(DefaultAzureCredential())
  query = """
  PowerBIDatasetsWorkspace
  | where OperationName == 'QueryEnd'
  | summarize TotalCpuMs=sum(CpuTimeMs), Queries=count()
    by ExecutingUser, ArtifactName, PowerBIWorkspaceId
  """
  try:
      resp = client.query_workspace(
          os.environ["LOG_WORKSPACE_ID"],   # the LA workspace GUID
          query,
          timespan=timedelta(days=7),
      )
      if resp.status == LogsQueryStatus.SUCCESS:
          tables = resp.tables
      else:  # LogsQueryPartialResult
          print(resp.partial_error); tables = resp.partial_data
      for t in tables:
          df = pd.DataFrame(data=t.rows, columns=t.columns)
  except HttpResponseError as err:
      print("fatal", err)
  ```
- **OTHER METHODS / PARAMS the agent will use:**
  - **`query_resource(resource_id, query, timespan=...)`** — query directly against an Azure
    resource id (e.g. a `Microsoft.PowerBIDedicated/capacities/...` resource) **without** a
    workspace id. Resource id form:
    `/subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/{type}/{name}`.
  - **`timespan`** = `timedelta` | `(start_datetime, timedelta)` | `(start_datetime,
    end_datetime)` (use tz-aware UTC datetimes).
  - **`server_timeout`** seconds (default **180**, max **600**).
  - **`additional_workspaces=[...]`** — query **multiple LA workspaces** in one call (names,
    workspace IDs, or Azure resource IDs) — useful when the estate fans into several LA workspaces.
  - **`include_statistics=True`** → `result.statistics["query"]["executionTime"]` etc.
  - **`include_visualization=True`** → `result.visualization` (render operator output).
  - **Batch:** `query_batch([LogsBatchQuery(query=..., timespan=..., workspace_id=...), ...])`
    returns a list of `LogsQueryResult` / `LogsQueryPartialResult` / `LogsQueryError`
    (throttled → `LogsQueryError.code == "ThrottledError"`).
  - **Result shape:** `LogsQueryResult.tables[i].rows / .columns / .columns_types`; iterate
    `for table in response:` then `pd.DataFrame(table.rows, columns=table.columns)`.
- **SOVEREIGN CLOUD:** `DefaultAzureCredential(authority=AzureAuthorityHosts.AZURE_GOVERNMENT)` +
  `LogsQueryClient(credential, endpoint="https://api.loganalytics.us")` (matches the PBI LA US
  Gov/DoD support note in Item 1).
- **RATE LIMITS:** Throttling + max-rows limits apply (Query API limits); handle partial results.
- **HOW IT HELPS:** This is exactly how the agent's collector pulls per-user/per-item CPU
  read-only: `LogsQueryClient.query_workspace` against the LA workspace GUID, push the KQL from
  Item 5, get rows back as a pandas DataFrame, and batch/multi-workspace to scale across the
  estate — all with a least-privilege **Log Analytics Reader** SP.

---

## Flat URL list (all sources)

1. https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview
2. https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure
3. https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure#events-and-schema
4. https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-faq
5. https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/powerbidatasetsworkspace
6. https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/powerbidatasetstenant
7. https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables-category
8. https://learn.microsoft.com/en-us/azure/azure-monitor/logs/log-powerbi
9. https://learn.microsoft.com/en-us/azure/azure-monitor/logs/cost-logs
10. https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-table-plans
11. https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-retention-configure
12. https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-platform-logs
13. https://learn.microsoft.com/en-us/analysis-services/trace-events/analysis-services-trace-events
14. https://learn.microsoft.com/en-us/azure/azure-monitor/platform/resource-logs-categories
15. https://learn.microsoft.com/en-us/azure/azure-monitor/platform/diagnostic-settings
16. https://learn.microsoft.com/en-us/power-bi/developer/embedded/monitor-power-bi-embedded-reference
17. https://learn.microsoft.com/en-us/fabric/fundamentals/workspace-monitoring-overview
18. https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations
19. https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-query-readme?view=azure-python
20. https://learn.microsoft.com/en-us/python/api/azure-monitor-query/azure.monitor.query.logsqueryclient?view=azure-python
21. https://github.com/microsoft/PowerBI-LogAnalytics-Template-Reports
22. https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/workspace-monitoring-dashboards

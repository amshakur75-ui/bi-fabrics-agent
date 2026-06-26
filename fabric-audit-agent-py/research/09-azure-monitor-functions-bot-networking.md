# Azure Platform Services for the bi-fabrics-audit-agent

Research focus: Azure Monitor (platform metrics, diagnostic settings, metric/alert rules,
action groups), Azure Functions, Logic Apps, Azure Bot Service / Bot Framework (incl. Teams
proactive messaging), Copilot Studio, and Azure networking (Private Link / VNet) for
Databricks <-> Fabric. Read-only Fabric/PBI capacity audit agent with near-real-time alerting
and a two-way Teams bot.

> Scope note: OAuth scopes, Fabric/PBI REST, and Log Analytics for Power BI / Activity Events /
> Capacity Metrics are covered by a separate agent and are intentionally NOT duplicated here.

Date researched: 2026-06-23. All sources are learn.microsoft.com unless noted.

---

## TL;DR — the load-bearing findings

1. **`Microsoft.Fabric/capacities` exposes NO Azure Monitor platform metrics.** It does not
   appear anywhere in the Azure Monitor "supported metrics by resource type" index
   (regenerated 2026-06-19). There is therefore **no metric definitions REST API, no metrics
   explorer chart, and no metric-alert rule** you can build directly on a Fabric capacity ARM
   resource. Fabric capacity health/CU% lives in the **Fabric Capacity Metrics app** (a Power BI
   semantic model) which, per Microsoft, **"doesn't support alerts or notifications."** So
   near-real-time CU% alerting cannot come from Azure Monitor on the Fabric resource — it must
   come from the Metrics app semantic model (XMLA/DAX query) or Fabric **Real-Time hub /
   Activator**, with the agent providing the alert glue.
2. **`Microsoft.PowerBIDedicated/capacities` (P/A/EM SKUs, "Gen2") DOES expose platform
   metrics**: `cpu_metric` (CPU %) and `overload_metric` (Overload 0/1), both PT1M, both
   exportable via diagnostic settings, plus an `Engine` resource-log category. So for the
   **Power BI Embedded / Premium-capacity-as-Azure-resource** path you CAN use Azure Monitor
   metric alerts + action groups for near-real-time alerting. (Fabric F-SKUs do not surface here.)
3. **Action groups are the alerting fan-out hub**: email / SMS / push / voice + automated
   actions (Azure Function, Logic App, webhook, secure webhook, event hub, automation runbook,
   ITSM). To reach Teams, the supported pattern is **action group → Logic App (or Function) →
   Teams**, because action-group webhooks emit the Azure alert schema, not the Teams schema.
4. **Two-way Teams bot**: use **Azure Bot Service / Bot Framework** (resource type
   `Microsoft.BotService/botServices`) with the **Teams channel**. Proactive (agent-initiated)
   messages require a stored **conversation reference** and `ContinueConversationAsync`; the app
   must be installed in the user/team scope first. **Copilot Studio** is the low-code
   alternative (publishes to Teams, calls REST/connectors/flows) but gives less control.
5. **Networking**: Databricks and Fabric secure connectivity via **Azure Private Link +
   private endpoints**. Databricks uses sub-resources `databricks_ui_api` and
   `browser_authentication`; serverless egress uses **NCC** private endpoints. Fabric uses
   **tenant-level and workspace-level private links** (inbound only; private endpoint guarantees
   inbound path, not Fabric→external egress). Note: **the Fabric Capacity Metrics app does NOT
   support Private Link**, and Fabric private endpoints are one-directional/inbound.

---

## 1. Azure Monitor — platform metrics for Fabric / Power BI capacities

### 1.1 Azure Monitor supported metrics by resource type (the index)
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/reference/metrics-index
- **Summary:** Master index of all platform (auto-collected) metrics per resource provider/type,
  regenerated 2026-06-19. Lists which resource types have metrics vs. log categories. To query
  the list programmatically use the `2018-01-01` metricDefinitions api-version. Export options:
  metrics export via Data Collection Rules (DCRs), diagnostic settings, or the Metrics REST API.
- **Exact identifiers found in the index (load-bearing):**
  - `Microsoft.Fabric` — **ABSENT from the index entirely** (no metrics, no log categories listed).
  - `Microsoft.PowerBIDedicated` → metrics: `capacities`; logs: `capacities`.
  - `Microsoft.PowerBI` → metrics: **N/A**; logs: `tenants`, `tenants/workspaces`.
  - `Microsoft.Databricks` → metrics: **N/A**; logs: `workspaces`.
  - `Microsoft.AnalysisServices` → metrics+logs: `servers` (relevant analogue: AAS QPU/memory).
  - `Microsoft.BotService` → metrics: `botServices/channels`, `botServices/connections`, etc.;
    (note logs N/A in this row, but a `microsoft.botservice/botservices` logs entry also exists).
  - `Microsoft.Logic` → metrics: `Workflows`, `IntegrationServiceEnvironments`; logs: `Workflows`,
    `IntegrationAccounts`.
  - `Microsoft.Web/sites` (Functions/App Service) → metrics + logs `sites`, `sites/slots`.
  - `Microsoft.PowerPlatformMonitoringHub` → metrics: `copilotstudio`, `powerautomate`,
    `powerapps`, `microsoftapp` (Copilot Studio / Power Platform telemetry surface).
- **How it helps:** Definitively settles the core architecture question — **the agent cannot
  rely on Azure Monitor platform metrics for Fabric F-SKU capacities**; it can for
  PowerBIDedicated (P/A/EM) capacities, Bot Service, Logic Apps, and Functions.

### 1.2 Supported metrics — Microsoft.PowerBIDedicated/capacities
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-powerbidedicated-capacities-metrics
- **Alt/equivalent source (used to extract values):**
  https://learn.microsoft.com/en-us/power-bi/developer/embedded/monitor-power-bi-embedded-reference
- **Summary + exact identifiers (REST API metric names):**
  - `cpu_metric` — "CPU (Gen2)", Percent, Avg aggregation, no dimensions, time grain PT1M,
    **DS Export = Yes**.
  - `overload_metric` — "Overload (Gen2)", Count, Avg, no dimensions, PT1M, **DS Export = Yes**
    (1 if capacity overloaded else 0).
  - Also referenced: `cpu_workload_metric` ("CPU per workload").
  - Resource log category: **`Engine`** (Audit Login, Query Begin/End, VertiPaq Query Begin/End,
    Session Initialize, Audit Logout, Error). Tables: `AzureDiagnostics`, `AzureMetrics`,
    `AzureActivity`. Diagnostic categories selectable: **`Engine`** and **`AllMetrics`**.
  - Schema = "Power BI Dedicated". No multi-dimensional metrics.
- **How it helps:** `overload_metric = 1` and `cpu_metric > threshold` are perfect near-real-time
  (1-minute) **metric-alert** signals for the Embedded/Premium-capacity-as-resource path. Both
  are exportable to Log Analytics/Event Hub/Storage via a diagnostic setting.

### 1.3 Monitoring data reference for Power BI Embedded
- **URL:** https://learn.microsoft.com/en-us/power-bi/developer/embedded/monitor-power-bi-embedded-reference
- **Summary:** Canonical monitoring reference for `Microsoft.PowerBIDedicated/capacities`:
  the two metrics above, the `Engine` resource-log category and its event schema, the Logs
  tables, and a sample **Azure Automation runbook** (`ScaleUp-Automation-RunBook.ps1`) that an
  Azure alert can trigger to scale a capacity. Recommends the Fabric Capacity Metrics app for
  capacity monitoring.
- **How it helps:** Shows the alert→runbook auto-remediation pattern (read-only agent would
  *recommend* the scale action rather than execute it) and documents the exact log events
  available for query-based detection.

### 1.4 Microsoft Fabric Capacity Metrics app (where Fabric CU% actually lives)
- **URL:** https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
- **Summary:** The Power BI app that monitors Fabric capacity CU consumption, throttling,
  overload, autoscale, storage. Pages: Health, Compute (14-day), Storage, Timepoint (30-sec
  granularity), Autoscale-for-Spark, Plan capacity. Backed by a **semantic model refreshed at
  midnight** for dimensions; usage data available **~10–15 min after activity**. Capacity-admin
  only to install/view; supports EM/A and P SKUs.
- **Exact identifiers / load-bearing limits:**
  - **"The Microsoft Fabric Capacity Metrics app doesn't support alerts or notifications. For
    real-time alerts, see Real-Time hub."** ← decisive constraint.
  - "The semantic model ... is only supported for use by the reports provided in the
    application. Any consumption from, usage of, or modification of the semantic model isn't
    supported." (i.e., querying it via XMLA is unsupported, though technically possible.)
  - Supports **tenant-level private links**; **workspace-level private links NOT supported** on
    workspaces where the app is installed.
- **How it helps:** Confirms the agent must source Fabric CU%/throttling from the Metrics app
  model (DAX/XMLA, unsupported) or from Real-Time hub / Activator / Log Analytics — and must
  supply its own alerting brain. Azure Monitor is not an option for Fabric capacity metrics.

### 1.5 Azure Monitor Metrics & Metric Definitions REST APIs
- **URLs:**
  - Metrics - List: https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list
  - Metric Definitions: https://learn.microsoft.com/en-us/rest/api/monitor/metric-definitions
  - REST API walkthrough: https://learn.microsoft.com/en-us/azure/azure-monitor/platform/rest-api-walkthrough
- **Summary:** ARM data-plane APIs under `https://management.azure.com/`. Pattern:
  `GET .../{resourceId}/providers/Microsoft.Insights/metricDefinitions?api-version=...` to list
  definitions, and `.../providers/Microsoft.Insights/metrics?...` to read values. All platform
  metrics are queryable via this API even if not exportable via diagnostic settings.
- **How it helps:** This is how the agent would programmatically pull `cpu_metric` /
  `overload_metric` for a PowerBIDedicated capacity (read-only) without standing up a workspace.

---

## 2. Diagnostic settings — routing logs/metrics to LA / Event Hub / Storage

- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings
  (also resolves at .../platform/diagnostic-settings)
- **Summary:** A diagnostic setting routes **platform metrics (`AllMetrics`)**, **resource logs**
  (by category or `allLogs`/`audit` category groups), and the activity log to destinations.
- **Exact identifiers / constraints:**
  - ARM resource type: **`Microsoft.Insights/diagnosticSettings`** (sample apiVersion
    `2021-05-01-preview`). Create via portal/PowerShell (`New-AzDiagnosticSetting`)/CLI
    (`az monitor diagnostic-settings create`)/ARM/Bicep/REST (`Diagnostic Settings - Create Or
    Update`).
  - Destinations: **Log Analytics workspace**, **Azure Storage account**, **Azure Event Hubs**,
    **Azure Monitor partner solutions**. One of each type per setting; **max 5 settings per
    resource**.
  - Regional resources require the Storage/Event Hub to be in the **same region**.
  - With VNets enabled you must "**Allow trusted Microsoft services**" to bypass Storage/Event
    Hub firewall.
  - Metrics limitations: multi-dimensional metrics are flattened/aggregated when exported;
    not all metrics are exportable (see "Exportable" column). Latency: data flows within ~90 min
    of setting creation; inactive resources back off (up to 2 h after 7 days idle).
- **How it helps:** For the PowerBIDedicated path, stream `Engine` logs + `AllMetrics` to a Log
  Analytics workspace (for KQL detectors) and/or to **Event Hub for near-real-time** consumption
  by an Azure Function/agent. Event Hub is the right destination for low-latency push pipelines.

---

## 3. Metric alerts, alert types, and action groups (the alerting layer)

### 3.1 Types of Azure Monitor alerts
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types
- **Summary:** Metric alerts (near-real-time, precomputed metric data, stateful by default,
  multi-condition, multi-resource, dimension splitting, dynamic ML thresholds); log search
  alerts (KQL, advanced logic, billed by evaluation interval); simple log search alerts (per-row,
  billed as 1-min); activity log alerts (event-driven, stateless — Service Health, Resource
  Health); Prometheus alerts; query-based metric alerts (preview, PromQL/OTel).
- **Exact identifiers:** Log search alerts managed via **`ScheduledQueryRules`** API; billed
  resource provider `microsoft.insights/scheduledqueryrules`. Metric alerts support **dynamic
  thresholds** (ML) and **splitting by dimensions** for at-scale resource-centric alerts.
- **How it helps:** Decision guide — metric alerts for the PowerBIDedicated CPU/overload signals;
  **log search alerts** (KQL over Log Analytics) for Fabric, since Fabric data must arrive as
  logs (activity events / capacity data ingested to LA), not platform metrics. Dynamic thresholds
  reduce the need to hardcode CU% thresholds.

### 3.2 Create or edit a metric alert rule
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-metric-alert-rule
- **Summary:** How to scope, pick a signal/condition, set static vs dynamic thresholds, choose
  aggregation granularity + evaluation frequency, attach action groups, and split by dimensions.
- **How it helps:** Recipe for wiring `overload_metric`/`cpu_metric` alerts to action groups.
  (Related: Tutorial - create a metric alert
  https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/tutorial-metric-alert ; metric
  alerts for logs https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-metric-logs)

### 3.3 Create and manage action groups
- **URL:** https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups
- **Summary:** Action groups define **who is notified** and **what runs** when an alert fires.
- **Exact identifiers:**
  - ARM resource type: **`Microsoft.Insights/actionGroups`** (sample apiVersion `2021-09-01`,
    location `Global`). Up to **5 action groups per alert rule**; actions run concurrently.
  - **Notification types:** Email, Email ARM role, SMS (bi-directional), Azure app push, Voice.
  - **Action types:** **Automation Runbook**, **Event Hubs** (only action type supporting Private
    Link / network security perimeter), **Functions** (calls existing **HTTP trigger** endpoint;
    stores function URL + access key in the action), **ITSM**, **Logic Apps**, **Secure webhook**
    (Microsoft Entra-secured), **Webhook**.
  - **Teams caveat (load-bearing):** action-group webhooks emit the Azure alert JSON, not the
    Teams schema — "**If the webhook endpoint expects a specific schema, for example, the
    Microsoft Teams schema, use the Logic Apps action to transform the alert schema.**"
  - Webhook retry: up to 5 retries (5/20/5/40/5 s), then 15-min cooldown; retried on 408/429/
    503/504.
  - **Managed Identity (preview)** supported for Logic App, Event Hub, Automation Runbook actions
    (NOT for Azure Function, Secure webhook, ITSM, Webhook).
  - Common alert schema toggle per receiver.
- **How it helps:** This is the alert fan-out hub. For the two-way Teams surface, the canonical
  chain is **metric/log alert → action group → Logic App (Teams connector) → Teams channel/chat**,
  or **action group → Function (HTTP) → Bot Service proactive message**. Event Hub action enables
  a private-link-friendly streaming path to the agent.

---

## 4. Azure Functions — HTTP/timer triggers + bindings (integration glue)

- **URL:** https://learn.microsoft.com/en-us/azure/azure-functions/functions-overview
- **Summary:** Serverless compute; event-driven **triggers and bindings** connect to services
  with little code. Relevant scenarios: **HTTP trigger** (build REST/webhook endpoints — e.g. the
  bot messaging endpoint, the action-group Function target, the Teams `/api/notify` proactive
  endpoint), **Timer trigger** (scheduled polling of Fabric/PBI REST or the Metrics app on a CRON),
  queue/Event Hubs/Service Bus triggers (consume the diagnostic-settings Event Hub stream),
  Durable Functions (orchestration). Languages: C#, Java, JS, PowerShell, Python, Go (custom
  handlers for Rust). Built-in Azure Monitor + App Insights integration.
- **Exact identifiers / hosting options:** **Flex Consumption** (recommended; event-driven scale,
  VNet integration, pay-as-you-go), **Premium** (always-warm, unlimited duration, VNet),
  **Dedicated** (App Service plan), **Container Apps**, legacy **Consumption**.
- **How it helps:** The agent's natural home. A **Timer-triggered** Function runs the periodic
  read-only capacity audit (Fabric/PBI REST + metrics REST API); an **HTTP-triggered** Function
  serves both the Bot Framework messaging endpoint (inbound Teams) and the action-group webhook
  target. Premium/Flex + VNet integration lets the Function reach private-endpoint'd resources.

---

## 5. Azure Logic Apps — orchestration / Teams posting / alert transform

- **URL:** https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-overview
- **Summary:** Low-code workflow platform, 1,400+ connectors. **Consumption** (multitenant,
  pay-per-execution, single workflow) vs **Standard** (single-tenant, multiple workflows,
  **VNet/private endpoint integration**, more built-in connectors, dedicated/static outbound IPs).
  Triggers: **Recurrence** (schedule) and **Request** (HTTP, "wait until called") built-ins;
  managed connectors for **Office 365 / Microsoft Teams**. ARM-template deployable. Guaranteed
  at-least-once delivery.
- **Exact identifiers:** resource provider `Microsoft.Logic/workflows`. Built-in **Recurrence**
  and **Request/Response** triggers; managed **Microsoft Teams** connector (post message to
  channel/chat, post adaptive card).
- **How it helps:** The transform/posting layer between an Azure Monitor action group and Teams —
  receives the alert payload (Request trigger), reshapes it into a Teams adaptive card, and posts
  via the Teams connector. **Standard** Logic Apps can run inside a VNet to reach private
  resources. Also handles scheduled pulls (Recurrence) if you prefer no-code over a Timer Function.

---

## 6. Azure Bot Service / Bot Framework — the inbound two-way Teams bot

### 6.1 Bot Service / Bot Framework overview
- **URL:** https://learn.microsoft.com/en-us/azure/bot-service/bot-service-overview
- **Summary:** Build/host a bot as an Azure-hosted web service with a **messaging endpoint**; the
  **Bot Connector Service** relays normalized messages between the bot and **channels**
  (Microsoft Teams, Web Chat, etc.). SDKs in C#/JS/Python/Java. Connect-to-channel step adds the
  **Teams channel**.
- **Exact identifiers:** ARM resource type **`Microsoft.BotService/botServices`** (the "Azure
  Bot" resource). Bot authenticates with a **Microsoft App ID** (single-tenant, multi-tenant, or
  **managed identity** app types). Metrics surface under `Microsoft.BotService/botServices/channels`
  and `.../connections`.
- **Important deprecation:** The **Bot Framework SDK is archived** (support ends 2025-12-31).
  Microsoft now points new builds to the **Microsoft 365 Agents SDK** (`aka.ms/agents`,
  C#/JS/Python) or the **Teams SDK (Teams AI Library)**; **Copilot Studio** for SaaS/low-code.
  Azure Bot Service (the channel/connector infra) remains.
- **How it helps:** This is the inbound surface — users @mention/DM the bot in Teams to query the
  audit agent. The Azure Bot resource + Teams channel + an HTTP messaging endpoint (hosted on the
  Function/App Service) is the minimal plumbing. Plan new code on the Agents SDK, not the archived
  Bot Framework SDK.

### 6.2 Proactive messaging (general Bot Framework)
- **URL:** https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-howto-proactive-message
- **Summary:** A **proactive message** is sent not in response to a user turn (e.g. a CU-overload
  alert). The bot must first capture and persist a **conversation reference** (from any prior
  activity — includes `conversation`, `user`, and **`serviceUrl`**), then call the adapter's
  **`ContinueConversationAsync`** (C#) / `continueConversation` with that reference + a callback
  that sends the message. A separate endpoint (e.g. `/api/notify`) triggers the proactive turn.
  The adapter creates a `ContinueConversation` event activity.
- **Exact identifiers / caveats:** Persist conversation references in a DB (not in-memory). If
  the `serviceUrl` changes, old references break and you must reacquire one. Many channels block
  bot-initiated messages unless the user messaged first; **Teams allows proactive 1:1 messages if
  the user is in an established group conversation that includes the bot.**
- **How it helps:** This is exactly how the agent pushes a near-real-time CU/overload/security
  alert into Teams. The alert pipeline (action group → Function/Logic App → bot `/api/notify`)
  uses the stored conversation reference to deliver the proactive message.

### 6.3 Send proactive messages — Microsoft Teams specifics
- **URL:** https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages
- **Summary:** Teams steps: get the **Entra user ID / userId / teamId / channelId**, create the
  conversation if needed, get the **conversationId**, send via `continueConversation`. The app
  **must be installed** in that scope first (personal/team/group), else `403`
  `ForbiddenOperationException`. Cannot create new group chats/channels proactively.
- **Exact identifiers:**
  - To create a conversation you need `aadObjectId` or `userId`, `tenantId`, and `serviceUrl`.
  - **Global proactive `serviceUrl` endpoints** (use only when not available from an incoming
    activity): Public `https://smba.trafficmanager.net/teams/`; GCC
    `https://smba.infra.gcc.teams.microsoft.com/teams`; GCC High
    `https://smba.infra.gov.teams.microsoft.us/teams`; DoD
    `https://smba.infra.dod.teams.microsoft.us/teams`.
  - `aadObjectId` proactive messaging supported **only in personal scope**. No email/UPN.
  - Blocked/uninstalled detection: `403` with `subCode: MessageWritesBlocked`.
  - Mass install via **Microsoft Graph** (`userteamwork-post-installedapps`) or custom app policy.
  - Update/delete sent messages via `UpdateActivityAsync` / `DeleteActivityAsync`
    (`PUT {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}`).
- **How it helps:** Operational detail for delivering alerts to specific capacity admins/owners in
  Teams; clarifies the install-first requirement and the regional serviceUrl handling for a
  read-only audit agent that pushes notifications.

---

## 7. Copilot Studio — low-code bot alternative

- **URL:** https://learn.microsoft.com/en-us/microsoft-copilot-studio/fundamentals-what-is-copilot-studio
- **Summary:** Graphical low-code platform for building **agents** (topics + generative answers +
  tools) and **flows**. Agents engage users "across websites, mobile apps, Facebook, **Microsoft
  Teams, or any channel supported by the Azure Bot Service**." Connects to data via prebuilt/custom
  **connectors**, can **retrieve real-time data from external APIs**, take actions on triggers, and
  run **Power Automate / agent flows**. Standalone subscription = full generative agents; **Teams
  plan** = classic-orchestration agents published to Teams only.
- **Exact identifiers / caveats:** Telemetry surfaces under `Microsoft.PowerPlatformMonitoringHub/
  copilotstudio` (see §1.1). Note: "After end of June 2026 it will no longer be possible to use the
  Copilot Studio for Teams app to create classic chatbots" (redirects to the web app).
- **How it helps:** If the team wants the conversational Teams surface without writing/maintaining
  a custom bot, Copilot Studio can publish a Teams agent that calls the audit agent's REST API
  (via custom connector / flow) for pull queries. Trade-off vs Bot Service: faster + low-code, but
  less control over orchestration, branding, model choice, and proactive-message timing.

---

## 8. Azure networking — Private Link / VNet for Databricks <-> Fabric

### 8.1 Azure Private Link concepts — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/concepts/private-link
  (canonical; the older `.../classic/private-link` path redirects here)
- **Summary:** Three Private Link patterns: **Inbound (front-end)** secures user→workspace;
  **Outbound (serverless)** secures serverless compute→your Azure resources via **NCC**;
  **Classic (back-end)** secures classic compute→control plane. Can enforce private-only and reject
  public connections.
- **Exact identifiers:**
  - Private endpoint sub-resources: **`databricks_ui_api`** (workspace REST/UI, used for front-end
    and back-end) and **`browser_authentication`** (SSO callback; one per region + private DNS
    zone).
  - Outbound uses **Network Connectivity Configuration (NCC)** — account-level, regional; private
    endpoint rules; ≤10 NCCs/region, 100 PEs/region, ≤50 workspaces/NCC.
  - Two VNets: **transit VNet** (inbound PEs) and **workspace VNet** (VNet injection + classic PE).
    Prereqs for back-end/front-end: Premium plan, VNet injection, Secure Cluster Connectivity (SCC).
  - NSG on the PE subnet must allow ports 443, 6666, 3306, 8443-8451.
- **How it helps:** If the agent runs inside/near Databricks (the project has Databricks-native
  adapters) and must read Databricks privately, this defines the endpoints/sub-resources to
  provision. NCC outbound PEs are how Databricks serverless reaches private Azure data sources.

### 8.2 Private Links for secure access to Microsoft Fabric
- **URL:** https://learn.microsoft.com/en-us/fabric/security/security-private-links-overview
- **Summary:** Fabric supports **tenant-level** and **workspace-level** private links via Azure
  private endpoints; traffic uses the Microsoft backbone. Two admin tenant settings: **Azure
  Private Links** and **Block Public Internet Access**. Private endpoints are **inbound and
  one-directional** — they guarantee the path *into* Fabric (e.g. OneLake upload), **not** Fabric→
  external data-source egress (secure those with the source's own firewall/PE). Enabling Private
  Link + Spark creates a **managed VNet** per workspace (starter pools disabled, on-demand pools).
- **Exact identifiers / load-bearing limits:**
  - Single-tenant boundary; **no cross-tenant** private link (use OneLake data sharing instead).
  - OneLake, Warehouse/Lakehouse SQL endpoint (TDS), SQL database, Eventstream, Eventhouse,
    Data Activator, Dataflow Gen2 (via **VNet data gateway**), Pipeline, API for GraphQL, Data
    agent, mirrored DB (selected sources) support Private Link with documented limitations.
  - **`The Microsoft Fabric Capacity Metrics app doesn't support Private Link.`** ← directly
    affects the agent's capacity-data source path.
  - On-prem data gateway NOT supported under Private Link; **VNet data gateway** is.
  - Copilot not supported under Private Link/closed network. Up to 450 capacities per PL tenant;
    new capacity not PL-usable until DNS reflects (≤24 h).
- **How it helps:** Defines how the agent connects to Fabric securely and the constraints to
  design around — notably that the Capacity Metrics app (the only Fabric CU source) is *not*
  reachable over Private Link, and Fabric PEs don't secure Fabric→Databricks egress (Databricks
  side must use its own NCC/firewall). For Databricks↔Fabric, secure each leg independently:
  Fabric inbound PE + Databricks NCC/outbound PE, or VNet data gateway for gateway-style access.

---

## 9. Recommended Azure architecture for this agent (synthesis)

- **Pull/audit engine:** Timer-triggered **Azure Function** (Flex/Premium + VNet) or Databricks
  job — read-only Fabric/PBI REST + Metrics REST API for PowerBIDedicated metrics + Fabric
  Capacity Metrics app model for Fabric CU%.
- **Near-real-time alerting:**
  - PowerBIDedicated path: Azure Monitor **metric alert** on `cpu_metric`/`overload_metric` (PT1M)
    → **action group**.
  - Fabric path: Fabric data → Log Analytics (separate agent) → **log search alert**, or Fabric
    **Real-Time hub / Activator**; the agent supplies the reasoning brain.
- **Fan-out:** **Action group** → **Logic App** (transform to Teams adaptive card) → Teams, and/or
  → **Function (`/api/notify`)** → **Bot Service** proactive message. Use **Event Hub** action /
  diagnostic-settings Event Hub for the lowest-latency streaming.
- **Two-way Teams:** **Azure Bot Service** + Teams channel + HTTP messaging endpoint (on the
  Function/App Service); build new code on the **Microsoft 365 Agents SDK** (Bot Framework SDK is
  archived). **Copilot Studio** is the low-code alternative for pull-only Q&A.
- **Networking:** Fabric tenant/workspace **Private Link** (inbound); Databricks
  `databricks_ui_api`/`browser_authentication` PEs + **NCC** for serverless egress; **Standard**
  Logic Apps / Premium Functions for VNet reach. Remember: Fabric Capacity Metrics app is **not**
  PL-compatible.

---

## Flat URL list (all sources)

- https://learn.microsoft.com/en-us/azure/azure-monitor/reference/metrics-index
- https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-powerbidedicated-capacities-metrics
- https://learn.microsoft.com/en-us/power-bi/developer/embedded/monitor-power-bi-embedded-reference
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
- https://learn.microsoft.com/en-us/rest/api/monitor/metrics/list
- https://learn.microsoft.com/en-us/rest/api/monitor/metric-definitions
- https://learn.microsoft.com/en-us/azure/azure-monitor/platform/rest-api-walkthrough
- https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings
- https://learn.microsoft.com/en-us/azure/azure-monitor/platform/diagnostic-settings
- https://learn.microsoft.com/en-us/rest/api/monitor/diagnostic-settings/create-or-update
- https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types
- https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-metric-alert-rule
- https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/tutorial-metric-alert
- https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-metric-logs
- https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-dynamic-thresholds
- https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/action-groups
- https://learn.microsoft.com/en-us/azure/azure-functions/functions-overview
- https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-overview
- https://learn.microsoft.com/en-us/azure/bot-service/bot-service-overview
- https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-howto-proactive-message
- https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages
- https://learn.microsoft.com/en-us/microsoft-copilot-studio/fundamentals-what-is-copilot-studio
- https://learn.microsoft.com/en-us/azure/databricks/security/network/concepts/private-link
- https://learn.microsoft.com/en-us/fabric/security/security-private-links-overview

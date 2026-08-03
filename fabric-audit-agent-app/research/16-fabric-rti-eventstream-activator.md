# 16 — Fabric Real-Time Intelligence: Eventstream + Activator (Reflex) as the Agent's Native Near-Real-Time Alerting Backbone

> **Research focus:** Fabric capacities expose **no Azure Monitor metrics**, so near-real-time CU% / throttling alerting for the `bi-fabrics-audit-agent` must be **Fabric-native**. This file covers the streaming + alerting machinery: **Eventstream** (sources/transform/destinations/routing/schema/latency/REST/CI-CD) and **Activator / Data Activator / Reflex** (objects, rules, conditions, actions, latency, limits, SPN, MCP, Git/CI). It connects each capability to the agent and flags where the agent's own brain must still add reasoning.
>
> **Already covered elsewhere (NOT re-covered here):** Real-Time Hub "Capacity Overview Events" as a *source schema* (file 10/14/15 covered the event itself); Eventhouse vs Lakehouse vs Warehouse storage choice (file 15); Workspace Monitoring (file 13). This file treats the Capacity Overview Events stream **only** as the input that feeds the Eventstream→Activator alerting pipeline.
>
> **Date of research:** 2026-06-23. All sources `learn.microsoft.com` unless noted. Doc `ms.date` values range 2025-09 to 2026-06.

---

## ★ HEADLINE ANSWER (the central question)

**YES — Activator can fire a CU% / throttling alert *natively*, with zero external compute.** Microsoft publishes an end-to-end tutorial that does exactly this: it adds **Capacity Overview Events** (`Microsoft.Fabric.Capacity.Summary`, emitted every 30 s) as the source, sets an Activator rule with condition **`backgroundRejectionThresholdPercentage` Increases to or above `80`** grouped by `capacityId`, and sends an email when breached. The same rule type can also **Run function (UDF)**, run a pipeline/notebook, or call a Power Automate flow for automitigation.

**Key nuance for the agent:** the *most direct, fully-automatable* alert path (UI "Set alert" + Reflex REST API with **service-principal support**) runs on the **Eventstream / Real-Time Hub** path. The newer **Activator Remote MCP Server (Preview)** — which would let the agent's LLM create rules in natural language — currently only targets **KQL databases (Eventhouse/ADX)**, *not* the live capacity stream, and supports **no aggregation/summarization and only email/Teams actions**. So the agent's native alerting layer = **Eventstream(Capacity Overview Events) → Activator rule (threshold) → email/Teams/UDF**, provisioned via the **Reflex REST API under a service principal**. See "Recommended architecture" at the end.

---

## ITEM 1 — Eventstream: Overview, Sources, Transform, Destinations, Routing, Schema, Latency, Limits

**TITLE:** Microsoft Fabric Eventstreams Overview
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview
**ms.date:** 2026-04-29

**Summary.** Eventstream is the no-code item under Real-Time Intelligence that **captures, transforms, and routes real-time events** to destinations. Model: *create eventstream → add source(s) → optional transformations → route to destination(s)*. Backed by an auto-provisioned Azure Event Hubs namespace; offers an **Apache Kafka endpoint** (Kafka protocol in/out). Two capability tiers: **Standard** vs **Enhanced capabilities** (toggled at creation; Enhanced unlocks far more sources, all-destination transforms, Activator/custom-endpoint destinations via a derived stream bridge).

**Exact identifiers — Sources (Enhanced tab, partial list relevant to agent):**
- `Azure Event Hubs`, `Azure IoT Hub`, `Azure Event Grid`, `Azure Service Bus (preview)`, `Azure Data Explorer (preview)`
- **`Fabric capacity overview events (preview)`** ← THE source for capacity health alerting. "provide summary-level information about your capacity. You can use these events to create alerts related to your capacity health via Fabric Activator. You can also store these events in an eventhouse for granular or historical analysis."
- `Fabric workspace item events`, `Fabric OneLake events`, `Fabric job events` (semantic-model refresh, pipeline run, notebook run job events — useful for the agent's *operational* signals)
- `Custom endpoint` / `Custom app`, `Sample data` (Bicycles, Yellow Taxi, Stock Market, Buses, S&P 500, Semantic Model Logs), `Real-time weather`, many CDC connectors (Azure SQL, PostgreSQL, MySQL, Cosmos DB, Oracle, MongoDB, SQL Server, Mirrored DB change feed), `Google Cloud Pub/Sub`, `Amazon Kinesis`, `Amazon MSK`, `Confluent Cloud`, `Apache Kafka (preview)`, `MQTT (preview)`, `HTTP (preview)`, `Azure Blob Storage events`, `Cribl`, `Solace PubSub+`.
- **Standard tier sources are limited to:** `Azure Event Hubs`, `Azure IoT Hub`, `Sample data`, `Custom app`.

**Exact identifiers — Transformations (event processor editor, drag-and-drop, no-code):**
`Filter`, `Manage fields`, `Aggregate` (sum/min/max/avg per event over a period), `Group by` (aggregations over a time window, richer windowing), `Union`, `Expand` (row per array value), `Join`. **Plus** a **SQL operator (preview)** for code-first stream processing (windowing/joins/aggregations via SQL expressions).
- Enhanced capabilities: transforms supported for **all** destinations (derived stream bridges to custom endpoint / Activator). Without Enhanced: transforms only for Lakehouse + Eventhouse ("event processing before ingestion").

**Exact identifiers — Destinations (Enhanced tab):**
- **`Activator`** ← "directly connect your real-time event data to Fabric Activator … When the data reaches certain thresholds or matches other patterns, Activator automatically takes appropriate action, such as alerting users or starting Power Automate workflows."
- `Eventhouse` (two modes: **Direct ingestion** / **Event processing before ingestion**), `Lakehouse` (Delta format), `Derived stream`, `Custom endpoint`, `Spark Notebook (Preview)`.
- Multiple destinations can attach simultaneously without interference (so one stream can feed Activator **and** Eventhouse for history at the same time).

**Schema management:** `Schema Registry (preview)`, `Multiple schema inferencing (preview)`, `Confluent Schema Registry–based deserialization (preview)`. **DeltaFlow (Preview)** turns raw CDC into analytics-ready streams with auto schema registration.

**Latency / sizing / limits (verbatim):**
- Recommended **minimum capacity: F4** (≥4 CU).
- **Maximum message size: 1 MB.** **Max retention: 90 days.** **Event delivery guarantee: At least once.** (⇒ agent must dedupe — matches the Capacity Summary "best-effort delivery, duplicates possible" note.)
- Operational: derived streams support **pause/resume**; **Workspace Private Link (preview)** for private inbound.

**How it helps the agent.** This is the **ingestion + routing fabric**. The agent (or its IaC) stands up one Eventstream with **Fabric capacity overview events** as source, fans it out to (a) **Activator** for the live 30 s/80% alert and (b) **Eventhouse** for historical CU analysis the agent's brain reasons over later. The `Fabric job events` / `Fabric workspace item events` sources additionally give the agent real-time signals on refresh failures, pipeline runs, item changes.

**Where the agent's brain still adds value.** Eventstream only *moves and shapes* events; it has no notion of "is this throttle expected vs anomalous," no per-user attribution, no remediation reasoning. The agent correlates the raw threshold breach with workload breakdown, history, and blast-radius before recommending action.

---

## ITEM 2 — Eventstream REST API (CRUD + item definition + service principal + CI/CD)

**TITLE:** Eventstream REST API
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/eventstream-rest-api
**ms.date:** 2025-09-08 (updated 2026-05-22)

**Summary.** Full programmatic lifecycle. **CRUD** ops: Create / Delete / Get / List / Update Eventstream (`/en-us/rest/api/fabric/eventstream/items`). **Definition-based** ops: Create-with-definition, Get definition, Update definition.

**Exact identifiers:**
- Create endpoint (generic items): `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items` (also `.../eventstreams`).
- Item definition is a **graph** of four component arrays: **`sources`, `destinations`, `operators`, `streams`** + `compatibilityLevel` (e.g. `"1.1"`). Definition delivered as **Base64** in `definition.parts[]` with `path: "eventstream.json"` + `path: ".platform"`, `payloadType: "InlineBase64"`.
- **Source `type` enum includes `FabricCapacityUtilizationEvents`** ← the API identifier for the Capacity Overview Events source. (Other enums: `AzureEventHub`, `AzureIoTHub`, `CustomEndpoint`, `SampleData`, `FabricWorkspaceItemEvents`, `FabricJobEvents`, `FabricOneLakeEvents`, the CDC/cloud connectors, etc.)
- **Destination `type` enum: `"Activator"`, `"CustomEndpoint"`, `"Eventhouse"`, `"Lakehouse"`.**
- **Operator `type` enum:** `"Filter"`, `"Join"`, `"ManageFields"`, `"Aggregate"`, `"GroupBy"`, `"Union"`, `"Expand"`.
- **Stream `type`:** `"DefaultStream"`, `"DerivedStream"`.
- **Authentication: service principal supported** — "If your application needs to access Fabric APIs using a **service principal**, you can use the MSAL.NET library to acquire an access token." (Entra token in `Authorization` header.) Caveat: if a source uses a cloud connection, the SPN must have permission to that connection.
- API templates on GitHub: `https://github.com/microsoft/fabric-event-streams/blob/main/API%20Templates/eventstream-definition.json`.

**How it helps the agent.** The agent's deployment layer can **provision the entire capacity-alerting Eventstream non-interactively under its service principal** — create the eventstream, wire the `FabricCapacityUtilizationEvents` source, attach the `Activator` destination and an `Eventhouse` destination, all in one Base64 definition POST. Fully repeatable / IaC-friendly.

**CI/CD caveat (cross-ref Item 8):** the **Fabric capacity overview events source does NOT support Git Integration or Deployment Pipelines** — see Item 8.

---

## ITEM 3 — Activator (Data Activator / Reflex): What it is, object model, rules, actions

**TITLE:** What is Fabric Activator?
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-introduction
**ms.date:** 2026-04-17

**Summary.** Activator (a.k.a. **Reflex**) is a **no-code event-detection + rules engine** — "an intelligent observer" that consumes high-velocity streams, evaluates rule conditions in near real-time, and initiates downstream actions on state changes. **Subsecond latency for stateless rules on streaming data.** GA since **November 2024**.

**Object model (exact terms):**
- **Event sources:** Eventstreams (multiple), **Fabric events**, **Azure events**, **Business Events**, **Fabric Ontology business entities (preview)**, **Power BI report** (refresh-cadence observations + new-row-in-table-visual), **Fabric Real-Time Dashboard**, and **Fabric Data Warehouse SQL query results (preview)** on a schedule.
- **Events → Objects:** events grouped by a shared identifier (object key, e.g. `device_id`, `capacityId`). Rules evaluated **per object instance** ("population" = full set of instances).
- **Properties:** monitored fields of an object; reusable across rules (e.g. a rolling 1-hour average defined once).
- **Rules:** stateless (`value < 50`) or **stateful** (`BECOMES`, `DECREASES`, `INCREASES`, `EXIT RANGE`, heartbeat/absence). Stateful relies on **delta detection, temporal sequencing, state transitions** — rules **fire only on entry into a new state**, suppressing repeated firings (built-in noise suppression).
- **Lookback period:** window of history analyzed (e.g. 6 h lookback for a 3 h average). Tracks **distinct, active object IDs** within the lookback.

**Actions (exact list):**
- **Fabric pipelines, notebooks, Spark jobs, dataflows, User Data Functions (UDFs), copy jobs (preview), publish business event (preview)** — i.e. "Run a Fabric item."
- **Power Automate flows** (external/custom).
- **Teams message** (individual, group chat, or channel) and **Email**.
- Fire-and-forget: "Activator sends information about what happened and continues monitoring without waiting for the action to complete."

**Cost model:** pay-as-you-go, capacity-bound; **you only incur cost when activators are actively running** (good for intermittent detection). Reflex CU shows up in the Capacity Summary breakdown under workload `Reflex` → "Activator."

**How it helps the agent.** This is the **native trigger engine**. Capacity stream → object keyed on `capacityId` → property = a throttling/CU metric → rule = threshold → action = email/Teams/**Run UDF** (autoremediation) or **Run notebook** (which can invoke the agent's own analysis). Crucially, an Activator action can **call back into the agent** (Run notebook/UDF), so the native fast-path and the agent's slow-path reasoning compose cleanly.

**Where the agent's brain still adds value.** Activator does threshold/state detection only — no root-cause, no "which workload/user caused it," no prioritization across many simultaneous breaches, no natural-language explanation. The agent consumes the activation (or the same Eventhouse data) and supplies diagnosis + recommended remediation.

---

## ITEM 4 — Creating Activator rules (UI flow, save/start, properties)

**TITLE:** Create Activator Rules
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-create-activators
**ms.date:** 2026-04-17

**Summary / exact flow.** Create > Activator (or author **in-context inside Eventstream**). Pick the property/eventstream to monitor (**Monitor** section auto-fills). Choose condition type: *On each event* / *On each event when a value is met* / *On each event grouped by a field* (e.g. "on each PackageId event when Temperature > 30"). Choose **Action**: **Email / Teams / Fabric item / Custom action (Power Automate)**. Use `@property` tagging for **Context** in messages; `{columnName}` dynamic values. **Send me a test alert** validates against a past true event. Rules are created **Stopped**; **Save and start** activates; **Update** pushes edits to a running rule. Delete can take up to **5 minutes** to fully stop.

**How it helps the agent.** Documents the exact authoring contract the agent must replicate via API: Monitor→Condition→Occurrence→Action→Save location→Start. The agent can also **author rules in-context from the Eventstream**, matching Item 7's embedded Rules pane.

---

## ITEM 5 — Detection conditions / operators (the exact threshold vocabulary)

**TITLE:** Detection settings in Activator
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-detection-conditions
**ms.date:** 2026-05-04

**Exact condition categories (drop-down):**
- **Numeric change:** `Increases above`, `Decreases below` (directional vs threshold). ← *the CU%/throttle "rises above 80" alert.*
- **Numeric state:** `Is greater than`, `Is less than`, `Is between` (fires every event the state is true).
- **Text change:** `Changes to`, `Changes from`. **Text state:** `Contains`, `Begins with`, `Ends with`.
- **Logical change:** `Becomes true`, `Becomes false`. **Logical state:** `Is equal to`, `Is not equal to`.
- **Common change:** `Changes` (any type, no target).
- **Heartbeat:** `No presence of data` (no events within a time → useful to detect a *paused/dead* capacity stream), `Object first appearance`.

**Summarization** (rolling aggregation before the condition): Operation = `Average | Minimum | Maximum | Sum | Total`; **Window size** and **Step size** each **10 seconds to 24 hours**.

**Occurrence:** `Every time the condition is met` | `When it has been true for n times` | `When it has been true for <duration>` (sustained).

**Property filter:** up to **3 filters**, combined with **AND**; Attribute + Operation + Value.

**Advanced:** `Wait time for late-arriving events` (default 2 min) — see Item 6.

**How it helps the agent.** Exact knobs the agent maps user intent onto. "Alert me when smoothed CU exceeds 80% for 5 minutes" = Numeric state `Is greater than` 80 + Occurrence `When it has been true for 5 minutes`, or `Increases above` for a one-shot. `No presence of data` detects a stalled capacity feed. Distinction *change vs state*: use `Increases above`/`Decreases below` for one-time transition alerts; `Is above`/`Is below` for repeated per-event alerts.

---

## ITEM 6 — Latency & accuracy (how fast the native alert really fires)

**TITLE:** Latency and accuracy considerations in Activator rules
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-latency
**ms.date:** 2026-04-30

**Exact figures.**
- "Results are near instantaneous, but … in some cases, it can be **up to 10 minutes**."
- Three latency drivers: **late-arrival tolerance**, **backend processing latency (up to ~1 minute)**, **aggregation latency** (rule fires only when the aggregation window completes — a 4 h average ingested at 12 PM triggers at 4 PM).
- **Late arrival tolerance default = 2 minutes** (configurable; rules with a wait time have a minimum latency equal to that duration).
- **Stateless streaming rules respond within milliseconds.**
- For **query data sources (Power BI, KQL Querysets, Real-Time Dashboards)**, **query frequency** dominates latency. **Power BI default query = once per hour** ⇒ up to 1 h delay.

**How it helps the agent.** Sets realistic SLA expectations and a design rule: for the **fastest** capacity alert, drive Activator **directly from the streaming Eventstream (subsecond–seconds)**, NOT from a scheduled KQL queryset (≥1–5 min) or Power BI (≤1 h). Keep summarization windows small; leave late-arrival at 2 min unless the agent needs completeness over speed.

**Where the agent's brain adds value.** Latency tradeoffs (wait for complete data vs alert sooner) are policy decisions the agent should choose per scenario rather than leave at defaults.

---

## ITEM 7 — Eventstream → Activator destination (the live streaming wire-up)

**TITLE:** Add a Fabric Activator Destination to an Eventstream
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-destination-activator
**ms.date:** 2026-03-22

**Summary / exact flow.** In Eventstream **Edit mode** → **Add destination > Activator** → name it, pick **Workspace**, pick existing **Activator** or **Create new** → **Save** → **Publish**. Requirement: **each event must be a JSON dictionary with one key as a unique object ID** (e.g. `{"PackageID":"PKG123","Temperature":25}` → object ID = `PackageID`; for capacity → `capacityId`). In **Live mode**, click the **Activator** icon → **Rules** pane → **Add rule** (Rule name + Condition like `airport_fee > 0` on a JSON key + Action: email or **trigger a Logic App**).

**Embedded Rules pane** gives consolidated, in-Eventstream management: view all rules, **start/stop toggle**, edit, delete, **Open in Activator**, add rule — no context switch.

**How it helps the agent.** This is the **primary native streaming alert path** for capacity: Eventstream(`FabricCapacityUtilizationEvents`) → Activator destination → threshold rule on `capacityId`. Subsecond/seconds latency. The agent can manage all capacity rules from one Rules pane, and the same Activator item can hold many capacity rules.

---

## ITEM 8 — Activator limitations, quotas, throttling-of-the-alerter, lifecycle/CI-CD

**TITLE:** Activator limitations
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-limitations
**ms.date:** 2026-04-17 (updated 2026-06-15)

**Critical limits (verbatim / exact numbers):**
- **GENERAL:** **"Creating alerts from the Fabric or Power BI Capacity Metrics app isn't supported."** ⇒ the agent **cannot** alert off the Capacity Metrics app; it **must** use the **Capacity Overview Events stream** (which *is* supported — see Items 1, 9, 11). Also unsupported: alerts on reports using Dynamic M parameters; alerts from a **SQL analytics endpoint**.
- **Throughput: up to 10,000 incoming events/second per rule** — exceeding it **stops the rule**. (Capacity stream is 1 event/30 s/capacity → trivially within budget.)
- **Action rate limits** (throttle/cancel if exceeded):
  - Email: **500/activator item/hour**, **30/rule/recipient/hour**.
  - Teams: **500/activator item/hour**, **30/rule/recipient/hour**, **100/recipient/hour**, **50/Teams tenant/second**.
  - Custom action (Power Automate): **10,000 flow executions/rule/hour**.
  - Fabric item: **50 activations/user/minute**.
- **Email recipients must be internal** (same/verified Entra domain; **no external or guest** addresses). **Teams:** only recently-active group chats and **shared** channels (no private channels).
- **Real-Time Dashboard tiles supported:** Time chart, Bar, Column, Area, Line, Stat, Multi stat, Pie — and **only** if data is non-static, KQL-based, single time range, predefined (not custom) time range, and **not** `make-series` time series.
- **Lifecycle management (Git / Deployment Pipelines) limitations** — Activator items **don't work with Fabric ALM** if they use: **Azure Blob Storage Events as data source, Power BI as data source, or User Data Functions as action**. Including such an item in a deployment pipeline / Git-integrated workspace **errors on deploy/commit**. (Support "planned for a future release.")

**Cross-ref (Item 1 of capacity source):** **Eventstream with the Fabric capacity overview events source does NOT support Git Integration or Deployment Pipelines either** — exporting/importing such an Eventstream to Git "may result in errors."

**How it helps the agent.** Defines the non-interactive boundaries. Action volume budgets are generous for capacity alerting. **CI/CD reality:** the capacity-alerting Eventstream + (UDF-action) Activator must be **provisioned via REST under the SPN, not via Git/Deployment Pipelines** — bake this into the agent's deploy story. Email-recipient domain restriction means the agent should route external/escalation notifications via **Teams or a Power Automate custom action** (e.g. ServiceNow), not raw email.

---

## ITEM 9 — THE TUTORIAL: native CU%/throttle alert, end-to-end (proof + exact recipe)

**TITLE:** Monitor Fabric Capacity Health in Real Time with Capacity Overview Events
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-hub/tutorial-monitor-capacity-threshold
**ms.date:** 2026-06-11

**Summary.** Step-by-step: build an automated workflow that **emails an alert when a capacity approaches throttling**. **Prereq: existing non-trial Fabric capacity with the Capacity Admin role.**

**Exact recipe (verbatim):**
1. Real-Time hub → **Fabric events** → **Capacity Overview Events** → **Set alert**.
2. **Details:** Rule name e.g. `Capacity Throttling Alert`.
3. **Monitor:** Source = Capacity Overview Events; in the Connect wizard pick **Event type = `Microsoft.Fabric.Capacity.Summary`**, **Event scope = By capacity**, select the capacity → Next → Save.
4. **Condition (exact):**
   - **Check:** `On each event`
   - **Grouping field:** `capacityId`
   - **When:** `backgroundRejectionThresholdPercentage`
   - **Condition:** `Increases to or above`
   - **Value:** `80`
   - **Occurrence:** `Every time the condition is met`
   - Note (verbatim): you may instead use **`interactiveDelayThresholdPercentage`** (interactive operations being delayed) or **`interactiveRejectionThresholdPercentage`** (interactive operations being rejected). `80` is illustrative — tune to policy.
5. **Action:** `Send email` → To = capacity admin/team; Subject `Fabric Capacity Throttling Alert`; Headline `Capacity threshold exceeded`; Notes `Your Fabric capacity has exceeded the configured rejection threshold: @backgroundRejectionThresholdPercentage%` (type `@…` so the variable populates).
   - **TIP (verbatim):** "you can trigger automitigation logic by selecting **Run function** as the action and pointing to a **user-defined function (UDF)** that implements your mitigation workflow."
6. **Save location:** workspace + Activator item name → **Create**.
7. **Result:** rule listens for `Microsoft.Fabric.Capacity.Summary` events from the capacity; when `backgroundRejectionThresholdPercentage` ≥ threshold, the Activator emails recipients.

**How it helps the agent.** This is the **canonical, copy-able blueprint** the agent automates. Confirms: (a) native alerting works with **no Azure Monitor**; (b) the **exact threshold fields** to monitor; (c) **`Run function (UDF)`** is the sanctioned hook for **autoremediation** (and the agent's UDF can call its own logic). The agent can offer "throttle alert" as a one-click provisioned artifact, parameterizing capacity, threshold field, value, and action.

**Where the agent's brain adds value.** The tutorial picks an arbitrary 80% on one field. The agent should choose *which* of the three rejection/delay fields matter, set thresholds per SKU and per workload mix, and on activation explain *why* (which workload pushed CU up, who ran it, what to do) — none of which Activator provides.

---

## ITEM 10 — Capacity Overview Events schema (exact fields the rule/agent reads)

**TITLE:** Explore Fabric capacity overview events in Fabric Real-Time hub
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-hub/explore-fabric-capacity-overview-events
**ms.date:** 2025-11-17 (updated 2026-04-30)

> Covered as a *source* in earlier files; included here ONLY for the field names the Activator rule/agent must reference. (Cadence 30 s already established elsewhere.)

**Event types:** `Microsoft.Fabric.Capacity.Summary` (every 30 s, smoothed CU), `Microsoft.Fabric.Capacity.State` (on state change only).

**Summary `data` fields the rule can monitor (exact names/types):**
- `capacityId` (string) — **the object/grouping key.** `capacityName`, `capacitySku` (e.g. `FT1`), `tenantId`, `capacityRegion`.
- `windowStartTime` / `windowEndTime` (UTC; always 30 s apart).
- `baseCapacityUnits` (int — CU/sec for SKU); `capacityUnitMs` (double — CU-ms used). **% util = `capacityUnitMs` / (`baseCapacityUnits` × 1000 × 30) × 100** (the agent computes this; there is **no** pre-baked `CapacityUtilizationPercentage` field — derive it).
- **`interactiveDelayThresholdPercentage`** (double; >100% ⇒ interactive delay begins; avg util of next 20 windows / 10 min).
- **`interactiveRejectionThresholdPercentage`** (double; >100% ⇒ interactive rejection; next 120 windows / 1 h).
- **`backgroundRejectionThresholdPercentage`** (double; >100% ⇒ background rejection; next 2,880 windows / 24 h). ← used by the tutorial.
- `overageTotalCapacityUnitMs`, `overageAddCapacityUnitMs`, `overageBurndownCapacityUnitMs` (carry-forward / burndown).
- `utilizationBackground`, `utilizationInteractive` (+ `…Preview` uncharged variants; background+interactive = `capacityUnitMs`).
- `capacityUnitUtilizationBreakdown` (object) — **per-workload CU split** with workload codes incl. `AS`=Semantic Model, `DMS`=Warehouse, `SparkCore`=Spark, `Kusto`=Eventhouse, `ES`=Eventstream, `Reflex`=Activator, `DI`=Data Integration, `AI`, `ML`, `SQLDb`, `lake`=OneLake, etc.

**State `data` fields:** `capacityId`, `capacityName`, `capacitySku`, `transitionTime`, **`capacityState`** (e.g. `Active`), **`stateChangeReason`** (e.g. `InteractiveDelay`), `activationId`. State only emits on change; blank table ≈ "NotOverloaded"; `ManuallyResumed` ≈ NotOverloaded.

**Best-effort delivery (verbatim):** Summary table is **best-effort** — duplicates possible (one event/30 s/capacity; dedupe with KQL `| summarize take_any(*) by windowStartTime, windowEndTime, capacityId`), missing events rare. Real-time only, **no historical backfill** → push to Eventhouse/OneLake early.

**How it helps the agent.** Exact field map. The "overloaded" signal = `Microsoft.Fabric.Capacity.State` (`capacityState`/`stateChangeReason`) → a `Changes to` text rule. The three `…ThresholdPercentage` fields are the throttle proxies. The `capacityUnitUtilizationBreakdown` is the per-workload data the agent's brain uses for root-cause. Best-effort delivery ⇒ the agent must dedupe (matches Eventstream "at least once").

---

## ITEM 11 — Activator Remote MCP Server (Preview) — the agent-native, NL rule-authoring path

**TITLE:** Get Started with the Activator Remote MCP Server (Preview)
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/mcp-remote-activator
**ms.date:** 2026-06-03 (updated 2026-06-22)

**Summary.** A **remote MCP server** that lets **AI assistants create monitoring rules, manage alerts, and trigger actions via natural language**. HTTP-based MCP endpoint, OAuth (Entra) auth (GitHub Copilot handles tokens automatically).

**Exact identifiers:**
- **Server URL:** `https://api.fabric.microsoft.com/v1/mcp/workspaces/<Workspace ID>/reflexes/<Artifact ID>` (type `"http"`, configured per Activator artifact in `mcp.json`).
- **Tools:** `create_rule` (watches a stream, triggers email/Teams when conditions met; supports **numeric, text, Boolean, heartbeat** functions + occurrence modifiers; **starts automatically**), `list_rules`, `start_rule`, `stop_rule`.
- **Rule structure:** **Stream** (`splitColumn` for per-entity grouping, `filters`), **Detection** (`condition`, `occurrence` — "every time," "stays for 5 minutes," "three times in 10 minutes"), **Action** (email or Teams).
- **Data source:** **KQL only** — either an **Azure Data Explorer / Kusto cluster** (`host name` + `database`) or a **Fabric eventhouse** (KQL database item ID + workspace ID).
- Underlying function names exposed in examples: `increasesAbove`, `decreasesBelow` + `andStays`, `changesTo` + `everyNthTime(3, 300)`, `noPresenceOfData(600)`.

**Limitations (verbatim, critical):**
- **KQL data sources only** — rules can only target **KQL databases (ADX) or Fabric eventhouses**. Other source types (incl. the live Eventstream / capacity stream) **not supported**.
- **Per-item** — one MCP URL per Activator artifact; multiple artifacts = multiple MCP entries.
- **Teams + email actions only** — **no webhooks, no Power Automate, no Run-Fabric-item** via MCP.
- **No multi-event triggers; no aggregation/summarization** (operates on individual events — no avg/sum/count over a window).
- **Tip:** also connect the **eventhouse MCP server** (`mcp-remote-eventhouse`) so the agent can inspect schema / validate KQL before creating rules.

**How it helps the agent.** This is the **most direct LLM-native control plane** — the agent can literally say "monitor table X, email me when CU > 90%" and a rule is created + started. **BUT** because it's **KQL-only + no aggregation + email/Teams-only**, it does **not** drive the *live capacity Eventstream* and can't do smoothed-window averages or UDF autoremediation. **Best fit:** the agent **lands Capacity Summary events in an Eventhouse** (Item 1 destination) and uses the MCP `create_rule` against that **KQL table** for conversational rule authoring — accepting ≥ the KQL execution-interval latency rather than subsecond.

**Where the agent's brain adds value.** The MCP gives NL→rule, but the agent must decide the KQL query, the right field/threshold, dedupe (`take_any`), and that aggregation isn't available (so pre-aggregate in the KQL query itself).

---

## ITEM 12 — Reflex (Activator) item definition — programmatic rule/action authoring

**TITLE:** Reflex definition (Fabric REST API item definition)
**URL:** https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/reflex-definition
**ms.date:** 2026-02-26

**Summary.** "**Reflex is also known as Activator.**" The full Activator config — sources, objects, attributes, **rules + actions** — is expressible as a **`ReflexEntities.json`** definition part (Base64), so the agent can create/replace rules **fully programmatically** via Create/Update Item Definition.

**Exact identifiers:**
- **Definition parts:** `ReflexEntities.json` (required) + `.platform` (optional). Format `json`, `payloadType: InlineBase64`. `Update Item Definition` honors `.platform` only with `updateMetadata=true`.
- **`ReflexEntities.json` = JSON array** of entities, each `{ uniqueIdentifier, payload, type }`; entities wire to each other by `uniqueIdentifier` (parents via `parentContainer.targetUniqueIdentifier` / `parentObject.targetUniqueIdentifier`).
- **Entity types:** `container-v1`, `simulatorSource-v1`, **`kqlSource-v1`**, **`realTimeHubSource-v1`**, **`eventstreamSource-v1`**, `fabricItemAction-v1`, `timeSeriesView-v1` (the `definition.type` of a timeSeriesView ∈ `Event | Object | Attribute | Rule`).
- **`eventstreamSource-v1`** payload: `metadata.eventstreamArtifactId` (GUID) → connects Reflex to a Fabric Eventstream. **`kqlSource-v1`** payload: `query.queryString`, `runSettings.executionIntervalInSeconds`, `eventhouseItem.targetUniqueIdentifier`. **`realTimeHubSource-v1`**: `connection.eventGroupType` (e.g. `Microsoft.Fabric.WorkspaceEvents`), `filterSettings.eventTypes[].name`.
- **Rule view** (`timeSeriesView-v1`, `definition.type:"Rule"`): `definition.instance` (JSON-encoded template string), `definition.settings.shouldRun` (**`true` = rule active/started**), `shouldApplyRuleOnUpdate`. **Rule templates:** `EventTrigger`, `AttributeTrigger`. Inside `instance`: steps like `NumberSummary` (op `Average`, `TimeDrivenWindowSpec` width/hop in ms), `NumberBecomes` (op `BecomesGreaterThan`, value), `OccurrenceOption` (`EachTime`), `TeamsMessage`/`EmailMessage`/`FabricItemInvocation` act steps.
- **Rule action types:** `TeamsMessage`, `EmailMessage`, **`FabricItemInvocation`** (executes a `fabricItemAction-v1` Fabric item — pipeline/notebook/etc., with params). (Email props: `sentTo`, `copyTo`, `bCCTo`, `subject`, `headline`, `optionalMessage`, `messageLocale`.)
- **Tip (verbatim):** easiest path is configure a Reflex in the UI, **Get Item Definition**, then tweak the returned template instance.

**How it helps the agent.** Unlike the MCP path, the Reflex definition supports **eventstreamSource-v1 (live capacity stream), summarization windows, AND FabricItemInvocation (UDF/notebook autoremediation)** — i.e. everything the tutorial does — **fully as code under the SPN**. This is the agent's **production rule-provisioning mechanism**; the recommended pattern is *UI-author once → Get Definition → parameterize → re-deploy via API*.

---

## ITEM 13 — Create Reflex REST API (SPN + scopes + LRO)

**TITLE:** Items - Create Reflex (REST API)
**URL:** https://learn.microsoft.com/en-us/rest/api/fabric/reflex/items/create-reflex
**ms.date:** updated 2026-06-17

**Exact identifiers:**
- **Endpoint:** `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/reflexes` (supports **LRO**; 201 created or 202 accepted).
- **Permissions:** caller needs **Contributor** workspace role. **Required delegated scopes: `Reflex.ReadWrite.All` or `Item.ReadWrite.All`.**
- **Microsoft Entra supported identities:** **User = Yes; Service principal & Managed identities = Yes.** ⇒ the agent can create/manage Reflex items headless.
- Body: `displayName` (req), `definition` (ReflexDefinition: `format:"json"`, `parts[]` with `ReflexEntities.json` + `.platform` Base64), `description`, `folderId`, `sensitivityLabelSettings`.
- Companion ops (same service): Get / List / Update / Delete Reflex, Get/Update Reflex Definition. Item type enum value = **`Reflex`**.

**How it helps the agent.** Confirms the **single most important automation fact: Activator (Reflex) items are fully creatable/updatable by a service principal or managed identity** with `Reflex.ReadWrite.All`. The agent provisions and maintains every capacity alert rule headless, end-to-end, no human in the loop.

---

## ITEM 14 — Set alert on a KQL Queryset / KQL query results (the Eventhouse-backed path)

**TITLE:** Create Activator Alerts from KQL Query Results
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-alert-queryset
**ms.date:** 2026-04-17

**Summary.** From a **KQL Queryset**: run a query → **Set Alert** (top ribbon). Two scenarios: (a) **scheduled query returns results** (alert per returned record — 5 records ⇒ 5 alerts; control by returning a single row via `count`/`make_list()`), (b) **query returns a visualization meeting a condition**. **Monitor frequency default = every 5 minutes** (configurable). Same Condition vocabulary as Item 5; Actions = Email / Teams / **Run Fabric activities** (pipeline, dataflow, Spark, notebook, **UDF**, copy job preview, publish business event preview) / **Custom action (Power Automate)**.

**Limitations (verbatim):** **Only KQL databases *within an Eventhouse* are supported** — **external ADX cluster querysets can't create alerts.** Timechart visualizations not supported here (use Real-Time Dashboard). **Cost warning:** a query every 1–5 min keeps **Eventhouse in an always-on state** (no idle/cost-down); without queries/ingestion >5 min Eventhouse can go idle.

**How it helps the agent.** The richer Eventhouse-backed alert path: the agent can write an arbitrary **KQL query** (e.g. derive `% util` from `capacityUnitMs`/budget, dedupe with `take_any`, aggregate per-workload) and alert on it with **full Fabric-item/UDF/Power Automate actions** — more expressive than the streaming threshold, at **≥5 min** latency and **always-on Eventhouse cost**. Aligns with the MCP path (Item 11) since both target Eventhouse KQL.

---

## ITEM 15 — Set alert on a Real-Time Dashboard tile

**TITLE:** Create Activator alerts from a Real-Time Dashboard
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-get-data-real-time-dashboard
**ms.date:** 2026-04-17

**Summary / exact flow.** Open dashboard → **Set alert** (ribbon, choose tile) or tile **More menu (…) → Set Alert**. **Monitor:** query run frequency, **default every 5 minutes**. **Condition:** `On each event when` (no dimensions) / `On each event grouped by` (dimensions) → When / Condition / Occurrence. **Actions:** Send email / Teams (individual, group chat, channel) / **Run Fabric activities** (pipeline, dataflow, Spark, notebook, UDF, copy job preview, publish business event preview) / **Custom action**. Save location = workspace + new/existing activator.

**Limitations:** supported tiles = Stat/KPI/Card + Line/Bar/Column/Area/Pie (not Tables, Maps, Funnel, Anomalies, Scatter, Markdown, Heatmap, Timechart). **Time-axis caveat:** Activator reads each time point **once**; later changes to that point are ignored — for current-period values use a **Card/KPI**, or filter the chart to end "one bin before now."

**How it helps the agent.** Lets the agent attach alerts to the *same KQL-dashboard tiles* it surfaces to humans (single source of truth for capacity health). Same 5-min cadence and Eventhouse-query basis as Item 14. The time-axis caveat matters for any "current CU%" tile — use Card/KPI.

---

## ITEM 16 — Power Automate custom actions (escalation / ticketing / webhooks)

**TITLE:** Use Custom Actions to Trigger Power Automate Flows
**URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows
**ms.date:** 2026-03-19

**Summary / exact flow.** A **custom action** = a reusable Activator action backed by a **Power Automate flow** (notifications beyond Teams/email, ticketing systems, line-of-business apps). In the rule **Definition > Action > Type > New custom action**: name it, define **input fields** (e.g. `Task name`, `assignee`), **Copy** the connection string, **Open flow builder**. The flow is **prepopulated with Activator as the triggering system**; paste the connection string into the Activator tile. Add a connector action (example uses **To Do "Add a to-do (V3)"**). Pass event data via **Dynamic content** (e.g. `Activation time`) and `triggerBody()?['customProperties/NAME_OF_INPUT_FIELD']`. Save; then select the custom action from **Action > Type** in any rule.

**How it helps the agent.** This is the escape hatch around the email "internal-only" restriction (Item 8) and the route to **ServiceNow / PagerDuty / Slack / webhooks**. The agent can pass `@backgroundRejectionThresholdPercentage`, `capacityName`, etc. as input fields into a flow that opens an incident — turning a native CU breach into a tracked ticket. (Note: the MCP path cannot author these — Item 11; use the UI or Reflex definition.)

---

## SYNTHESIS — How this becomes the agent's native near-real-time alerting layer

**Recommended architecture (native fast-path + agent slow-path):**

1. **Provision (SPN, headless, no Git/Deployment Pipeline — Item 8):**
   - Create one **Eventstream** with source `FabricCapacityUtilizationEvents` (Item 2 enum), fan-out to **(a) Activator destination** and **(b) Eventhouse destination** (Item 1) — via the Eventstream **Create-with-definition** REST API.
   - Create/maintain the **Reflex (Activator)** item and its rules via **Create/Update Reflex REST API** under the **service principal** (`Reflex.ReadWrite.All`, Contributor) using a parameterized **`ReflexEntities.json`** (Items 12–13). Author once in UI → Get Definition → templatize.

2. **Fast path (subsecond–seconds, native):** Streaming Activator rule on `capacityId` — `Increases above`/`Is greater than` on `backgroundRejectionThresholdPercentage` / `interactiveDelayThresholdPercentage` / `interactiveRejectionThresholdPercentage` (Items 5, 9, 10), or `Microsoft.Fabric.Capacity.State` `capacityState` change → **Action: Email/Teams** for humans **and `Run function` (UDF) / Run notebook** to invoke the agent's own logic / autoremediation (Items 3, 9, 16). Use `No presence of data` to detect a dead capacity feed.

3. **Rich path (≥5 min, Eventhouse-backed):** For derived `% util`, dedupe (`take_any`), per-workload root-cause, the agent writes **KQL** and alerts via **KQL Queryset Set alert** (Item 14) or **Real-Time Dashboard tile** (Item 15), or — conversationally — via the **Activator Remote MCP `create_rule`** against the Eventhouse KQL table (Item 11), accepting MCP's KQL-only / no-aggregation / email-Teams-only limits.

4. **Escalation:** **Power Automate custom action** for ServiceNow/PagerDuty/webhook (Item 16), bypassing the internal-email restriction.

**Where Fabric stops and the agent's brain begins.** Activator detects *that* a threshold/state was crossed (per object, with noise suppression) and fires a notification/job — but it does **no** root-cause, **no** per-user/per-workload attribution, **no** SKU-aware threshold selection, **no** prioritization across simultaneous breaches, **no** natural-language explanation or remediation reasoning, and **no** alerting from the Capacity Metrics app (banned — Item 8). The agent supplies all of that: it consumes the activation (or the Eventhouse history), reads `capacityUnitUtilizationBreakdown` + its own collectors, and produces diagnosis + recommended action. Activator is the **trigger and the muscle**; the agent is the **judgment**.

---

## FLAT URL LIST

1. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview
2. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/eventstream-rest-api
3. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-introduction
4. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-create-activators
5. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-detection-conditions
6. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-latency
7. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-destination-activator
8. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-limitations
9. https://learn.microsoft.com/en-us/fabric/real-time-hub/tutorial-monitor-capacity-threshold
10. https://learn.microsoft.com/en-us/fabric/real-time-hub/explore-fabric-capacity-overview-events
11. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/mcp-remote-activator
12. https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/reflex-definition
13. https://learn.microsoft.com/en-us/rest/api/fabric/reflex/items/create-reflex
14. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-alert-queryset
15. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-get-data-real-time-dashboard
16. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows
17. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-source-fabric-capacity-overview-events
18. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-manage-eventstream-destinations
19. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-manage-eventstream-sources
20. https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/process-events-using-event-processor-editor
21. https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/items
22. https://blog.fabric.microsoft.com/en-us/blog/fabric-capacity-events-in-real-time-hub-preview/ (Fabric blog — Capacity Events in Real-Time Hub Preview)

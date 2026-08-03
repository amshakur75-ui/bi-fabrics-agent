# Power BI — Non-Admin REST + Embedding + Gateways + Governance + Capacity SKUs

Research for **bi-fabrics-audit-agent** (READ-ONLY Power BI/Fabric capacity audit agent).
Scope of this file: **non-admin REST** (`/v1.0/myorg/...`, no `AsAdmin`), **Power BI Embedded** (A SKUs, embed tokens), **deployment pipelines REST**, **Dataflows Gen2 + Datamarts**, **on-premises + VNet data gateways**, **sensitivity labels / endorsement**, and the **Power BI/Fabric capacity SKU ladder + autoscale + pause/resume + PPU**.
Verified against learn.microsoft.com, June 2026.

> NOTE — explicitly out of scope here (covered in prior research): Power BI **Admin** REST (`...AsAdmin`, GetActivityEvents, scanner/metadata), **executeQueries**, **refreshables**, admin tenant settings, OAuth scopes catalog, XMLA endpoint, Capacity Metrics / Log Analytics / Activity Events. Cross-references to those appear only where a non-admin call hands off to them.

---

## How this maps to the audit agent (orientation)

The non-admin REST surface is what a **service principal added to individual workspaces** (or a master user) can read without tenant-admin rights. It is the fallback / complement to the admin scanner: when the agent isn't a Fabric admin it can still walk each workspace it has been granted, enumerate datasets/reports/dataflows, read **refresh history + schedules** (reliability + capacity-load signals), read **datasources + gateway bindings** (single points of failure, on-prem dependencies), read **parameters** (config drift), and inspect **deployment pipelines** (governance maturity). Embedding + capacity SKU material drives the **capacity-sizing verdict** (which SKU, whether free viewers are allowed, A vs F vs P, autoscale, pause/resume cost levers). Gateway material drives **resilience + networking** findings (HA clusters, ports, VNet vs on-prem).

---

# SECTION A — Non-admin REST: Datasets (semantic models)

Common base: `https://api.powerbi.com/v1.0/myorg/...`. "In Group" variants insert `/groups/{groupId}` and are the ones the agent should prefer (My-workspace variants omit `/groups/{groupId}` and only cover the caller's personal workspace). Most dataset reads accept **service principal profiles** (multi-tenant embedding).

### A1. Datasets - Get Refresh History In Group
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshes` (optional `?$top={n}`)
- **Scope**: `Dataset.ReadWrite.All` or `Dataset.Read.All`. Callable by service principal profile.
- **Limits**: OneDrive refresh history NOT returned. Caller must have **Write** permission on the dataset. Default returns last **60** entries if `$top` omitted.
- **Response** (`Refreshes.value[]` = `Refresh`): `refreshType` (enum: `Scheduled`, `OnDemand`, `ViaApi`, `ViaXmlaEndpoint`, `ViaEnhancedApi`, `OnDemandTraining`), `startTime`, `endTime`, `status` (`Unknown` | `Completed` | `Failed` | `Disabled`), `requestId`, `serviceExceptionJson` (failure error code JSON, e.g. `{"errorCode":"ModelRefreshFailed_CredentialsNotSpecified"}`), `refreshAttempts[]` (`attemptId`, `startTime`, `endTime`, `type` = `Data`|`Query`, `executionMetrics`, `serviceExceptionJson`).
- **How it helps**: Primary **reliability + capacity-load signal** per model without admin rights — failure rate, failure reasons (credentials/gateway/timeout via `serviceExceptionJson`), refresh duration (capacity pressure), and refresh trigger mix (`Scheduled` vs `ViaApi`/`ViaXmlaEndpoint` tells you how the model is driven). `refreshType=ViaXmlaEndpoint` flags write-back/automation maturity.
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group
- My-workspace variant: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history

### A2. Datasets - Get Refresh Schedule In Group
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/refreshSchedule`
- **Scope**: `Dataset.ReadWrite.All` or `Dataset.Read.All`. Callable by service principal profile.
- **Response** (`RefreshSchedule`): `days[]` (Mon–Sun enum), `times[]` (e.g. `["05:00","11:30"]`), `enabled` (bool), `localTimeZoneId` (TimeZoneInfo id, e.g. `"UTC"`), `notifyOption` (`NoNotification` | `MailOnFailure`; service principals only support `NoNotification`).
- **How it helps**: Detects scheduled-refresh **frequency and clustering** — many models refreshing at the same `times[]` on the same capacity = scheduling collision / capacity throttling risk. `enabled=false` + stale data = abandoned model. Combined with A1, cross-checks "scheduled 8×/day but only 1 succeeds." (Enhanced/scheduled-refresh write is a separate `Update Refresh Schedule` call — out of scope for read-only.)
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-schedule-in-group
- My-workspace variant: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-schedule

### A3. Datasets - Get Datasources In Group
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/datasources`
- **Scope**: `Dataset.ReadWrite.All` or `Dataset.Read.All`. Callable by service principal profile. Caller must have Write permission on the dataset.
- **Response** (`Datasources.value[]` = `Datasource`): `datasourceType` (e.g. `Sql`, `AnalysisServices`, `AzureBlobs`, `Oracle`, `SAPHana`, `SharePointList`, `OData`, `Extension`, `File`, `Salesforce`, `Exchange`), `connectionDetails` (`server`, `database`, `url`, `path`, `account`, `domain`, `emailAddress`, `kind`, `loginServer`, `classInfo`), `datasourceId` (empty when not gateway-bound), `gatewayId` (empty when not gateway-bound; for clusters = primary/cluster gateway ID), plus deprecated `connectionString`/`name` (DirectQuery only).
- **How it helps**: Core **dependency-mapping + blast-radius** primitive — which models depend on which servers/databases, which depend on a gateway (`gatewayId` populated = on-prem / VNet dependency = single point of failure), and which `datasourceType` (e.g. on-prem `Sql`/`Oracle` vs cloud `AzureBlobs`). Detects shared datasources (many models → one server) and credential-failure root cause (combine with A1 `serviceExceptionJson`).
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-datasources-in-group
- My-workspace variant: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-datasources

### A4. Datasets - Get Parameters In Group / Update Parameters In Group
- **Get URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets/{datasetId}/parameters` — **Scope** `Dataset.ReadWrite.All` or `Dataset.Read.All`. Returns `MashupParameter` list: `name`, `type`, `currentValue`, `isRequired`, `suggestedValues`.
- **Update URL** (write — not used by read-only agent, listed for completeness): `POST .../datasets/{datasetId}/Default.UpdateParameters` — **Scope** `Dataset.ReadWrite.All`; caller must be **dataset owner**; body `{ "updateDetails": [ { "name": "...", "newValue": "..." } ] }`. **Limits**: max 100 params/request; names case-sensitive; can't update `Any`/`Binary` types; XMLA-edited datasets unsupported; DirectQuery only with enhanced dataset metadata; AAS live connections unsupported.
- **How it helps (read)**: Get Parameters surfaces **config drift / environment leakage** — e.g. a production model still pointing at a dev `DatabaseName`/`ServerName` parameter, or required parameters with empty values. Strictly read for the audit agent.
- **Get**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-parameters-in-group
- **Get (My workspace)**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-parameters
- **Update**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/update-parameters-in-group

### A5. Datasets - Get Datasets In Group (enumeration root)
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/datasets` — **Scope** `Dataset.Read.All` / `Dataset.ReadWrite.All`. Returns dataset list with `id`, `name`, `configuredBy`, `isRefreshable`, `isEffectiveIdentityRequired`, `isOnPremGatewayRequired`, `targetStorageMode`, `createdDate`. `isOnPremGatewayRequired=true` is a direct gateway-dependency flag; `targetStorageMode` (`Abf`/`PremiumFiles`) hints large-model. Use as the per-workspace fan-out point before A1–A4.
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets (group + My-workspace operations indexed here)

> Related non-admin dataset writes (NOT used by read-only agent, noted so the agent recognizes them in logs): `Take Over`, `Update Datasources`, `Refresh Dataset` (enhanced refresh), `Update Refresh Schedule`, `Get Refresh Execution Details`. Index: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets

---

# SECTION B — Non-admin REST: Reports, Imports, Push, Groups, Apps, Dataflows

### B1. Reports - Get Reports In Group
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/reports`
- **Scope**: `Report.ReadWrite.All` or `Report.Read.All`. Callable by service principal profile.
- **Response** (`Report`): `id`, `name` (app reports prefixed `[App]`), `webUrl`, `embedUrl`, `datasetId` (blank for paginated/RDL), `reportType` (`PowerBIReport` | `PaginatedReport`), `format` (`PBIR`/`PBIRLegacy` for PBI; `RDL` for paginated), `isOwnedByMe`, `appId`, `originalReportId`, `description`. (`users[]`/`subscriptions[]` are now empty — use admin APIs `Get Report Users As Admin` / `Get Report Subscriptions As Admin` instead.)
- **How it helps**: Inventories report → dataset linkage (`datasetId`) for **blast-radius** ("if this model breaks, these N reports go dark"), flags **paginated reports** (`PaginatedReport`/RDL — needs F-SKU/Premium feature + often a gateway), and detects **orphan reports** (datasetId present but dataset deleted). `ReportUserAccessRight` enum (`None`/`Read`/`ReadWrite`/`ReadReshare`/`ReadCopy`/`Owner`) defines the permission vocabulary for access findings.
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-reports-in-group
- My-workspace: https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-reports

### B2. Imports - Post Import In Group
- **URL** (write): `POST https://api.powerbi.com/v1.0/myorg/groups/{groupId}/imports?datasetDisplayName={name}` — **Scope** `Dataset.ReadWrite.All` / `Content.Create`. Imports `.pbix`, `.rdl`, `.json` (model.json / dataflow), `.xlsx`.
- **Key capacity fact**: A **service principal can publish content WITHOUT a Pro/PPU license** by using **Post Import In Group** (the only publish path that doesn't require a per-user Pro/PPU). Relevant to how the agent's own SP could operate, and to licensing-cost reasoning.
- **How it helps**: Not used for read-only auditing, but the agent should recognize import-driven deployments (vs pipeline-driven) as a **governance maturity** signal.
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/imports/post-import-in-group

### B3. Push Datasets - Datasets PostDataset / PostDatasetInGroup / PostRows
- **URLs**: `POST .../myorg/datasets` (My workspace) and `POST .../myorg/groups/{groupId}/datasets` (group). Rows: `PostRowsInGroup`. **Scope** `Dataset.ReadWrite.All`.
- **Limits / nature**: Supports **only push datasets** (streaming/real-time, defaultMode `Push`/`PushStreaming`). These have no scheduled refresh and no gateway; they ingest via API.
- **How it helps**: When auditing, a **push/streaming dataset** explains why a model has no refresh schedule/history (A1/A2 empty is expected, not a defect). Distinguishes streaming workloads (different capacity-consumption profile) from import/DirectQuery.
- **URL (PostDataset)**: https://learn.microsoft.com/en-us/rest/api/power-bi/push-datasets/datasets-post-dataset
- **URL (PostDatasetInGroup)**: https://learn.microsoft.com/en-us/rest/api/power-bi/push-datasets/datasets-post-dataset-in-group
- **Index**: https://learn.microsoft.com/en-us/rest/api/power-bi/push-datasets

### B4. Groups - Get Groups (workspace enumeration)
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/groups` (optional `?$filter=&$top=&$skip=`)
- **Scope**: `Workspace.Read.All` or `Workspace.ReadWrite.All`. Callable by service principal profile.
- **Response** (`Group`): `id`, `name`, `isReadOnly`, `isOnDedicatedCapacity` (true = on Premium/Fabric/Embedded capacity), `capacityId` (only when on dedicated capacity), `defaultDatasetStorageFormat` (`Small` | `Large` — large = large-model storage), `dataflowStorageId`, `logAnalyticsWorkspace` (AzureResource: `id`/`resourceGroup`/`resourceName`/`subscriptionId` — only on single-group fetch). `$filter` supports OData `contains(name,'...')`.
- **How it helps**: **Top-level fan-out** — every per-workspace audit starts here. `isOnDedicatedCapacity` + `capacityId` group workspaces by capacity (essential for the capacity-sizing verdict: "these 40 workspaces all sit on capacity X"). `defaultDatasetStorageFormat=Large` flags large-model usage (memory pressure). `dataflowStorageId` flags BYO dataflow storage (ADLS Gen2). `logAnalyticsWorkspace` reveals which workspaces have query-log telemetry wired up.
- **Note**: workspace permission changes lag; call `Refresh User Permissions` first if the SP was just added.
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/groups/get-groups
- **Index**: https://learn.microsoft.com/en-us/rest/api/power-bi/groups

### B5. Dataflows - Get Dataflows / Get Dataflow Data Sources / Get Upstream Dataflows
- **Get Dataflows URL**: `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/dataflows` — **Scope** `Dataflow.ReadWrite.All` or `Dataflow.Read.All`. Callable by service principal profile.
- **Response** (`Dataflow`): `objectId` (dataflow ID), `name`, `description`, `modelUrl` (URL to `model.json` definition, e.g. on `*.dfs.core.windows.net`), `configuredBy` (owner). `DataflowUserAccessRight` enum: `None`/`Read`/`ReadWrite`/`ReadReshare`/`Owner`.
- **Companion reads**: `Get Dataflow Data Sources` (`.../dataflows/{dataflowId}/datasources`) returns the dataflow's source connections; `Get Upstream Dataflows In Group` returns dataflow-to-dataflow chains (`Dataflow.Read.All`).
- **How it helps**: Maps the **ELT layer** — dataflow lineage (`Get Upstream Dataflows` = chained dataflows = refresh-order + blast-radius), dataflow datasources (on-prem/gateway dependency at the prep layer), and `configuredBy` for ownership/orphan analysis. Note these classic-Dataflow REST endpoints cover **Power BI Dataflows (Gen1)**; Gen2 is governed via Fabric items API (see Section E).
- **URL (Get Dataflows)**: https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-dataflows
- **URL (Get Dataflow Data Sources)**: https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-dataflow-data-sources
- **URL (Get Upstream Dataflows In Group)**: https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-upstream-dataflows-in-group
- **Index**: https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows

### B6. Apps - Get Apps / Get Reports/Dashboards in App
- **URL**: `GET https://api.powerbi.com/v1.0/myorg/apps` — **Scope** `App.Read.All`. Returns published apps (`id`, `name`, `lastUpdate`, `publishedBy`, `workspaceId`). Companion: `Get Reports`, `Get Dashboards` in app.
- **How it helps**: Apps are the consumption surface; auditing them shows which workspaces are **productized for end users** (governance maturity) vs raw workspaces. Free viewers consuming apps tie directly to the F64/P-SKU free-viewer rule (Section F).
- **Index**: https://learn.microsoft.com/en-us/rest/api/power-bi/apps

---

# SECTION C — Gateways (non-admin) + datasource binding

### C1. Gateways - Get Gateways / Get Datasources / Get Datasource
- **Get Datasources URL**: `GET https://api.powerbi.com/v1.0/myorg/gateways/{gatewayId}/datasources`
- **Permissions**: User must have **gateway admin** permissions. **Scope** `Dataset.ReadWrite.All` or `Dataset.Read.All`.
- **Limitation**: **VNet gateways are NOT supported** by this endpoint (on-prem standard/personal gateways only).
- **Response** (`GatewayDatasource`): `id`, `gatewayId` (for clusters = primary/cluster gateway ID), `datasourceType` (large enum: `Sql`, `AnalysisServices`, `Oracle`, `SAPHana`, `Teradata`, `PostgreSql`, `MySql`, `DB2`, `SharePoint`, `Web`, `OData`, `File`, `Folder`, `AzureDataLakeStorage`, `Extension`, etc.), `connectionDetails` (JSON string), `credentialType` (`Basic` | `Windows` | `Anonymous` | `OAuth2` | `Key` | `SAS` | `KeyPair`), `datasourceName`, `credentialDetails.useEndUserOAuth2Credentials` (SSO/DirectQuery flag).
- **Note on `{gatewayId}`**: For a **cluster**, gateway ID = the **primary (first)** gateway = effectively the cluster ID.
- **How it helps**: Enumerates **every datasource flowing through each on-prem gateway** — concentration risk ("one gateway serves 200 datasources"), `credentialType` audit (e.g. legacy `Basic`/`Windows` vs `OAuth2`/SSO), and SSO/DirectQuery exposure (`useEndUserOAuth2Credentials`). Combine with A3/B5 `gatewayId` to build the full **gateway → datasource → model/dataflow** dependency graph (single-point-of-failure detection).
- **URL (Get Datasources)**: https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/get-datasources
- **URL (Get Datasource, single)**: https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/get-datasource
- **Index**: https://learn.microsoft.com/en-us/rest/api/power-bi/gateways

### C2. Datasets / Reports - Bind To Gateway (write — recognized, not invoked)
- `POST .../groups/{groupId}/datasets/{datasetId}/Default.BindToGateway` and `.../reports/{reportId}/Default.BindToGateway`. Binds a dataset/paginated report to a gateway (optionally with specific `datasourceObjectIds`); if no ID supplied, binds to first matching datasource. **Scope** `Dataset.ReadWrite.All`.
- **How it helps**: Read-only agent doesn't call it, but understanding the bind model explains how `gatewayId` in A3/B1 gets populated and why a model may be "bound but credentials missing" (A1 failure cause).
- **URL (Datasets)**: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/bind-to-gateway-in-group
- **URL (Reports)**: https://learn.microsoft.com/en-us/rest/api/power-bi/reports/bind-to-gateway-in-group
- Datasets companion: `Get Gateway Datasources In Group` (`.../datasets/{datasetId}/Default.GetBoundGatewayDatasources`) — https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-gateway-datasources-in-group

---

# SECTION D — On-premises data gateway + VNet data gateway (install / ports / clusters / capacity)

### D1. What is an on-premises data gateway / Install
- On-prem gateway = software installed in the customer network giving cloud services line-of-sight to on-prem data. Modes: **standard** (enterprise, shareable, supports clustering) and **personal** (single-user, Power BI only, no clustering). Only **one standard gateway per machine**; each cluster member must be a separate machine.
- **How it helps**: Establishes the dependency the agent flags — any datasource with a populated `gatewayId` ties model refresh availability to this on-prem component.
- **URL (overview)**: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-onprem
- **URL (install)**: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-install

### D2. Gateway communication — ports, FQDNs, protocols
- **Outbound ports**: TCP **80, 443, 433, 5671, 5672, and 9350–9354**. **No inbound ports required.** Uses **Azure Relay** for cloud connectivity (region-pinned to tenant home region).
- **Key FQDNs (public cloud)**: `*.powerbi.com` (443), `*.analysis.windows.net` (443), `*.servicebus.windows.net` (5671–5672 AMQP, **443 + 9350–9354** Azure Relay over TCP), `*.download.microsoft.com` (443, installer/version), `*.login.windows.net`/`login.microsoftonline.com`/`*.microsoftonline-p.com`/`aadcdn.msauth.net` (443, Entra ID OAuth2), `*.dc.services.visualstudio.com` (443, telemetry), `gatewayadminportal.azure.com` (443, mgmt), `*.msftncsi.com` (80, connectivity test).
- **Fabric-workload ports** (when a gateway query touches OneLake/staging): `*.dfs.fabric.microsoft.com` (443), `*.datawarehouse.fabric.microsoft.com` (1433 TDS — replaces `*.datawarehouse.pbidedicated.windows.net`), `*.frontend.clouddatahub.net` (443, pipelines), `*.core.windows.net` (443).
- **Protocol**: defaults to **HTTPS** since June 2019 installs (force via gateway app → Network → HTTPS mode); **TLS 1.3** default to Power BI service. **Service tags**: `PowerBI`, `ServiceBus`, `AzureActiveDirectory`, `AzureCloud` (no service tag exists for Azure Relay itself).
- **GCC/GCC High/DoD/China** have distinct FQDN sets (documented in the page).
- **How it helps**: Networking/firewall findings — the agent can correlate gateway refresh failures with likely blocked ports/endpoints, and flag gateways not on HTTPS/TLS 1.3. Informs "is this gateway correctly reachable" diagnostics.
- **URL**: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-communication

### D3. Gateway high-availability clusters + load balancing
- **Max 10 gateway members per cluster** (clusters already >10 keep running but can't add more until one is removed). All members **must run the same gateway version**.
- Cloud service always routes to the **primary** member; on failure it routes to the next available member. Optional **load balancing**: "Distribute requests across all active gateways in this cluster" (random by default; round-robin when distribution disabled). **CPU/Memory throttling** via `Microsoft.PowerBI.DataMovement.Pipeline.GatewayCore.dll.config` keys: `CPUUtilizationPercentageThreshold`, `MemoryUtilizationPercentageThreshold` (0–100; 0=disabled), `ResourceUtilizationAggregationTimeInMinutes` (default 5). Manageable via PowerShell `Set-DataGatewayCluster`.
- **How it helps**: **Resilience finding** — a single-member gateway cluster = single point of failure for every dependent model (combine with C1 datasource count). Version-mismatch and offline-member states are concrete risk flags.
- **URL**: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-high-availability-clusters

### D4. Virtual network (VNet) data gateway — managed, networking, capacity cost
- **What**: Fully **Microsoft-managed** gateway injected into your Azure VNet/subnet; traffic stays on the Azure backbone (no public internet); supports **Private Link/private endpoints**. A cheaper, no-VM alternative to the on-prem gateway for Azure-hosted sources.
- **Supported workloads**: Fabric **Dataflow Gen2**, Fabric data pipelines, Fabric Copy Job, Fabric Mirroring, **Power BI semantic models**, **Power BI paginated reports**. **NOT supported**: Power BI **dataflows (Gen1)** and **datamarts**.
- **Licensing/SKU**: requires **P, F, or A4-or-higher (A4, A5, A6, A7)** SKU (any Fabric F SKU works; **F8+** recommended). Not in GCC L2 (GCC L4/L5 + air-gapped supported).
- **Capacity cost**: **fixed 4 CU per gateway member** consumed from the capacity. Limit **1,000 datasources per VNet gateway** per user. OAuth2 token auto-refresh NOT supported (tokens expire ~1h → `InvalidConnectionCredentials`/`AccessUnauthorized` on long dataflow refreshes).
- **How it helps**: For capacity sizing, VNet gateways are a **standing CU draw** (4 CU each) the agent must subtract from headroom. For networking/security posture, VNet+Private Link is the "good" pattern vs public-endpoint on-prem; the SKU floor (A4/F8+) gates whether the customer can even adopt it. The Gen1-dataflow/datamart exclusion is a migration-blocker the agent should call out.
- **URL (overview)**: https://learn.microsoft.com/en-us/data-integration/vnet/overview
- **URL (architecture)**: https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-architecture
- **URL (capacity consumption / business model)**: https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-business-model
- **URL (use in Power BI / supported sources)**: https://learn.microsoft.com/en-us/data-integration/vnet/use-data-gateways-sources-power-bi
- **URL (implementation planning: data gateways)**: https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-data-gateways

---

# SECTION E — Deployment pipelines (REST) + Dataflows Gen2 / Datamarts

### E1. Pipelines - Get Pipelines / Get Pipeline / Get Pipeline Stages / Get Pipeline Operations
- **Get Pipelines**: `GET https://api.powerbi.com/v1.0/myorg/pipelines` — **Scope** `Pipeline.Read.All` or `Pipeline.ReadWrite.All`. Returns pipelines the user can access.
- **Get Pipeline Stages**: `GET https://api.powerbi.com/v1.0/myorg/pipelines/{pipelineId}/stages` — **Scope** `Pipeline.ReadWrite.All` or `Pipeline.Read.All`. Response (`PipelineStage`): `order` (0=Dev, 1=Test, 2=Prod), `workspaceId` (only if a workspace is assigned), `workspaceName` (only if assigned AND caller has access). Optional `?$expand=datasets,reports,dashboards,dataflows` to list stage artifacts.
- **Get Pipeline Operations**: `GET .../pipelines/{pipelineId}/operations` — deployment history (each op: `id`, `type` Deploy/…, `status`, `lastUpdatedTime`, `executionStartTime`, `executionEndTime`, `performedBy`).
- **Writes (recognized only)**: `Deploy All` (`POST .../pipelines/{pipelineId}/deployAll`), Assign/Unassign Workspace.
- **How it helps**: Deployment pipelines are the strongest **governance-maturity** signal in the estate. The agent reads: do workspaces sit in a Dev→Test→Prod pipeline (3 stages with assigned workspaces) or are they ungoverned singletons? `Get Pipeline Operations` shows deployment cadence and who deploys (change-management hygiene). Note pipelines are a **Premium/Fabric-capacity feature** (F-SKU/P-SKU; not on F<64 in older tiering, A SKUs) — their presence corroborates Premium capacity usage.
- **URL (Get Pipelines)**: https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipelines
- **URL (Get Pipeline Stages)**: https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipeline-stages
- **URL (Get Pipeline Operations)**: https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipeline-operations
- **URL (Get Pipeline)**: https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipeline
- **Index**: https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines
- **Fabric item-level pipeline automation** (newer, Fabric REST): https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/pipeline-automation
- **Admin variant** (out of scope here, cross-ref): https://learn.microsoft.com/en-us/rest/api/power-bi/admin/pipelines-get-pipelines-as-admin

### E2. Dataflow Gen2 (Fabric) — refresh, capacity consumption, CI/CD
- **Capacity requirement**: Dataflow Gen2 requires a **Fabric capacity** (or Fabric trial, or Power BI Premium capacity).
- **Refresh limits**: **300 refreshes / 24h rolling window** for CI/CD Dataflows Gen2; **150 / 24h** for non-CI/CD Gen2.
- **CU consumption** on refresh/publish: **Standard Compute** (by query-evaluation time), **High Scale Dataflow Compute** (when staging enabled), **Fast Copy** (when fast-copy connectors used). The **Modern Evaluator** is on by default in new Gen2 (CI/CD) items.
- **CI/CD default**: as of **April 2026**, all new Dataflow Gen2 items are created with **CI/CD + Git integration** by default.
- **How it helps**: Gen2 dataflows are often the **biggest hidden CU consumers** on a Fabric capacity (staging + high-scale compute). The agent should treat Gen2 refresh frequency, staging, and fast-copy as capacity-sizing inputs, and flag non-CI/CD Gen2 (lower refresh ceiling + weaker governance). Gen2 needs F/Premium capacity — its presence rules out pure-A-SKU scenarios.
- **URL (refresh)**: https://learn.microsoft.com/en-us/fabric/data-factory/dataflow-gen2-refresh
- **URL (Gen1 vs Gen2)**: https://learn.microsoft.com/en-us/fabric/data-factory/dataflows-gen2-overview
- **URL (pricing/CU)**: https://learn.microsoft.com/en-us/fabric/data-factory/pricing-dataflows-gen2
- **URL (CI/CD + Git)**: https://learn.microsoft.com/en-us/fabric/data-factory/dataflow-gen2-cicd-and-git-integration
- **URL (incremental refresh)**: https://learn.microsoft.com/en-us/fabric/data-factory/dataflow-gen2-incremental-refresh

### E3. Datamarts
- Datamarts are a Premium/PPU/F-capacity feature (managed Azure SQL DB + auto-generated semantic model). **Not supported by VNet data gateway** (see D4). Governed primarily via the Fabric items API / admin scanner rather than a dedicated non-admin datamart REST surface.
- **How it helps**: Presence of datamarts = Premium/PPU/F-capacity dependency and an additional SQL-endpoint + auto-model to inventory; their VNet-gateway exclusion is a networking/migration constraint to flag.
- **URL (data gateway support note)**: https://learn.microsoft.com/en-us/data-integration/vnet/overview (limitations section)

---

# SECTION F — Power BI Embedded + capacity SKU ladder + autoscale / pause-resume / PPU

### F1. Embedded analytics overview — "embed for your customers" vs "embed for your organization"
- **Embed for your customers** = **app owns data**: external users, no Power BI license needed per viewer, **non-interactive auth** via **service principal or master user**, requires an **embed token** (generate-token API). R/Python visuals unsupported. Used by ISVs.
- **Embed for your organization** = **user owns data**: internal users, each needs a Power BI license, **interactive auth** against Microsoft Entra ID (uses the user's own AAD token, not a generate-token embed token). R/Python visuals supported.
- **Capacity**: production embedding requires an **A, EM, P, or F** capacity. Free trial tokens (with a Pro license) are dev-only and show a "Free trial version" banner.
- **How it helps**: Lets the agent classify embedding workloads and their licensing model. App-owns-data (A-SKU) implies **no per-viewer Pro cost** but a standing capacity cost; user-owns-data on <F64 means **every viewer needs Pro/PPU** (a cost the agent should surface).
- **URL**: https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-analytics-power-bi

### F2. Embed Token - Generate Token (generate-token, V2)
- **URL**: `POST https://api.powerbi.com/v1.0/myorg/GenerateToken`
- **Scope** (all that apply): `Content.Create` (if `targetWorkspaces` specified), `Report.ReadWrite.All` or `Report.Read.All` (if a report specified; ReadWrite required when `allowEdit`), `Dataset.ReadWrite.All` or `Dataset.Read.All`. Service principal / SP-profile supported.
- **Relevance**: **Only for the "embed for your customers" scenario.** Body = `GenerateTokenRequestV2`: `reports[]` (`id`, `allowEdit`), `datasets[]` (`id`, `xmlaPermissions` = `Off`|`ReadOnly`), `targetWorkspaces[]` (`id`), `identities[]` (`EffectiveIdentity`: `username`, `roles[]` ≤50, `datasets[]`, `reports[]`, `customData`, `auditableContext` for RLS audit, `identityBlob`), `datasourceIdentities[]` (SSO), `lifetimeInMinutes` (shorten only, never extend).
- **Response** (`EmbedToken`): `token`, `tokenId` (correlates to audit logs), `expiration`.
- **Limits**: max **50 reports / 50 datasets / 50 target workspaces** per token; all must reside in **V2 workspaces**.
- **How it helps**: Read-only agent doesn't mint tokens, but understanding the request shape lets it interpret **RLS usage** (`identities`/`roles`), **multi-tenancy** (SP profiles), and **embed-driven capacity load**. Embedding consumes capacity CUs and is a sizing input. `tokenId` ↔ audit-log correlation is a forensics note.
- **URL**: https://learn.microsoft.com/en-us/rest/api/power-bi/embed-token/generate-token
- **Considerations guide**: https://learn.microsoft.com/en-us/power-bi/developer/embedded/generate-embed-token

### F3. Capacity & SKUs in embedded analytics — the SKU ladder (canonical table)
SKU → Capacity Units → Power BI SKU equivalence → v-cores:

| Fabric SKU | CU | Power BI SKU (EM/A / P) | v-cores |
|---|---|---|---|
| F2 | 2 | N/A | N/A |
| F4 | 4 | N/A | N/A |
| F8 | 8 | EM1 / A1 | 1 |
| F16 | 16 | EM2 / A2 | 2 |
| F32 | 32 | EM3 / A3 | 4 |
| **F64** | 64 | **P1 / A4** | 8 |
| F128 | 128 | P2 / A5 | 16 |
| F256 | 256 | P3 / A6 | 32 |
| F512 | 512 | P4 / A7 | 64 |
| F1024 | 1,024 | P5 / A8 | 128 |
| F2048 | 2,048 | N/A | N/A |

- **Offers**: **A SKUs** = Power BI Embedded (Azure, hourly, scale/pause/resume, app-owns-data). **EM/P SKUs** = Power BI Premium (Office offer, monthly/yearly). **F SKUs** = Microsoft Fabric (Azure).
- **F64 / P1 free-viewer rule** (the single most important sizing threshold): **only P SKUs and F64-or-higher allow FREE Power BI users to consume apps/shared content.** Below F64 or on any A SKU → **every viewer needs Pro or PPU** (except app-owns-data, where external viewers need no license).
- **Memory**: per-item memory cap (not cumulative) — e.g. **F64 caps a single semantic model at 25 GB** (see semantic-model SKU-limitation table).
- **GCC**: F (Azure Embedded) not in GCC; only EM/P in GCC; F supported in GCC High/DoD.
- **How it helps**: This is the agent's **capacity-verdict backbone** — translate the customer's SKU to v-cores/CU/memory, decide if free viewers are allowed (F64/P1 line), and recommend up/down moves. The A↔F↔P equivalence lets the agent normalize a mixed estate.
- **URL**: https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-capacity
- **F-SKU licenses**: https://learn.microsoft.com/en-us/fabric/enterprise/licenses

### F4. Scale your Fabric capacity (resize / pause / resume / Azure RBAC)
- **Resize**: Azure portal → Microsoft Fabric → capacity → Scale → Change size → Resize. Billed **pay-as-you-go hourly** at the new size; scaling **below reserved** instance doesn't change the bill. Scaling between ≤F256 and ≥F512 may be slower. Scaling <F64 → bigger is near-instant; license update can take up to a day (Free users may see a Pro-upgrade prompt during the window).
- **Pause/Resume**: F SKUs support **pause/resume** (P SKUs run 24/7). Paused = no compute charge (only OneLake storage billed).
- **Azure RBAC needed** (for the agent to even read/scale): `Microsoft.Fabric/capacities/read`, `/write`, `/suspend/action`, `/resume/action` (build a scoped custom role).
- **How it helps**: Pause/resume + resize are the **biggest cost levers** the agent recommends (e.g. "pause dev capacity off-hours," "downsize F128→F64 given headroom"). The RBAC actions tell the agent (or its SP) exactly what permissions a read-only-vs-actionable posture requires (`read` only for audit; `suspend`/`resume`/`write` only if it ever acts).
- **URL**: https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity
- **Buy subscription / Azure SKUs**: https://learn.microsoft.com/en-us/fabric/enterprise/buy-subscription

### F5. Autoscale + Premium Per User (PPU)
- **Power BI Premium (P SKU) autoscale**: when combined (interactive+background) utilization exceeds the capacity's v-quota, the capacity **autoscales by an extra v-core for the next 24 hours** (Azure-billed). Fabric F SKUs instead use **pay-as-you-go + smoothing/bursting + throttling** and manual/scheduled scaling rather than classic v-core autoscale.
- **PPU**: per-user license giving an individual most Premium features without dedicated capacity; on a capacity **below F64**, the estate behaves like Embedded/PPU (each creator/viewer needs Pro or PPU). PPU content lives in PPU workspaces (a distinct capacity type).
- **How it helps**: Autoscale flags **uncontrolled cost growth** (P-SKU autoscale spend) the agent should surface. PPU vs capacity is a **licensing-strategy fork** — for small estates PPU may beat F64; the agent uses the F64 free-viewer threshold + viewer count to recommend PPU-vs-capacity.
- **URL (Premium FAQ / PPU)**: https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-faq
- **URL (Premium what-is / subscriptions & licensing, P vs EM, semantic-model SKU limits)**: https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-what-is
- **URL (A-SKU scale)**: https://learn.microsoft.com/en-us/power-bi/developer/embedded/azure-pbie-scale-capacity
- **URL (A-SKU pause/start)**: https://learn.microsoft.com/en-us/power-bi/developer/embedded/azure-pbie-pause-start

---

# SECTION G — Governance: sensitivity labels, endorsement, information protection

### G1. Endorsement — Promoted / Certified
- Two endorsement levels: **Promoted** (any contributor highlights content as ready) and **Certified** (admin-gated; only authorized reviewers, configured in tenant settings). Applies to **semantic models, reports, apps, dataflows** (and Fabric items). Endorsement metadata (`endorsementDetails`: `endorsement` = `Promoted`|`Certified`, `certifiedBy`) is read via the **admin scanner / GetGroupsAsAdmin `$expand`** (cross-ref to prior admin research) — there is **no non-admin "Get Endorsement" REST**; non-admin sees it embedded in item metadata where exposed.
- **How it helps**: Endorsement coverage is a **trust/governance KPI** — % of widely-used models that are Certified vs uncertified-but-popular (risk). The agent pairs low endorsement + high usage as a finding.
- **URL**: https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-endorsement-overview

### G2. Sensitivity labels — InformationProtection setLabels / removeLabels (admin REST)
- Programmatic labeling is **admin-only**: `SetLabelsAsAdmin` and `RemoveLabelsAsAdmin` (Fabric administrator required). **Scope** `Tenant.ReadWrite.All`. **Rate limit: max 25 requests/hour, up to 2000 artifacts/request.** Set request: `Artifacts` (dashboards/reports/datasets/dataflows by `id`), `LabelId` (GUID), optional `AssignmentMethod` (`Standard`|`Privileged`), optional `DelegatedUser`. Admin/delegated user must have the label in their policy + usage rights.
- **Reading labels**: label state per artifact comes from the **admin scanner** (`sensitivityLabel.labelId` in WorkspaceInfo output) — cross-ref prior admin research. These set/remove APIs are **write** and thus NOT used by a read-only agent.
- **How it helps**: Read-only agent reports **labeling coverage gaps** (sensitive datasources w/o labels) from scanner data; the set/remove APIs are noted so the agent recognizes them and can recommend (not perform) bulk relabeling, respecting the 25/hr · 2000-artifact governor.
- **URL (how-to)**: https://learn.microsoft.com/en-us/fabric/governance/service-security-sensitivity-label-inheritance-set-remove-api
- **URL (setLabels API)**: https://learn.microsoft.com/en-us/rest/api/power-bi/admin/information-protection-set-labels-as-admin
- **URL (removeLabels API)**: https://learn.microsoft.com/en-us/rest/api/power-bi/admin/information-protection-remove-labels-as-admin

---

# FLAT URL LIST

https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-schedule-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-schedule
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-datasources-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-datasources
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-parameters-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-parameters
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/update-parameters-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets
https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-reports-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-reports
https://learn.microsoft.com/en-us/rest/api/power-bi/imports/post-import-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/push-datasets/datasets-post-dataset
https://learn.microsoft.com/en-us/rest/api/power-bi/push-datasets/datasets-post-dataset-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/push-datasets
https://learn.microsoft.com/en-us/rest/api/power-bi/groups/get-groups
https://learn.microsoft.com/en-us/rest/api/power-bi/groups
https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-dataflows
https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-dataflow-data-sources
https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows/get-upstream-dataflows-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/dataflows
https://learn.microsoft.com/en-us/rest/api/power-bi/apps
https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/get-datasources
https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/get-datasource
https://learn.microsoft.com/en-us/rest/api/power-bi/gateways
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/bind-to-gateway-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/reports/bind-to-gateway-in-group
https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-gateway-datasources-in-group
https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-onprem
https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-install
https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-communication
https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-high-availability-clusters
https://learn.microsoft.com/en-us/data-integration/vnet/overview
https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-architecture
https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-business-model
https://learn.microsoft.com/en-us/data-integration/vnet/use-data-gateways-sources-power-bi
https://learn.microsoft.com/en-us/power-bi/guidance/powerbi-implementation-planning-data-gateways
https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipelines
https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipeline-stages
https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipeline-operations
https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/get-pipeline
https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines
https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/pipeline-automation
https://learn.microsoft.com/en-us/rest/api/power-bi/admin/pipelines-get-pipelines-as-admin
https://learn.microsoft.com/en-us/fabric/data-factory/dataflow-gen2-refresh
https://learn.microsoft.com/en-us/fabric/data-factory/dataflows-gen2-overview
https://learn.microsoft.com/en-us/fabric/data-factory/pricing-dataflows-gen2
https://learn.microsoft.com/en-us/fabric/data-factory/dataflow-gen2-cicd-and-git-integration
https://learn.microsoft.com/en-us/fabric/data-factory/dataflow-gen2-incremental-refresh
https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-analytics-power-bi
https://learn.microsoft.com/en-us/rest/api/power-bi/embed-token/generate-token
https://learn.microsoft.com/en-us/power-bi/developer/embedded/generate-embed-token
https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-capacity
https://learn.microsoft.com/en-us/fabric/enterprise/licenses
https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity
https://learn.microsoft.com/en-us/fabric/enterprise/buy-subscription
https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-faq
https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-what-is
https://learn.microsoft.com/en-us/power-bi/developer/embedded/azure-pbie-scale-capacity
https://learn.microsoft.com/en-us/power-bi/developer/embedded/azure-pbie-pause-start
https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-endorsement-overview
https://learn.microsoft.com/en-us/fabric/governance/service-security-sensitivity-label-inheritance-set-remove-api
https://learn.microsoft.com/en-us/rest/api/power-bi/admin/information-protection-set-labels-as-admin
https://learn.microsoft.com/en-us/rest/api/power-bi/admin/information-protection-remove-labels-as-admin

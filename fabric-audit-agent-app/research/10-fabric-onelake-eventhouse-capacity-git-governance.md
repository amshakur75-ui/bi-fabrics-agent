# Fabric Platform — Governance / Ops / Identity Research

**Project:** bi-fabrics-audit-agent (READ-ONLY Microsoft Fabric/PBI capacity audit agent)
**Doc scope (this file):** OneLake security/RBAC + catalog + shortcuts (governance angle); Fabric capacity management via Azure ARM (create/scale/suspend/resume/delete, SKU ladder, reservations, autoscale); Fabric Git integration + deployment pipelines (CI/CD); full Fabric admin/tenant-settings + governance (domains, Purview hub, sensitivity labels, monitoring hub, Admin REST APIs — Tenant Settings, Git, Domains, External Data Shares); workspace identity / managed identity for Fabric items.
**Out of scope (covered by sibling docs):** Capacity Metrics app/throttling/surge/Workspace Monitoring; Fabric Core REST capacities/workspaces/items + SP scopes; Data Agents/MCP/XMLA; Eventhouse/Lakehouse/Warehouse storage-choice; OneLake↔Databricks access. Eventhouse/Lakehouse kept light here on purpose.
**Researched:** 2026-06-23. All facts cited from learn.microsoft.com (and Azure pricing). Identifiers/scopes are quoted verbatim from the docs.

> Auditing posture reminder: this agent is READ-ONLY. Treat every write/suspend/resume/delete operation below as **knowledge the agent uses to reason and recommend**, not actions it performs. Use the `*.Read.All` / `Tenant.Read.All` scopes for collection, and surface the write/control endpoints only as recommended human actions.

---

## 1. OneLake security — RBAC / data-access roles (governance angle)

**TITLE:** OneLake security access control model
**URL:** https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model

**Summary.** OneLake uses a deny-by-default, role-based access control (RBAC) model layered *under* Fabric workspace/item permissions. A OneLake security role has four components:
- **Type** — GRANT or DENY. **Only GRANT is supported** today.
- **Permission** — the action granted. OneLake security roles support exactly two: **`Read`** (≈ SQL `VIEW_DEFINITION` + `SELECT`) and **`ReadWrite`** (≈ `ALTER`, `DROP`, `UPDATE`, `INSERT`).
- **Scope** — OneLake objects: tables, folders, or schemas.
- **Members** — any Microsoft Entra identity (user, group, or nonuser/service identity). Granting to a group grants all members.

**Which Fabric items support OneLake data-access roles (exact list):**
| Fabric item | Supported permissions |
|---|---|
| Lakehouse | Read, ReadWrite |
| Azure Databricks Mirrored Catalog | Read |
| Mirrored Database | Read |

**Workspace roles are the FIRST security boundary** and override OneLake roles:
| OneLake action | Admin | Member | Contributor | Viewer |
|---|---|---|---|---|
| View files in OneLake | Always Yes | Always Yes | Always Yes | No by default (grant via OneLake security) |
| Write files in OneLake | Always Yes | Always Yes | Always Yes | No by default |
| Can edit OneLake security roles | Yes | Yes | No | No |

Because Admin/Member/Contributor implicitly hold Write, **they override any OneLake security Read restriction** — RLS/CLS only meaningfully restrict **Viewers**.

**Default roles** (auto-created per item, use "member virtualization" so membership = anyone with the matching permission):
| Item | Role | Permission | Folders | Members |
|---|---|---|---|---|
| Lakehouse | `DefaultReader` | Read | `Tables/` + `Files/` | all users with **ReadAll** |
| Lakehouse | `DefaultReadWriter` | Read | All | all users with **Write** |
| ADB Mirrored Catalog | `DefaultReader` | Read | `Tables/`+`Files/` | all users with **Read** |
| Mirrored Database | `DefaultReader` | Read | `Tables/`+`Files/` | all users with **ReadAll** |

**Item-level Fabric permissions vs OneLake visibility:** `Read` (no file access by default), `ReadAll` (file access via DefaultReader; SQL endpoint access depends on endpoint mode), `Write` (full incl. SQL endpoint). `Execute`, `Reshare`, `ViewOutput`, `ViewLogs` can't be granted standalone.

**RLS / CLS.** RLS = SQL predicate; rows shown where predicate is true; string compares are case-insensitive with collation `Latin1_General_100_CI_AS_KS_WS_SC_UTF8`. CLS hides columns (treated as no-permission). Engines that enforce RLS/CLS filtering: Lakehouse (GA), Spark notebooks (GA, requires env 3.5+/runtime 1.3), SQL analytics endpoint **in user's-identity mode** (GA), Direct Lake **on OneLake** (GA), Eventhouse (RLS only, preview), authorized 3rd-party engines (preview). **For non-engine/user access** to RLS/CLS data, the query is **blocked** unless the user may see all rows/cols.

**Role combination math (audit-relevant):** roles UNION (least-restrictive). Within a role: `R = Rols ∩ Rcls ∩ Rrls`. Across roles: `(R1ols∩R1cls∩R1rls) ∪ (R2...)`. CLS in **SQL endpoint** is special — it uses **deny / intersection** semantics across roles, not union. If two roles' rows/cols don't align, access is blocked. Shortcuts add "inferred roles" resolved on the target first.

**Limits:** 250 roles/item (request up to 1000 via support), 500 members/role, 500 permissions/role. **Latency:** role-definition changes ~5 min; group-membership changes ~1 hr (+1 hr more for some engines' caches). B2B guests need Entra "Guest users have the same access as members". Distribution lists don't resolve at the SQL endpoint. Does NOT work with Azure Data Share / Purview Data Share.

**How it helps the agent.** This is the core data-access governance surface. The agent can (a) flag items where **Viewers have broad DefaultReader** access that should be scoped; (b) detect RLS/CLS gaps for sensitive lakehouses; (c) warn that Admin/Member/Contributor bypass OneLake Read rules; (d) explain why a SQL-endpoint query leaks/blocks given the deny-CLS intersection rule; (e) account for the 5-min/1-hr propagation latency before declaring a misconfig.

**Related:** Get started with OneLake security — https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-onelake-security ; Data security overview — https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security ; Lakehouse sharing — https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sharing ; Secure/manage shortcuts — https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcut-security

---

## 2. OneLake shortcuts (governance angle) + Create Shortcut REST API

**TITLE:** OneLake Shortcuts — Create Shortcut (Core REST API) / Unify data sources with OneLake shortcuts
**URL:** https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut
**URL:** https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts

**Summary.** Shortcuts are pointers (no data copy) from a lakehouse/KQL DB/warehouse into internal OneLake or external stores. **Internal** = OneLake-to-OneLake (identity passthrough; honors target's OneLake security). **External** = ADLS Gen2, Amazon S3, S3-compatible, Google Cloud Storage, Dataverse, Azure Blob Storage, OneDrive/SharePoint (uses a fixed **`connectionId`** delegated credential). Internal + ADLS Gen2 support writes; S3/GCS/Dataverse are read-only.

**Create Shortcut endpoint:**
```
POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/shortcuts
   [?shortcutConflictPolicy=Abort|GenerateUniqueName|CreateOrOverwrite|OverwriteOnly]   (default Abort)
```
- **Required delegated scope:** `OneLake.ReadWrite.All`. **Service principal / managed identity:** Yes.
- **Body:** `name`, `path` (must include `Files` or `Tables`), `target` (`CreatableShortcutTarget` — exactly one of): `oneLake` (workspaceId/itemId/path), `adlsGen2` (location/subpath/connectionId), `amazonS3`, `azureBlobStorage`, `googleCloudStorage`, `s3Compatible` (adds `bucket`), `dataverse` (environmentDomain/tableName/deltaLakeFolder), `oneDriveSharePoint` (adds `updateFabricItemSensitivity`). `externalDataShare` target also exists.
- Response `target.type` enum values: `OneLake, AmazonS3, AdlsGen2, GoogleCloudStorage, S3Compatible, Dataverse, ExternalDataShare, AzureBlobStorage, OneDriveSharePoint`.

**Shortcut security governance facts** (from §1 doc): internal-shortcut listing returns ALL shortcuts regardless of target access (access checked only on open); external shortcuts require the requesting user to have **Fabric Read on the item where the shortcut resides** AND the delegated connection must authorize (both must pass). Direct Lake-over-SQL / T-SQL delegated mode pass the **item owner's** identity, not the caller's — a known passthrough caveat.

**How it helps the agent.** Inventory shortcuts via the List Shortcuts API to (a) map the real **blast radius / data lineage** of an item (external dependencies that break if a connection or capacity changes); (b) flag **cross-cloud egress** (S3/GCS) and read-only vs writable targets; (c) detect risky `connectionId` reuse; (d) reason about the passthrough caveat when assessing data exposure.

**Related:** List Shortcuts — https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/list-shortcuts ; Shortcut security — https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcut-security

---

## 3. OneLake catalog — Govern tab (admin governance)

**TITLE:** Govern your Fabric data with the OneLake catalog
**URL:** https://learn.microsoft.com/en-us/fabric/governance/onelake-catalog-govern

**Summary.** The OneLake catalog has Explore / **Govern** / Secure tabs. The **Govern** tab gives Fabric admins tenant-wide governance posture (and data owners a "My items" view). Admin insights are sourced from the **Admin Monitoring Storage** in the auto-created **Admin Monitoring workspace** and refresh **once per day**.

**"View more" report — three tabs (exact names):**
- **Manage your data estate** — inventory overview, capacities & domains, feature usage across tenant.
- **Protect, secure & comply** — **Sensitivity labels** selector (most-used labels, % unlabeled, drill by item type/user/domain/workspace) and **DLP** selector (workspaces/items evaluated by DLP, violations, last evaluation time). These absorb security insights formerly in the Purview Hub.
- **Discover, trust, and reuse** — data freshness, item curation state (description coverage, **endorsement** coverage), content-sharing view.

**Recommended-action cards** suggest: set Domains/Tags, increase **sensitivity-label coverage**, enable **endorsement/certification**, set policies. Each card explains the issue + steps.

**Limits:** subitems (tables) not shown; no cross-tenant/guest; unavailable under Private Link; admin data is ≤1 day stale; admin semantic model is read-only (NOT usable with Fabric data agents); Pro license needed unless workspace on capacity.

**How it helps the agent.** Quantitative governance KPIs (endorsement %, description %, **% unlabeled items**, DLP coverage, freshness) to summarize tenant posture and prioritize recommendations. The 1-day refresh and the "read-only admin semantic model" note matter for how the agent sources/cites this data.

**Related:** OneLake catalog overview (Explore/Govern/Secure) — https://learn.microsoft.com/en-us/fabric/governance/onelake-catalog-overview ; Security insights in Govern tab (blog) — https://blog.fabric.microsoft.com/en-us/blog/explore-your-fabric-security-insights-in-the-onelake-catalog-govern-tab/

---

## 4. Fabric capacity management via Azure ARM — create/scale/suspend/resume/delete

### 4a. ARM / Bicep / Terraform resource — `Microsoft.Fabric/capacities`
**TITLE:** Microsoft.Fabric/capacities — Bicep, ARM & Terraform AzAPI reference
**URL:** https://learn.microsoft.com/en-us/azure/templates/microsoft.fabric/capacities

- **API versions:** latest `2025-01-15-preview`; stable `2023-11-01` (https://learn.microsoft.com/en-us/azure/templates/microsoft.fabric/2023-11-01/capacities).
- **Resource type:** `Microsoft.Fabric/capacities`.
- **`name`** constraints: 3–63 chars, pattern `^[a-z][a-z0-9]*$` (lowercase, no hyphens/underscores — relevant to name-availability checks).
- **Required properties:**
  - `location` (string)
  - `sku` (`RpSku`): `sku.name` (string — the F-SKU, e.g. `F64`) and `sku.tier` = **`'Fabric'`** (only valid tier).
  - `properties.administration.members` — `string[]` of **capacity admin** user identities (UPNs/object IDs). Required.
  - `tags` (optional dictionary).

> Note: the ARM template ref types `sku.name` as an opaque `string` (no enum in the schema). The concrete F2–F2048 ladder is enumerated in §4d below.

ARM JSON skeleton:
```json
{ "type":"Microsoft.Fabric/capacities","apiVersion":"2025-01-15-preview",
  "name":"mycap","location":"westus",
  "properties":{"administration":{"members":["admin@contoso.com"]}},
  "sku":{"name":"F64","tier":"Fabric"} }
```
Azure Verified Module: `avm/res/fabric/capacity` (https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/fabric/capacity).

### 4b. Azure (ARM) control-plane REST — Fabric Capacities operation group
**TITLE:** Fabric Capacities — REST API (Azure Fabric), api-version 2023-11-01
**URL:** https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities?view=rest-microsoftfabric-2023-11-01

All under `https://management.azure.com` with `?api-version=2023-11-01`. Operations:
| Operation | What |
|---|---|
| Check Name Availability | local name-availability check |
| Create Or Update | create a FabricCapacity (set SKU, admins) |
| Delete | delete a FabricCapacity |
| Get | get one capacity |
| List By Resource Group | list capacities in an RG |
| List By Subscription | list capacities in a subscription |
| List Skus | eligible SKUs for the RP |
| List Skus For Capacity | eligible SKUs (for scale up/down) for an existing capacity |
| **Resume** | resume (start billing/compute) |
| **Suspend** | suspend (stop billing/compute) |
| Update | update a capacity (e.g. **scale** SKU, change admins) |

### 4c. Suspend / Resume detail
**TITLE:** Fabric Capacities — Suspend / Resume (Azure Fabric REST)
**URL (Suspend):** https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/suspend?view=rest-microsoftfabric-2023-11-01
**URL (Resume):** https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/resume?view=rest-microsoftfabric-2023-11-01

```
POST https://management.azure.com/subscriptions/{subscriptionId}/resourceGroups/{resourceGroupName}/providers/Microsoft.Fabric/capacities/{capacityName}/suspend?api-version=2023-11-01
POST .../capacities/{capacityName}/resume?api-version=2023-11-01
```
- **Auth:** Azure AD OAuth2, scope `user_impersonation` (i.e. Azure RBAC on the resource, not Fabric scopes). The action permissions are `Microsoft.Fabric/capacities/suspend/action` and `.../resume/action` (Contributor/Owner on the capacity, or a custom role).
- **Responses:** `200 OK` or `202 Accepted` (async — poll `Location` + `Azure-AsyncOperation` headers). .NET SDK: `Azure.ResourceManager.Fabric`, `FabricCapacityResource.SuspendAsync(...)`.
- **Pause/resume guidance:** https://learn.microsoft.com/en-us/fabric/enterprise/pause-resume — only F-SKUs (Azure resource) can pause; pausing stops compute billing (storage still billed); running/queued jobs are affected.

### 4d. SKU ladder F2–F2048, licensing, reservations, autoscale
**TITLE:** Microsoft Fabric pricing / Capacity reservations / Autoscale Billing for Spark
**URLs:**
- Pricing — https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/
- Capacity reservations — https://learn.microsoft.com/en-us/azure/cost-management-billing/reservations/fabric-capacity
- Autoscale Billing for Spark — https://learn.microsoft.com/en-us/fabric/data-engineering/autoscale-billing-for-spark-overview
- Configure autoscale — https://learn.microsoft.com/en-us/fabric/data-engineering/configure-autoscale-billing

**SKU ladder (F-SKU → Capacity Units; CU = name number):** `F2(2), F4(4), F8(8), F16(16), F32(32), F64(64), F128, F256, F512, F1024, F2048`. Approx PAYG list (2026): F2 ~$263, F4 ~$526, F8 ~$1,051, F16 ~$2,102, F32 ~$4,205, F64 ~$8,410/mo; scales ~linearly above.

**Licensing threshold (audit-relevant):** SKUs **< F64** (F2–F32) require Pro/PPU per viewer to consume Power BI content; **F64 and above** give free-viewer Power BI consumption.

**Reservations:** choose Azure region + 1-yr/3-yr term + CU quantity; save ~**41%** vs PAYG. Reservations are a billing discount, decoupled from the live SKU (a reservation matches any same-region capacity CUs).

**Autoscale Billing for Spark (GA):** opt-in **per capacity**; Spark jobs move to a **serverless pay-as-you-go** pool (rate 0.5 CU-hour, billed only for active job compute) and **no longer consume the capacity's CUs** — so they don't burst/throttle the capacity. A **max CU limit** caps spend; over the limit, batch jobs queue and interactive jobs throttle.

**How it helps the agent.** This is the cost/ops lever set. The agent (read-only) can: (a) read the SKU + admins via Get; (b) recommend **scale down / suspend during idle windows** (citing pause-resume + the async pattern) to cut spend; (c) flag **sub-F64 capacities serving many Power BI viewers** (per-user license waste); (d) recommend **reservations** for steady ~41% savings; (e) recommend **Autoscale Billing for Spark** to decouple bursty Spark from capacity throttling; (f) note the lowercase-only naming rule when validating capacity names.

**Related (policy):** restrict max SKU via Azure Policy — https://sandervandevelde.wordpress.com/2025/06/08/limit-fabric-capacity-size-with-custom-policy/

---

## 5. Fabric Git integration (CI/CD)

### 5a. Git REST APIs (Core)
**TITLE:** Git — REST API (Core) / Automate Git integration by using APIs
**URLs:** https://learn.microsoft.com/en-us/rest/api/fabric/core/git ; https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-automation

All under `https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/git/...`:
| Operation | Endpoint | Notes |
|---|---|---|
| **Connect** | `POST .../git/connect` | scope **`Workspace.ReadWrite.All`**; caller must be workspace **Admin**; does NOT sync |
| Initialize Connection | `POST .../git/initializeConnection` | required after Connect, before first sync |
| **Commit To Git** | `POST .../git/commitToGit` | push workspace → branch |
| **Update From Git** | `POST .../git/updateFromGit` | pull branch → workspace |
| **Get Status** | `GET .../git/status` | needs `Workspace.GitUpdate.All` or `Workspace.GitCommit.All`; shows incoming/uncommitted changes |
| Get Connection | `GET .../git/connection` | |
| Disconnect | `POST .../git/disconnect` | |

**Connect body (`GitProviderDetails`):** `gitProviderType` = **`AzureDevOps`** or **`GitHub`**. AzureDevOps: `organizationName`(≤100), `projectName`(≤100), `repositoryName`(≤128), `branchName`(≤250), `directoryName`(≤256). GitHub: `ownerName`, optional `customDomainName` (ghe.com only), repo/branch/directory. Optional `myGitCredentials` (`source` = `Automatic` | `ConfiguredConnection` (`connectionId`) | `None`).
**SP/MI support:** supported **only** when `myGitCredentials.source = ConfiguredConnection`; with **Automatic** credentials, Connect is **blocked for GitHub and for Service Principal**. Common errors: `WorkspaceAlreadyConnectedToGit`, `WorkspaceHasNoCapacityAssigned`, `InsufficientPrivileges`, `PrincipalTypeNotSupported`.

### 5b. Git integration tenant admin settings
**TITLE:** Git integration admin settings
**URL:** https://learn.microsoft.com/en-us/fabric/admin/git-integration-admin-settings

Switches (delegable: tenant → capacity admin → workspace admin override). Require the master **Fabric** switch on.
- **"Users can synchronize workspace items with their Git repositories"** — Azure DevOps sync; **enabled by default**.
- **"Users can export items to Git repositories in other geographical locations"** — allow cross-geo metadata commit; GitHub can't enforce this.
- **"Users can export workspace items with applied sensitivity labels to Git repositories"** — labels are NOT exported; choose to block or allow export of labeled items.
- **"Users can sync workspace items with GitHub repositories"** — GitHub sync; **disabled by default**.

**How it helps the agent.** Detect workspaces **not** under source control (no Git connection) as a CI/CD/governance gap; flag cross-geo export or labeled-item export risks; confirm SP-based automation viability (must use ConfiguredConnection); read Get Status to report drift. fabric-cicd (Microsoft-supported Python lib) — https://blog.fabric.microsoft.com/en-us/blog/announcing-official-support-for-microsoft-fabric-cicd-tool/

---

## 6. Fabric deployment pipelines (CI/CD)

**TITLE:** Automate deployment pipeline by using Fabric APIs / Deployment pipelines REST API
**URLs:** https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/pipeline-automation-fabric ; https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines

Operations (under `.../v1/deploymentPipelines/...`):
- **Create / Get / Update / Delete deployment pipeline**; **List Deployment Pipelines**.
- **List/Get Deployment Pipeline Stages**; **Get/Update Stage**; **List Deployment Pipeline Stage Items**.
- **Assign workspace to stage** / **Unassign workspace from stage**.
- **Deploy Stage Content** — deploy all or selected items between stages; integrated with **Long Running Operations** (poll Get Operation State; result available 24 h via Get Operation Result).
- **Add/Delete/List role assignments**; **Get/List pipeline operations**.

Auth: Microsoft Entra token for Fabric; **service principal supported** (sample scripts take `UserPrincipal` or `ServicePrincipal` with client/tenant/secret). Deploy body provides source stage, target stage, optional item list, deployment note.
**Limitations:** Dataflows not supported (use Power BI APIs); `allowPurgeData`, `allowTakeOver`, `allowSkipTilesWithMissingPrerequisites` NOT available in Fabric's Deploy API (Power BI-only).

**How it helps the agent.** Read pipeline/stage topology to verify a **dev→test→prod release process exists**, that prod workspaces are stage-paired, and that deployments use a service principal (not personal accounts). Surface stale/failed deployment operations. Recommend deployment pipelines for workspaces lacking ALM. CI/CD workflow options — https://learn.microsoft.com/en-us/fabric/cicd/manage-deployment

---

## 7. Fabric Admin / Tenant-settings + governance REST APIs (uncovered set)

### 7a. Tenant Settings API
**TITLE:** Tenants — List Tenant Settings (Admin REST)
**URL:** https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-tenant-settings
```
GET https://api.fabric.microsoft.com/v1/admin/tenantsettings[?continuationToken=...]
```
- **Permissions:** Fabric administrator **or** service principal. **Scope:** `Tenant.Read.All` or `Tenant.ReadWrite.All`. **Rate limit: 25 req/min.** SP/MI: **Yes**.
- **`TenantSetting` object fields:** `settingName`, `title`, `enabled`, `canSpecifySecurityGroups`, `tenantSettingGroup`, `enabledSecurityGroups[]` & `excludedSecurityGroups[]` (`TenantSettingSecurityGroup{graphId,name}`), `delegateToWorkspace`, `delegateToCapacity`, `delegateToDomain`, `properties[]` (`TenantSettingProperty{name,type,value}`; type ∈ FreeText/Url/Boolean/MailEnabledSecurityGroup/Integer). Response wrapper `TenantSettings{value[],continuationToken,continuationUri}`.
- Related: **List Capacities/Workspaces/Domains Tenant Settings Overrides** — e.g. https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-domains-tenant-settings-overrides
- Catalog of every setting: **Tenant settings index** — https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index

**How it helps the agent.** One read gives the full security-relevant tenant config: which features are org-wide vs scoped to security groups, and which are delegated to capacity/workspace/domain admins. The agent flags risky toggles (e.g. external data sharing, SP access, public publish, export) and `canSpecifySecurityGroups=false` (org-wide exposure).

### 7b. Domains API
**TITLE:** Domains — List Domains (Admin REST)
**URL:** https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains/list-domains
```
GET https://api.fabric.microsoft.com/v1/admin/domains?preview=false[&nonEmptyOnly=true]
```
- Note: `preview=false` is **required** (older preview version deprecates 2026-03-31). Permissions: Fabric admin; scope `Tenant.Read.All`/`Tenant.ReadWrite.All`; **25 req/min**; SP/MI Yes.
- **`Domain` object:** `id`, `displayName`, `description`, `parentDomainId` (subdomain support), `defaultLabelId` (domain default **sensitivity label**).
- Other ops in the group: Create/Delete/Update Domain; **Assign Domain Workspaces By Ids** (`POST .../domains/{domainId}/assignWorkspaces`), **By Capacities** (`assignWorkspacesByCapacities`), **By Principals**; Unassign; Domain Role Assignments bulk assign/unassign. Assign/write ops require `Tenant.ReadWrite.All`. Group landing — https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains ; List Domain Workspaces — https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains/list-domain-workspaces

**How it helps the agent.** Map workspaces → domains/subdomains to scope governance/data-mesh ownership, detect workspaces with no domain, and read each domain's default sensitivity label. Domain management UI — https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-domain-management-settings

### 7c. External Data Shares — Admin API + governance
**TITLE:** External Data Shares Provider (Admin REST) / External Data Sharing overview
**URLs:** https://learn.microsoft.com/en-us/rest/api/fabric/admin/external-data-shares-provider ; https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-overview
```
GET  https://api.fabric.microsoft.com/v1/admin/items/externalDataShares                 (list tenant-wide; paginated)
POST https://api.fabric.microsoft.com/v1/admin/workspaces/{workspaceId}/items/{itemId}/externalDataShares/{externalDataShareId}/revoke
```
- Permissions: Fabric admin; the **External data sharing** admin switch must be enabled for the calling principal. **Revoke rate limit: 10 req/min**; revoke is **irreversible**.
- **Feature:** in-place, **read-only, cross-tenant** OneLake sharing — creates a shortcut in the consumer tenant pointing back to live source data (no copy). **Provider items:** lakehouses, warehouses, KQL/SQL/mirrored DBs (tables or files). **Consumer:** only a lakehouse can host the resulting shortcut. Needs Fabric **Read + Reshare** on the shared item. Uses dedicated Fabric-to-Fabric auth (NOT Entra B2B).
- **Security risk (auditor-critical):** provider governance does **NOT** cross tenant boundaries — semantic-model **RLS, Purview Information Protection labels, and DLP are NOT enforced** on shared data in the consumer tenant; the consumer can re-grant to anyone (incl. their guests); data may cross geos. The grant exposes read access to **any user in the invited user's home tenant**.
- Enable switch — https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-enable ; Manage/revoke — https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-manage

**How it helps the agent.** This is a top exfiltration vector. The agent lists all external data shares tenant-wide, flags shares of sensitive/labeled items, warns that provider RLS/labels/DLP don't follow the data, and recommends review/revoke (a human action — agent is read-only).

### 7d. Git APIs (admin/automation)
Covered in §5a (Core Git APIs) + §5b (admin settings). The Admin-side Git visibility is via tenant-settings overrides (§7a) and the per-workspace Get Connection/Get Status (§5a).

### 7e. Admin portal / governance surfaces (concepts)
- **Monitoring hub** — https://learn.microsoft.com/en-us/fabric/admin/monitoring-hub : per-user/workspace view of activities (refreshes, runs); read for operational health.
- **Fabric identities tab** (admin portal) — manage workspace identities tenant-wide — https://learn.microsoft.com/en-us/fabric/admin/fabric-identities-manage
- **Export & sharing tenant settings** — https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-export-sharing

---

## 8. Microsoft Purview hub + sensitivity labels (governance)

**TITLE:** The Microsoft Purview hub in Microsoft Fabric / Information protection in Fabric / Protection policies
**URLs:**
- Purview hub — https://learn.microsoft.com/en-us/fabric/governance/use-microsoft-purview-hub
- Use Purview to govern Fabric — https://learn.microsoft.com/en-us/fabric/governance/microsoft-purview-fabric
- Information protection — https://learn.microsoft.com/en-us/fabric/governance/information-protection
- Protection policies — https://learn.microsoft.com/en-us/fabric/governance/protection-policies-overview
- Protected sensitivity labels — https://learn.microsoft.com/en-us/fabric/governance/protected-sensitivity-labels

**Summary.** The **Purview hub** (requires **Fabric administrator role or higher**) is the in-Fabric gateway to Purview Data Catalog, **Information Protection**, **DLP**, and **Audit**, plus a report with sensitivity-label/governance insights filterable by domain/workspace/item type/creator. **Sensitivity labels** (from Purview Information Protection) can be applied to **all Fabric items**; they classify and (with **protection policies**) control access by label. Workspace-identity audit events also surface in the Purview **Audit Log** (Created/Retrieved/Deleted Fabric Identity for Workspace).

**How it helps the agent.** Source for **% labeled vs unlabeled** sensitive items, DLP coverage, and label-distribution gaps; cross-references the OneLake catalog Govern tab (§3) which now hosts much of this. The agent reports labeling/DLP gaps and points the user to Purview for remediation.

---

## 9. Workspace identity / managed identity for Fabric items

**TITLE:** Workspace identity / Authenticate with workspace identity / Trusted workspace access
**URLs:**
- Workspace identity — https://learn.microsoft.com/en-us/fabric/security/workspace-identity
- Authenticate — https://learn.microsoft.com/en-us/fabric/security/workspace-identity-authenticate
- Trusted workspace access — https://learn.microsoft.com/en-us/fabric/security/security-trusted-workspace-access

**Summary.** A **workspace identity** is an **automatically managed service principal** (with backing Entra app registration) bound to a Fabric workspace (GA). Fabric manages its credentials (no keys/secrets to handle) and uses it to obtain Entra tokens. Name always = workspace name; lifecycle is tied to the workspace (delete workspace → identity deleted; **restore does NOT restore the identity**).

**What it CAN do:**
- **Authentication** for connections from: **OneLake shortcuts, data pipelines, semantic models, Dataflows Gen2 (CI/CD)** to data sources supporting Entra auth (only Admin/Member/Contributor can configure it on connections).
- **Trusted workspace access**: ADLS Gen2 shortcuts in a workspace with an identity can reach **firewall-enabled ADLS Gen2** (storage restricted to selected VNets/IPs) via trusted service access.
- Manageable by Fabric admins on the **Fabric identities** admin tab; audited in Purview; visible (don't edit) in Azure as Enterprise app + App registration.

**What it CANNOT do / limits:**
- Not available in **My Workspace**.
- Requires an **F-SKU** capacity; if the workspace moves to non-Fabric / non-F SKU, the identity persists but **trusted-access items stop working**.
- **Not supported in B2B / cross-tenant** scenarios.
- By default granted **no workspace role**; modifying/deleting the underlying Azure SP/app breaks dependent items (changes may be auto-reverted; adding API permissions to reach target resources IS supported).
- Default cap **10,000 identities/tenant** (tunable in tenant settings).
- Identity `State` values: `Active, Inactive, Deleting, Unusable, Failed, DeleteFailed`.

**How it helps the agent.** Detect workspaces using a workspace identity for trusted access to firewalled storage (a security best practice over stored keys); flag identities in `Failed`/`Unusable` state; warn that moving a workspace off an F-SKU will silently break trusted-access shortcuts; recommend workspace identity to replace credential-based connections. (Sibling doc covers the Databricks-access angle.)

---

## Flat URL list (all sources)

OneLake security / shortcuts / catalog:
- https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model
- https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-onelake-security
- https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security
- https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sharing
- https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcut-security
- https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts
- https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut
- https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/list-shortcuts
- https://learn.microsoft.com/en-us/fabric/governance/onelake-catalog-govern
- https://learn.microsoft.com/en-us/fabric/governance/onelake-catalog-overview
- https://blog.fabric.microsoft.com/en-us/blog/explore-your-fabric-security-insights-in-the-onelake-catalog-govern-tab/

Capacity / ARM / pricing:
- https://learn.microsoft.com/en-us/azure/templates/microsoft.fabric/capacities
- https://learn.microsoft.com/en-us/azure/templates/microsoft.fabric/2023-11-01/capacities
- https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities?view=rest-microsoftfabric-2023-11-01
- https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/suspend?view=rest-microsoftfabric-2023-11-01
- https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/resume?view=rest-microsoftfabric-2023-11-01
- https://learn.microsoft.com/en-us/fabric/enterprise/pause-resume
- https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/
- https://learn.microsoft.com/en-us/azure/cost-management-billing/reservations/fabric-capacity
- https://learn.microsoft.com/en-us/fabric/data-engineering/autoscale-billing-for-spark-overview
- https://learn.microsoft.com/en-us/fabric/data-engineering/configure-autoscale-billing
- https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/fabric/capacity
- https://sandervandevelde.wordpress.com/2025/06/08/limit-fabric-capacity-size-with-custom-policy/

Git / CI/CD:
- https://learn.microsoft.com/en-us/rest/api/fabric/core/git
- https://learn.microsoft.com/en-us/rest/api/fabric/core/git/connect
- https://learn.microsoft.com/en-us/rest/api/fabric/core/git/commit-to-git
- https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-from-git
- https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-status
- https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-automation
- https://learn.microsoft.com/en-us/fabric/admin/git-integration-admin-settings
- https://blog.fabric.microsoft.com/en-us/blog/automate-your-ci-cd-pipelines-with-microsoft-fabric-git-rest-apis/
- https://blog.fabric.microsoft.com/en-us/blog/announcing-official-support-for-microsoft-fabric-cicd-tool/
- https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/pipeline-automation-fabric
- https://learn.microsoft.com/en-us/rest/api/fabric/core/deployment-pipelines
- https://learn.microsoft.com/en-us/fabric/cicd/manage-deployment

Admin / governance REST + portal:
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-tenant-settings
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/tenants/list-domains-tenant-settings-overrides
- https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains/list-domains
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains/list-domain-workspaces
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains/assign-domain-workspaces-by-ids
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/domains/assign-domain-workspaces-by-capacities
- https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-domain-management-settings
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/external-data-shares-provider
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/external-data-shares-provider/list-external-data-shares
- https://learn.microsoft.com/en-us/rest/api/fabric/admin/external-data-shares-provider/revoke-external-data-share
- https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-overview
- https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-enable
- https://learn.microsoft.com/en-us/fabric/governance/external-data-sharing-manage
- https://learn.microsoft.com/en-us/fabric/admin/monitoring-hub
- https://learn.microsoft.com/en-us/fabric/admin/fabric-identities-manage
- https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-export-sharing

Purview / sensitivity labels:
- https://learn.microsoft.com/en-us/fabric/governance/use-microsoft-purview-hub
- https://learn.microsoft.com/en-us/fabric/governance/microsoft-purview-fabric
- https://learn.microsoft.com/en-us/fabric/governance/information-protection
- https://learn.microsoft.com/en-us/fabric/governance/protection-policies-overview
- https://learn.microsoft.com/en-us/fabric/governance/protected-sensitivity-labels

Workspace identity:
- https://learn.microsoft.com/en-us/fabric/security/workspace-identity
- https://learn.microsoft.com/en-us/fabric/security/workspace-identity-authenticate
- https://learn.microsoft.com/en-us/fabric/security/security-trusted-workspace-access

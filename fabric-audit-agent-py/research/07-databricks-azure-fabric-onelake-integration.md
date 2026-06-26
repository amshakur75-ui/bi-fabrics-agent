# 07 — Databricks ↔ Azure / Fabric / OneLake Integration + Networking

Research focus: how a READ-ONLY Fabric/PBI capacity-audit agent running **in Databricks** can (a) reach the Microsoft control-plane APIs (`api.powerbi.com`, `api.fabric.microsoft.com`, `*.kusto.fabric.microsoft.com`, `management.azure.com`, `login.microsoftonline.com`) and `api.anthropic.com`, and (b) **read Fabric / OneLake data** directly.

Scope deliberately excludes already-covered topics (capacity telemetry, OAuth/scopes, Fabric/PBI REST, Apps+MCP+Mosaic AI+Claude, Asset Bundles, UC volumes, secrets, Kusto-from-Python, databricks-sdk). This file is the **integration + networking plumbing** that those depend on.

Research date: 2026-06-22. All docs current as of their `ms.date` shown per item.

---

## PART A — Reading / writing OneLake & Fabric data from Databricks

### A1. Integrate OneLake with Azure Databricks (the canonical how-to)
- **TITLE:** Integrate OneLake with Azure Databricks — Microsoft Fabric
- **URL:** https://learn.microsoft.com/en-us/fabric/onelake/onelake-azure-databricks
- **Summary:** Two supported paths to read/write OneLake from Databricks, both using **service-principal auth** against the **OneLake ABFS endpoint**. (1) *Standard / job cluster*: set `fs.azure.*` Spark configs and use the Spark ABFS driver with Spark DataFrames. (2) *Serverless compute*: serverless **forbids** custom `fs.azure.*` Spark configs (returns `CONFIG_NOT_AVAILABLE`), so instead use **MSAL** to get an OAuth token + the Python **`deltalake`** library to read/write Delta tables.
- **Exact identifiers:**
  - ABFS URI: `abfss://<workspace_id_or_name>@onelake.dfs.fabric.microsoft.com/<lakehouse_id_or_name>.lakehouse/Files/<path>` (or `/Tables/<path>`). IDs or names accepted; avoid spaces/special chars in names.
  - Standard-cluster Spark conf keys: `fs.azure.account.auth.type` = `OAuth`; `fs.azure.account.oauth.provider.type` = `org.apache.hadoop.fs.azurebfs.oauth2.ClientCredsTokenProvider`; `fs.azure.account.oauth2.client.id`; `fs.azure.account.oauth2.client.secret`; `fs.azure.account.oauth2.client.endpoint` = `https://login.microsoftonline.com/{tenant_id}/oauth2/token`.
  - Serverless: `from msal import ConfidentialClientApplication`; `from deltalake import DeltaTable, write_deltalake`. Authority `https://login.microsoftonline.com/{tenant_id}`; token scope **`https://onelake.fabric.microsoft.com/.default`**. Read: `DeltaTable(onelake_uri, storage_options={"bearer_token": token_val, "use_fabric_endpoint": "true"})`. Write: `write_deltalake(target_uri, df, mode="overwrite", storage_options={"bearer_token": token_val, "use_fabric_endpoint": "true"})`.
  - SP needs at least **Contributor** workspace role in Fabric; premium Databricks workspace; secrets via Databricks secrets or Azure Key Vault.
- **How it helps:** This is the most direct way for the agent to **physically read Fabric lakehouse Delta tables** (e.g. to audit dataset/table footprint, row counts, table sizes) from Databricks. The serverless MSAL + `deltalake` recipe is the right pattern for a serverless audit job that can't set Spark configs. The `.../.default` scope and `login.microsoftonline.com` token endpoint are the same identity plumbing the agent uses for all Microsoft APIs.

### A2. Enable OneLake catalog federation (read Fabric tables via Unity Catalog, no copy) — STRONGEST FIT
- **TITLE:** Enable OneLake catalog federation — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/onelake
- **Summary:** Lets **Unity Catalog query Fabric Lakehouse/Warehouse data in place, read-only, no copy**. You create a UC **storage credential** (Managed Identity via Databricks Access Connector, *or* Azure service principal — SP supports **cross-tenant**), a UC **connection** of `TYPE onelake`, then a **foreign catalog** bound to a specific Fabric data item. Tables appear under three-part `catalog.schema.table` naming and sync automatically.
- **Exact identifiers / setup:**
  - Compute: **Databricks Runtime 18.0+ standard access mode**; SQL warehouses **2025.40+**. Dedicated access mode NOT supported.
  - Privileges: `CREATE CONNECTION` + `CREATE STORAGE CREDENTIAL` (or metastore admin); `CREATE CATALOG` / `CREATE FOREIGN CATALOG`.
  - **Fabric tenant settings that must be ON:** "Service principals can use Fabric APIs"; "**Allow apps running outside of Fabric to access data via OneLake**"; "**Use short-lived user-delegated SAS tokens**". Workspace setting: **Workspace settings > Delegated settings > OneLake settings > Authenticate with OneLake user-delegated SAS tokens**.
  - SP/MI needs **Member** role (min) on the Fabric workspace.
  - Access-connector resource ID format: `/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/<name>`.
  - SP storage credential must be created via **Storage Credentials API** (`POST /api/2.1/unity-catalog/storage-credentials`, `read_only: true`, `azure_service_principal{directory_id, application_id, client_secret}`) — *cannot* be created in Catalog Explorer for SP.
  - SQL: `CREATE CONNECTION <name> TYPE onelake OPTIONS (workspace '<workspace-id>', credential '<storage-credential-name>');` then `CREATE FOREIGN CATALOG <name> USING CONNECTION <conn> OPTIONS (data_item '<guid>', item_type 'Lakehouse'|'Warehouse', create_volume_for_lakehouse_files 'true');`
  - Unstructured `/Files` exposed as a UC **volume** under schema `onelake-folders` → `/Volumes/<catalog>/onelake-folders/files/`.
  - **Limitations:** read-only (SELECT only); only Fabric Lakehouse & Warehouse; no arrays/maps/structs; no materialized views/views; can't change `workspace` option after create; no case-only-differing column names.
- **How it helps:** Cleanest read path for the audit agent — Fabric Lakehouse/Warehouse tables become **governed, read-only UC tables** queryable with plain SQL/Spark, no data movement, with UC permissions on top. Ideal if the agent needs to inspect actual Fabric table contents/metadata while staying read-only by construction. Cross-tenant SP support covers multi-tenant capacity audits.

### A3. How to connect to OneLake (ADLS/Blob API surface, endpoints, scopes)
- **TITLE:** How do I connect to OneLake? — Microsoft Fabric
- **URL:** https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api
- **Summary:** OneLake speaks a **subset of the ADLS Gen2 + Blob APIs**, so any ADLS/Blob-compatible SDK/tool works by swapping the endpoint. Auth is Microsoft Entra bearer token in the `Storage` audience.
- **Exact identifiers / endpoints:**
  - DFS endpoint: `https://onelake.dfs.fabric.microsoft.com/<workspace>/<item>.<itemtype>/<path>/<fileName>` (GUID form: `.../<workspaceGUID>/<itemGUID>/...`).
  - ABFS form: `abfs[s]://<workspace>@onelake.dfs.fabric.microsoft.com/<item>.<itemtype>/<path>/<fileName>`.
  - Account name is always `onelake`; container = workspace; item types like `.lakehouse`, `.warehouse`.
  - **Regional endpoint:** `https://<region>-onelake.dfs.fabric.microsoft.com` (e.g. `westus-onelake...`) — use to keep data in-region.
  - **Private-endpoint / workspace FQDN:** `https://<wsid>.z<xy>.dfs.fabric.microsoft.com`.
  - **General FQDN variants:** `https://api.onelake.fabric.microsoft.com` and `https://<region>-api.onelake.fabric.microsoft.com` (may break tools that key off ".dfs"/".blob").
  - Token audience must be `Storage`; PowerShell example: `Get-AzAccessToken -ResourceTypeName Storage`.
  - Common gotcha: tools that validate `dfs.core.windows.net` reject `dfs.fabric.microsoft.com` — must allowlist OneLake endpoint.
- **How it helps:** Defines the exact hostnames the agent's egress must reach to read OneLake (`onelake.dfs.fabric.microsoft.com` and regional/private variants), and confirms the agent can reuse standard Azure Storage SDKs. Critical for the networking allowlists in Part C.

### A4. OneLake shortcuts (how Fabric virtualizes Databricks/ADLS data, and how Databricks reads shortcut tables)
- **TITLE:** Unify data sources with OneLake shortcuts — Microsoft Fabric
- **URL:** https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts
- **Summary:** Shortcuts are symbolic-link objects in OneLake pointing at internal OneLake items or external stores (**ADLS Gen2**, S3, GCS, Dataverse, Iceberg, OneDrive/SharePoint), or on-prem via the on-premises data gateway. They let Fabric read external/Databricks-managed data **without copying**. Importantly, **Mirrored Azure Databricks Catalogs** are listed as a valid **internal shortcut target type**.
- **Exact identifiers:**
  - Tables-folder shortcuts must be top-level and in Delta-Parquet to auto-register as tables; Files-folder shortcuts anywhere.
  - Non-Fabric access via OneLake API: `https://onelake.dfs.fabric.microsoft.com/MyWorkspace/MyLakehouse/Tables/MyShortcut/MyFile.csv`.
  - Spark read of a shortcut table: `spark.read.format("delta").load("Tables/MyShortcut")`.
  - ADLS/S3 shortcuts delegate auth via **cloud connections** (bind operation); caching 1–28 days (GCS/S3/S3-compat/on-prem only; files >1 GB not cached).
  - Limits: 100,000 shortcuts/item; 10 shortcuts per OneLake path; max 5 chained shortcut-to-shortcut; no `%`/`+`/non-Latin chars/spaces in Delta-table shortcut names.
- **How it helps:** Explains the mechanism behind UC-→-Fabric mirroring (it's shortcuts under the hood) and lets the agent reason about where Fabric data physically lives when auditing storage. If Databricks-managed ADLS is surfaced into Fabric via ADLS shortcuts, the agent can read the same bytes from either side.

### A5. Use Azure managed identities in Unity Catalog to access ADLS Gen2 storage
- **TITLE:** Use Azure managed identities in Unity Catalog to access storage / Connect to an ADLS Gen2 external location
- **URLs:**
  - https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities
  - https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/external-locations-adls
- **Summary:** To read ADLS Gen2 (the substrate under OneLake-as-ADLS and under Fabric-mirrored storage) you create two UC securables: a **storage credential** (Azure **Access Connector for Azure Databricks** managed identity — system- or user-assigned) and an **external location** (an `abfss://` path + the credential). Managed identities avoid secret rotation and can reach **firewall-protected** storage accounts.
- **Exact identifiers:** Path must start with `abfss://`. Resource type `Microsoft.Databricks/accessConnectors`. Grant the MI a Storage role (e.g. Storage Blob Data Reader/Contributor) on the account.
- **How it helps:** This is the read-only-friendly, secretless way for the agent to reach ADLS Gen2 backing stores (including OneLake-as-ADLS Gen2 and storage behind Fabric mirroring/trusted-workspace access). The managed-identity path is what makes "read firewalled storage" possible without baking in secrets.

---

## PART B — Fabric mirroring of Unity Catalog / Databricks, and the Power BI connector

### B1. Mirrored Azure Databricks Unity Catalog in Fabric (Fabric reads Databricks data)
- **TITLE:** Microsoft Fabric Mirrored Catalog From Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/fabric/mirroring/azure-databricks
- **Summary:** Mirrors the **UC catalog structure (metadata only)** into Fabric; underlying data is accessed via **OneLake shortcuts** — **no data movement / no replication**. Fabric auto-creates a **Mirrored Azure Databricks item** + a **SQL analytics endpoint** (T-SQL, read-only) and supports **Power BI Direct Lake** over it. Metadata auto-sync (schemas/tables add/delete) is on by default. Materialized views, streaming tables, and non-Delta external tables are excluded.
- **Tutorial:** https://learn.microsoft.com/en-us/fabric/mirroring/azure-databricks-tutorial
- **How it helps:** The inverse direction — surfaces Databricks UC tables into Fabric. Useful context for the audit agent to understand that "Fabric items" it audits may actually be **mirrored Databricks tables** (a capacity/storage cost attribution nuance: data isn't duplicated, but compute/Direct Lake load against them counts against Fabric capacity).

### B2. Mirroring security & networking (credentials, firewalled ADLS, trusted workspace access)
- **TITLE:** Microsoft Fabric Mirrored Databases From Azure Databricks Security
- **URL:** https://learn.microsoft.com/en-us/fabric/mirroring/azure-databricks-security
- **Summary:** **UC permissions are NOT mirrored** — you must re-apply access control in Fabric's model. A **service principal or OAuth** authenticates to Databricks/UC; **the connection credential is used for all data queries**. For **firewall-enabled ADLS Gen2**, enable **trusted workspace access** — Fabric uses the **Workspace Identity** (must be allowlisted in the storage firewall) even when SP is selected for ADLS auth. UC RLS/CLM/ABAC are **not** enforced when storage is accessed directly via a connection.
- **Related:** Control external access to data in UC — https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-open-api
- **How it helps:** Tells the agent (a) it cannot rely on UC permissions carrying into Fabric when reasoning about access/security audits, and (b) the exact identity (Workspace Identity / SP) and firewall allowlisting needed for Fabric to read firewalled Databricks storage — a security-posture item the audit agent may want to flag.

### B3. Power BI with Azure Databricks — integration overview
- **TITLE:** Power BI with Azure Databricks — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi
- **Summary:** Hub page. Connect via **Power BI Desktop** (Partner Connect or manual) or **Power BI service** (publish). Setup options: **M2M OAuth service principal**, and **ADBC vs ODBC driver** choice. Orchestration via a **Power BI task** requires a UC **Power BI connection** first.
- **How it helps:** Orients the agent on all PBI↔Databricks surfaces; the "Databricks (Azure Databricks)" data source in Power BI is exactly the connector an audit agent would inspect when correlating semantic models to Databricks SQL warehouses.

### B4. Connect Power BI Desktop to Azure Databricks (the "Databricks" data source)
- **TITLE:** Connect Power BI Desktop to Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-desktop
- **Also:** Power Query connector pages — https://learn.microsoft.com/en-us/power-query/connectors/databricks-azure and https://learn.microsoft.com/en-us/power-query/connectors/databricks
- **Summary:** Uses **Server Hostname + HTTP Path** of a Databricks SQL warehouse (or cluster). Auth: PAT, OAuth (U2M), or **M2M service principal** (needs Power BI Desktop **2.143.878.0 / May 2025** or above). DirectQuery recommended on SQL warehouses; SP needs **CAN USE** on the warehouse.
- **How it helps:** Defines the "Databricks (Azure Databricks)" data source identifiers (hostname/HTTP path) and auth modes the agent will see when auditing how PBI semantic models pull from Databricks.

### B5. Publish to the Power BI service from Azure Databricks ("Use with BI tools" → "Publish to Power BI workspace")
- **TITLE:** Publish to the Power BI service from Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-service
- **Summary:** From Catalog Explorer, **Use with BI tools** (schema) / **Open in a dashboard** (table) → **Publish to Power BI workspace** creates a **Power BI semantic model** (Import or DirectQuery — *not* Direct Lake). Auth to Power BI is via the Entra app **"Databricks Dataset Publishing Integration"** needing `Content.Create`, `Dataset.ReadWrite.All`, `Workspace.Read.All`. Data-source auth: OAuth (recommended) or PAT; M2M OAuth configurable post-publish.
- **Exact identifiers / requirements:**
  - Data must be in **Unity Catalog** (Hive metastore unsupported); a **Databricks SQL warehouse**; **Power BI Premium / PPU / Fabric capacity** license; **XMLA Endpoint = Read Write** on the capacity.
  - Publishing only to **home-tenant** PBI workspaces (no guest).
  - SSO (DirectQuery): "Report viewers can only access this data source with their own Power BI identities using Direct Query."
  - **If PBI workspace uses Private Link / Databricks uses Private Link or IP access lists → must configure a Power BI on-premises (or VNet) data gateway**; client credentials need gateway **v3000.270.10+**.
  - Note: Entra **managed** SP → workflows >1 hour fail.
- **How it helps:** This is the agent's own potential push surface to Power BI AND a key audit target (which semantic models originated from Databricks, in which mode, with what auth). The XMLA / Premium / capacity requirements tie directly into capacity-audit logic.

### B6. Power BI connection in Unity Catalog (Power BI task orchestration)
- **TITLE:** Create a Power BI connection in Unity Catalog for orchestration
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-uc-connect
- **Summary:** Creates a UC **connection of type Power BI** storing Entra creds, used by a **Power BI task (preview)** to refresh/publish semantic models from a Databricks job. Auth: **Service credential**, **OAuth M2M**, or **OAuth U2M**. Authorization endpoint `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize` (use `common` for home tenant). Same Entra app + permissions as B5. SP must be added to the PBI workspace and **enabled in the Power BI admin portal**.
- **Related task doc:** https://learn.microsoft.com/en-us/azure/databricks/jobs/powerbi
- **How it helps:** Lets the audit agent (running as a Databricks job) trigger/refresh Power BI semantic models natively via UC connection — a clean, governed way to drive Power BI from Databricks without hand-rolling REST/MSAL.

### B7. Configure service principals on Azure Databricks for Power BI (M2M OAuth)
- **TITLE:** Configure service principals on Azure Databricks for Power BI
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-m2m
- **Summary:** Steps to create a Databricks SP, set up an OAuth client secret, grant **CAN USE** on the SQL warehouse. Used by B4/B5/B6 for unattended auth.
- **How it helps:** The unattended-auth pattern for an audit agent that connects PBI→Databricks without a human in the loop.

### B8. ODBC / JDBC / ADBC drivers for Databricks SQL
- **TITLE:** Databricks ODBC Driver / Databricks JDBC Driver (Simba) / ADBC for Power BI
- **URLs:**
  - ODBC overview: https://learn.microsoft.com/en-us/azure/databricks/integrations/odbc/ ; download: https://learn.microsoft.com/en-us/azure/databricks/integrations/odbc/download
  - JDBC (Simba) overview: https://learn.microsoft.com/en-us/azure/databricks/integrations/jdbc/ ; download: https://learn.microsoft.com/en-us/azure/databricks/integrations/jdbc/download
  - Configure ADBC or ODBC driver for Power BI: https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-adbc
- **Summary:** As of **Feb 2026** the renamed **Databricks ODBC Driver** supersedes the deprecated Simba Spark ODBC Driver. The **Databricks JDBC Driver** (v3+) supersedes the legacy Simba JDBC (adds UC metric views, multi-statement transactions, stored procedures, faster large-result retrieval, built-in client telemetry). JDBC is a `.jar` (no install). Power BI connector can switch ODBC→**ADBC** (Arrow Database Connectivity).
- **How it helps:** These are the drivers any external BI/ETL tool (or the agent itself from outside Databricks) uses to query Databricks SQL warehouses; relevant if the agent ever connects to a warehouse over JDBC/ODBC for SQL-based collection.

---

## PART C — Networking & egress (reaching the Microsoft + Anthropic APIs)

> The agent must reach: `api.powerbi.com`, `api.fabric.microsoft.com`, `*.kusto.fabric.microsoft.com`, `management.azure.com`, `login.microsoftonline.com`, `onelake.dfs.fabric.microsoft.com`, `api.anthropic.com`. How that's allowed depends on **classic vs serverless** compute.

### C1. Serverless compute plane networking (overview, service tag, NAT/stable IPs)
- **TITLE:** Serverless compute plane networking — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/
- **Summary:** Serverless runs in the Databricks-managed serverless plane. Control-plane↔serverless traffic always over the Azure backbone, never public internet. **NCCs** (Network Connectivity Configurations) are account-level, regional constructs managing private-endpoint creation. Without a private endpoint, serverless reaches Azure **storage via service endpoints** and **other resources via NAT IPs**. Egress traffic to Azure storage is identifiable by the **`AzureDatabricksServerless`** service tag, regionally scoped (e.g. **`AzureDatabricksServerless.EastUS2`**).
- **CRITICAL DEADLINE:** By **June 9, 2026**, any Azure storage account that allowlists Databricks serverless **subnet IDs** must be onboarded to a **network security perimeter (NSP)** and allowlist the **`AzureDatabricksServerless`** service tag.
- **How it helps:** Establishes that for **serverless** the agent's outbound to Azure storage/resources is governed by NCC + service tag, and identifies the stable-identity mechanism (service tag) to allowlist on Fabric/ADLS firewalls.

### C2. Manage network policies for serverless egress control — KEY for api.anthropic.com & public APIs
- **TITLE:** Manage network policies for serverless egress control — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies
- **Summary:** Account-level **network policies** control serverless outbound. Two modes: **"Allow access to all destinations"** (full/unrestricted internet) vs **"Restricted access to specific destinations"**. In restricted mode you add **Allowed domains (FQDN)** and **Allowed storage destinations**. A **default policy** applies to all Premium-tier workspaces with no explicit assignment. **Dry-run mode** logs (not blocks) violations (per-product: Databricks SQL, AI model serving, All products). Denials land in **`system.access.outbound_network`** (values `DROP` vs `DRY_RUN_DENIAL`).
- **Exact identifiers / limits:**
  - Account console → **Security → Networking → Policies → Context-based ingress & egress control**.
  - Max **2500 destinations**; **≤100 FQDNs** per policy.
  - FQDN filter allows all domains sharing the same IP.
  - **Direct cloud-storage access from user code (REPLs/UDFs) is blocked by default** — must add the storage FQDN under Allowed Domains (don't add just the base domain — grants region-wide).
  - Must **restart compute** when changing internet-access mode or dry-run mode; other changes propagate in ~10 min (some up to 24 h).
  - Bundles UI deps to allowlist if restricted: `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `checkpoint-api.hashicorp.com`, `releases.hashicorp.com`, `registry.terraform.io`.
  - Model-serving provisioned-throughput endpoints don't support granular FQDN filtering (restricted = all internet blocked).
  - API: `account/networkpolicies/updatenetworkpolicyrpc` (egress `network_access.allowed_internet_destinations` / `allowed_storage_destinations`).
- **How it helps:** **This is the single most important control for the agent's outbound calls.** To call `api.anthropic.com`, `api.powerbi.com`, `api.fabric.microsoft.com`, `*.kusto.fabric.microsoft.com`, `management.azure.com`, `login.microsoftonline.com`, and `onelake.dfs.fabric.microsoft.com` from **serverless** under a restricted policy, each FQDN must be added to **Allowed domains** (watch the ≤100 FQDN cap). If pip installs are needed (e.g. `anthropic`, `msal`, `deltalake`), also allowlist `pypi.org` + `files.pythonhosted.org`. Use dry-run + `system.access.outbound_network` to discover exactly which domains the agent hits before enforcing.

### C3. Serverless firewall configuration / what is serverless egress control
- **TITLE:** Serverless compute firewall configuration / What is serverless egress control?
- **URLs:**
  - https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies
  - https://docs.databricks.com/aws/en/security/network/serverless-network-security/serverless-firewall-config
- **Summary:** Conceptual companion to C2 — explains the policy model, that restricting egress requires explicit allowlisting of every external destination, and that resources behind firewalls must allow serverless outbound IPs/service tags.
- **How it helps:** Background for designing the agent's allowlist; confirms the "deny by default in restricted mode" posture.

### C4. Configure private connectivity to Azure resources (outbound Private Link via NCC)
- **TITLE:** Configure private connectivity to Azure resources — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/serverless-private-link
- **Summary:** Outbound **Private Link** from serverless to your Azure resources via **NCC private-endpoint rules**. Supported from SQL warehouses, jobs, notebooks, Lakeflow pipelines, model serving. You create the NCC, attach to workspace, add private-endpoint rules (resource ID + **subresource ID**), then **approve on the resource** (status `PENDING`→`ESTABLISHED`).
- **Exact identifiers / limits:**
  - **Premium** account+workspace required; account admin.
  - **≤10 NCCs per region**; **100 private endpoints per region**; **≤50 workspaces per NCC**.
  - Storage subresources: create PE for **`blob`** (model artifacts) and **`dfs`** (log models in UC from serverless notebooks).
  - App Gateway v2 needs the **REST API** (resource_id + group_id + domain_names), not the UI: `POST .../network-connectivity-configs/<NCC_ID>/private-endpoint-rules`.
  - API: `account/networkconnectivity` (NCC API).
- **How it helps:** For private/locked-down deployments, this is how serverless privately reaches the **storage backing OneLake/ADLS and Databricks workspace storage** (use `dfs`+`blob` subresources). Note Microsoft control-plane SaaS endpoints (`api.powerbi.com`, `api.fabric.microsoft.com`) are reached via egress allowlist (C2)/NSP, not customer private endpoints; private endpoints are for your storage/data resources and App Gateway-fronted services.

### C5. Configure an Azure network security perimeter (NSP) for Azure resources — service-tag firewalling
- **TITLE:** Configure an Azure network security perimeter for Azure resources — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/serverless-nsp-firewall
- **Summary:** NSP is an Azure-native isolation boundary. Associate storage/key-vault/DB with an NSP profile and add an **inbound rule** with Source Type **Service Tag = `AzureDatabricksServerless.<region>`** (or global `AzureDatabricksServerless`). Tag includes service-endpoint IPs + Databricks NAT IPs; traffic stays on the Azure backbone (no data-processing charges). Supported only for **Azure Storage (incl. ADLS Gen2) in the workspace region**. Use **transition mode** (recommended indefinitely; falls back to existing firewall rules) vs enforced mode.
- **Exact identifiers:** Service tags `AzureDatabricksServerless` (global) and `AzureDatabricksServerless.EastUS2` (regional). Resource type filter `Microsoft.Storage/storageAccounts`. Test: `SELECT * FROM delta.\`abfss://container@storageaccount.dfs.core.windows.net/path\` LIMIT 10;`. Diagnostic logs: StorageRead/StorageWrite.
- **How it helps:** This is the **June 9 2026-mandated** way to let serverless reach firewalled ADLS/OneLake-backing storage without managing IP lists — the agent's storage reads (OneLake-as-ADLS, mirrored storage) survive storage firewalls by allowlisting the service tag.

### C6. Classic compute plane networking — SCC, VNet injection, stable egress IPs
- **TITLE:** Enable secure cluster connectivity / Deploy in your VNet (VNet injection) / Classic compute plane networking
- **URLs:**
  - https://learn.microsoft.com/en-us/azure/databricks/security/network/classic/secure-cluster-connectivity
  - https://learn.microsoft.com/en-us/azure/databricks/security/network/classic/vnet-inject
  - https://learn.microsoft.com/en-us/azure/databricks/security/network/classic/
- **Summary:** Classic clusters run in **your** VNet. With **Secure Cluster Connectivity (SCC / No-Public-IP)** Databricks auto-creates a **NAT gateway** for outbound. For **VNet injection** with a **stable egress public IP**, Databricks recommends an **Azure NAT gateway on both subnets** (or a custom egress like firewall/UDR).
- **CRITICAL DEADLINE:** After **March 31, 2026**, new Azure VNets default to **private (no outbound internet)**; new Databricks workspaces require an **explicit outbound method (e.g. NAT Gateway)**.
- **How it helps:** For **classic** compute, outbound to all the Microsoft + Anthropic APIs is governed by your VNet's egress (NAT gateway / Azure Firewall / UDR) — you control the firewall rules and get a **stable egress IP** to allowlist on `api.powerbi.com` / Fabric / storage firewalls. This is the alternative to serverless network policies, and the agent's reachability depends on which compute it runs on.

### C7. Configure private connectivity to resources in your VNet (serverless → internal network)
- **TITLE:** Configure private connectivity to resources in your VNet — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/pl-to-internal-network
- **Summary:** Private Link from serverless to a load balancer fronting your VNet/internal resources; such Private Link domains are **implicitly allowlisted** in network policies (removal can take up to 24 h to enforce).
- **How it helps:** Path for the agent to reach internal/on-prem resources (e.g. a private gateway to Fabric/Kusto) privately from serverless.

### C8. Manage private endpoint rules (supported subresources)
- **TITLE:** Manage private endpoint rules — Azure Databricks
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-private-endpoint-rules
- **Summary:** Lists supported target resource types/subresource IDs for NCC private endpoints and how to add/list/delete rules.
- **How it helps:** Reference for exactly which Azure resources (storage `dfs`/`blob`, etc.) the agent's serverless compute can privately reach.

### C9. account network-connectivity CLI command group
- **TITLE:** account network-connectivity command group
- **URLs:** https://docs.databricks.com/aws/en/dev-tools/cli/reference/account-network-connectivity-commands (Azure: `databricks account network-connectivity` via NCC API https://learn.microsoft.com/en-us/rest/api/databricks ... NCC API ref linked from C1/C4)
- **Summary:** CLI/API to create NCCs, private-endpoint rules, and manage egress programmatically.
- **How it helps:** Lets the agent's IaC/bootstrap (Asset Bundle adjacent) provision the NCC + private endpoints + service-tag allowlisting reproducibly.

### C10. IP addresses and domains for Databricks services
- **TITLE:** IP addresses and domains for Databricks services and assets
- **URL:** https://docs.databricks.com/aws/en/resources/ip-domain-region (Azure regional control-plane IPs: https://learn.microsoft.com/en-us/azure/databricks/resources/supported-regions)
- **Summary:** Reference for Databricks control-plane IPs/domains per region and the `AzureDatabricksServerless` regional tags; supported regions list.
- **How it helps:** Source for the exact IPs/regions to allowlist on the agent's side and on resource firewalls.

---

## How the pieces fit (decision guide for the agent)

- **To READ Fabric data from Databricks (read-only):** Prefer **OneLake catalog federation** (A2) — governed, read-only, no copy, SQL-native. For ad-hoc/serverless file reads use **MSAL + `deltalake`** against `onelake.dfs.fabric.microsoft.com` (A1/A3). For firewalled storage use **managed identity** (A5) + **NSP service tag** (C5) or **NCC private endpoints** `dfs`+`blob` (C4).
- **To PUSH to / audit Power BI:** Use **UC Power BI connection + Power BI task** (B6) for governed orchestration, or **Publish to Power BI** (B5). Driver-level access via **Databricks ODBC/JDBC/ADBC** (B8).
- **To REACH the SaaS APIs** (`api.powerbi.com`, `api.fabric.microsoft.com`, `*.kusto.fabric.microsoft.com`, `management.azure.com`, `login.microsoftonline.com`, `api.anthropic.com`):
  - **Serverless:** add every FQDN to the **network policy Allowed domains** (C2), restart compute, verify via `system.access.outbound_network`. (≤100 FQDNs; add `pypi.org`/`files.pythonhosted.org` for installs.)
  - **Classic:** route outbound through your **VNet NAT gateway / Azure Firewall** (C6) and open those FQDNs; you get a stable egress IP to allowlist on the resource side.
- **Mind the deadlines:** storage-firewall serverless allowlisting → **NSP + service tag by June 9, 2026** (C1/C5); new VNets private-by-default → **NAT gateway required after March 31, 2026** (C6).

---

## Flat URL list

1. https://learn.microsoft.com/en-us/fabric/onelake/onelake-azure-databricks
2. https://learn.microsoft.com/en-us/azure/databricks/query-federation/onelake
3. https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api
4. https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts
5. https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities
6. https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/external-locations-adls
7. https://learn.microsoft.com/en-us/fabric/mirroring/azure-databricks
8. https://learn.microsoft.com/en-us/fabric/mirroring/azure-databricks-tutorial
9. https://learn.microsoft.com/en-us/fabric/mirroring/azure-databricks-security
10. https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-open-api
11. https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi
12. https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-desktop
13. https://learn.microsoft.com/en-us/power-query/connectors/databricks-azure
14. https://learn.microsoft.com/en-us/power-query/connectors/databricks
15. https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-service
16. https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-uc-connect
17. https://learn.microsoft.com/en-us/azure/databricks/jobs/powerbi
18. https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-m2m
19. https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-adbc
20. https://learn.microsoft.com/en-us/azure/databricks/integrations/odbc/
21. https://learn.microsoft.com/en-us/azure/databricks/integrations/odbc/download
22. https://learn.microsoft.com/en-us/azure/databricks/integrations/jdbc/
23. https://learn.microsoft.com/en-us/azure/databricks/integrations/jdbc/download
24. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/
25. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies
26. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies
27. https://docs.databricks.com/aws/en/security/network/serverless-network-security/serverless-firewall-config
28. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/serverless-private-link
29. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/serverless-nsp-firewall
30. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/pl-to-internal-network
31. https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-private-endpoint-rules
32. https://learn.microsoft.com/en-us/azure/databricks/security/network/classic/secure-cluster-connectivity
33. https://learn.microsoft.com/en-us/azure/databricks/security/network/classic/vnet-inject
34. https://learn.microsoft.com/en-us/azure/databricks/security/network/classic/
35. https://docs.databricks.com/aws/en/dev-tools/cli/reference/account-network-connectivity-commands
36. https://docs.databricks.com/aws/en/resources/ip-domain-region
37. https://learn.microsoft.com/en-us/azure/databricks/resources/supported-regions
38. https://learn.microsoft.com/en-us/azure/databricks/security/network/storage/firewall-support
</content>
</invoke>

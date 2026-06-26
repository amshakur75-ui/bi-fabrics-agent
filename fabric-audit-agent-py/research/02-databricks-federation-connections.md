# 02 — Databricks Lakehouse Federation & External Connections

Research focus for **bi-fabrics-audit-agent**: a READ-ONLY Microsoft Fabric / Power BI capacity audit agent running in Databricks (Unity Catalog storage; MCP + agent; Teams). This document covers how Databricks Lakehouse Federation and external connections could let the agent **pull Fabric / Power BI-adjacent data directly into Databricks without copying it** — using Unity Catalog governance, query pushdown, and read-only foreign catalogs.

**Date researched:** 2026-06-22. Docs change fast; versions/identifiers quoted verbatim from the pages on this date.

---

## TL;DR — the load-bearing findings for the audit agent

1. **Databricks CAN federate to Microsoft Fabric — officially — via `CREATE CONNECTION ... TYPE onelake` (OneLake catalog federation).** This is GA-documented (Microsoft Learn, updated 2026-06-17). It gives Unity Catalog read-only foreign catalogs over **Fabric Lakehouse and Warehouse** items, with table-level governance, no data copy, and SP / managed-identity auth (incl. cross-tenant). This is the single most important option for the agent. Requires **DBR 18.0+ / SQL warehouse 2025.40+** and several Fabric tenant settings.
2. **The Fabric SQL endpoint can ALSO be reached two other ways:** (b) plain Spark JDBC with the mssql driver (works, ungoverned), and (c) a generic `TYPE sqlserver` federation connection pointed at `*.datawarehouse.fabric.microsoft.com:1433` (community-validated, **not officially listed**, experimental).
3. **A Unity Catalog *external location* directly on `onelake.dfs.fabric.microsoft.com` is NOT supported** ("onelake urls are not supported as external locations"). Governed OneLake access must go through `TYPE onelake` catalog federation; raw abfss Delta reads are possible but ungoverned (cluster Spark config / MSAL token).
4. **Power BI semantic models cannot be federated.** There is **no `powerbi` / Analysis Services / XMLA connection TYPE.** The XMLA endpoint is OLE DB/.NET (MSOLAP/ADOMD) only — no JDBC path from Databricks. The only way to query a semantic model from Databricks is **custom code** (Power BI REST `executeQueries` DAX — already covered in your prior research bucket).
5. **All Lakehouse Federation queries are read-only** (perfect fit for a read-only audit agent). Pushdown (filters, projections, limit, aggregates, sort, and joins for several sources) minimizes data movement. Result/disk cache is not used for federated queries.

---

## Concept primer — query federation vs catalog federation

**TITLE:** Connect to external databases and catalogs — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/

- **Lakehouse Federation** = Databricks' query-federation platform. Governed, **read-only** access to external data via Unity Catalog **foreign catalogs**, with automatic query pushdown and table-level access controls. Two flavors:
  - **Query federation** — Unity Catalog queries are **pushed down to the foreign DB over JDBC**; query runs on both Databricks and the remote engine. Best for "ad hoc reporting, BI, and proof-of-concept access to operational databases." Write: not supported.
  - **Catalog federation** — Unity Catalog queries run **directly against object storage** on Databricks compute only (more cost-effective/performant). For incremental UC migration or long-term hybrid. Write: not supported. Used by Hive metastore, Glue, Snowflake-Iceberg, and **OneLake**.
- A **connection** is a securable object in Unity Catalog that stores a path + credentials for an external system. A **foreign catalog** is a read-only mirror of a remote database/item, queried with three-part `catalog.schema.table` naming.
- Generic creation primitives: `CREATE CONNECTION`, `CREATE FOREIGN CATALOG ... USING CONNECTION`, plus `SHOW CONNECTIONS`, `DESCRIBE CONNECTION`, `DROP CONNECTION`.
- **Spark Data Source API** is the fallback when Federation doesn't support a source, when you need write access, or for parallelization control (bundled JDBC connectors for PostgreSQL, SQL Server, MySQL, Snowflake, Redshift; bring-your-own-driver via JDBC UC connection; custom PySpark DataSource).

**How it helps the audit agent:** This is the governance backbone. Every Fabric/PBI-adjacent source the agent reaches can be wrapped as a read-only foreign catalog under Unity Catalog, so the agent's SELECTs are governed (SELECT/USE CATALOG/USE SCHEMA), auditable, and never mutate the source.

---

## ★ OneLake catalog federation — Databricks federates to Microsoft Fabric (THE key finding)

**TITLE:** Enable OneLake catalog federation — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/onelake
**Status:** Official Microsoft Learn how-to, `ms.date: 2026-06-17`.

**What it is:** "OneLake federation enables you to analyze data stored in your Lakehouse or Warehouse without copying it... Data access is read-only." Unity Catalog queries run directly against OneLake storage.

**Supported Fabric data items:** **Fabric Lakehouse** and **Fabric Warehouse** (only these two).

### Prerequisites / requirements (verbatim)
- Workspace enabled for Unity Catalog.
- Compute: **Databricks Runtime 18.0 or above** and **standard access mode**. (Dedicated access mode is **not supported**.)
- **SQL warehouses must use 2025.40 or above.**
- Network connectivity from compute to the target.
- Supported auth: **Azure Managed Identity via an Access Connector for Azure Databricks**, or **Azure service principal** (SP supports **cross-tenant** — Databricks in one tenant, Fabric in another).

### Fabric tenant / workspace settings a Fabric admin must enable
- **Service principals can use Fabric APIs** (tenant setting).
- **Allow apps running outside of Fabric to access data via OneLake** (tenant setting).
- **Use short-lived user-delegated SAS tokens** (tenant setting) — OneLake issues short-lived Entra-backed SAS tokens that Databricks uses to read.
- In the Fabric workspace: **Authenticate with OneLake user-delegated SAS tokens** (Workspace settings > Delegated settings > OneLake settings).

### Permissions (Databricks side, verbatim)
- Create connection: **metastore admin** or a user with **`CREATE CONNECTION`** and **`CREATE STORAGE CREDENTIAL`** privileges on the metastore.
- Create foreign catalog: **`CREATE CATALOG`** on the metastore AND (connection owner OR **`CREATE FOREIGN CATALOG`** on the connection).
- Query: users need **`USE CATALOG`** + **`USE SCHEMA`** + **`SELECT`** on the federated table.

### Fabric side (Step 2)
- In Fabric workspace > Workspace settings > **Manage access** > Add the managed identity or SP; assign **Member** role at minimum (Contributor/Admin also fine). Item-level permissions inherit from the workspace role.

### Exact SQL — connection (Step 4)
```sql
CREATE CONNECTION <connection-name> TYPE onelake
OPTIONS (
  workspace '<workspace-id>',
  credential '<storage-credential-name>'
);
```
- `<workspace-id>` = GUID of the OneLake/Fabric workspace.
- `<storage-credential-name>` = a Unity Catalog **storage credential** created in Step 3 referencing the managed identity (Access Connector resource ID) or SP.
- **You cannot update connection options (e.g. `workspace`) after creating the connection.**

### Storage credential (Step 3) — SP variant requires the API (verbatim)
Managed identity: create via Catalog Explorer (Credential Type **Azure Managed Identity**, supply Access Connector **Resource ID**: `/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/<connector-name>`).
Service principal: **cannot** be created in Catalog Explorer — must be an account admin using the Storage Credentials API:
```bash
curl -X POST -n https://<databricks-instance>/api/2.1/unity-catalog/storage-credentials \
-d '{
   "name": "<storage-credential-name>",
   "read_only": true,
   "azure_service_principal": {
      "directory_id": "<directory-id>",
      "application_id": "<application-id>",
      "client_secret": "<client-secret>"
   },
   "skip_validation": "false"
}'
```
(Note `"read_only": true` — well-aligned with the audit agent's read-only posture.)

### Exact SQL — foreign catalog (Step 5)
```sql
CREATE FOREIGN CATALOG [IF NOT EXISTS] <catalog-name> USING CONNECTION <connection-name>
OPTIONS (
  data_item '<data-item-id>',
  item_type '<item-type>'
);
```
- `data_item` = GUID of the Fabric Lakehouse or Warehouse (find in Fabric UI or browser URL: `https://app.fabric.microsoft.com/groups/<workspace-id>/lakehouses/<data-item-id>?experience=power-bi`).
- `item_type` = `Lakehouse` or `Warehouse`.
- Optional `create_volume_for_lakehouse_files 'true'|'false'` (default `true`, Lakehouse only): creates a UC volume over the Lakehouse `/Files` folder under the `onelake-folders` schema → read-only access to unstructured files.
- "The catalog syncs automatically, making the Fabric tables available immediately."

### Reading unstructured files (Lakehouse /Files via volume)
```sql
LIST '/Volumes/<catalog-name>/onelake-folders/files/';
SELECT * FROM parquet.`/Volumes/<catalog-name>/onelake-folders/files/table.parquet`;
```
```python
df = spark.read.format("binaryFile").load("/Volumes/<catalog-name>/onelake-folders/files/")
```

### Query (three-part naming)
```sql
SELECT COUNT(*) FROM fabric_sales.silver.customer_details;
```

### Limitations (verbatim)
- **Read-only** (SELECT only; no writes).
- Auth limited to **Azure Managed Identity** or **Azure SP**.
- Only **Fabric Lakehouse and Warehouse** items.
- **Dedicated access mode not supported.**
- **Complex datatypes (arrays, maps, structs) not supported.**
- **Materialized views and views not supported.**
- Cannot update connection options (e.g. `workspace`) after creation.
- Tables with case-only-differing columns (`id` vs `ID`) not supported.

**How it helps the audit agent:** This is the cleanest, officially-supported, governed path to pull Fabric Lakehouse/Warehouse **table data** straight into Databricks Unity Catalog as read-only foreign catalogs — no copy, table-level RBAC, SP/cross-tenant auth. The agent can `SELECT` Fabric warehouse tables (e.g. operational/usage tables a customer materializes in Fabric) as if they were UC tables, then reason over them with the MCP/agent. Caveat: it needs DBR 18.0+/SQL 2025.40+ and Fabric-admin tenant settings — record these as deployment prerequisites. Views/MVs not federatable means the agent can only see base tables; structured files in `/Files` are reachable via the auto-created volume.

---

## Microsoft SQL Server (also Azure SQL Database & Azure SQL Managed Instance)

**TITLE:** Run federated queries on Microsoft SQL Server — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/sql-server
**Scope (verbatim):** "Lakehouse Federation supports SQL Server, Azure SQL Database, and Azure SQL Managed Instance." (All use `TYPE sqlserver`; Azure Synapse is separate — `sqldw`.)

**Connection (TYPE `sqlserver`):**
```sql
CREATE CONNECTION <connection-name> TYPE sqlserver
OPTIONS ( host '<hostname>', port '<port>', user '<user>', password '<password>' );
```
Secrets variant: `user secret ('<scope>','<key>')`, `password secret ('<scope>','<key>')`.

**OPTIONS:** `host`, `port`, `user`, `password`; UI adds `trustServerCertificate` (default `false`), **Application intent**.
**Auth types (UI selector):** **OAuth**, **OAuth Machine to Machine**, or **Username and password**. Microsoft Entra ID / OAuth configured via the companion page **Configure Microsoft Entra ID for SQL Server federation** (`query-federation/sql-server-entra`) — that page holds the exact OAuth SP/client option names (not fetched here). Note: "The Azure Entra ID OAuth endpoint must be accessible from Azure Databricks control plane IPs."

**Foreign catalog:**
```sql
CREATE FOREIGN CATALOG [IF NOT EXISTS] <catalog-name> USING CONNECTION <connection-name>
OPTIONS (database '<database-name>');
```

**Runtime/permissions:** DBR **13.3 LTS+**, Standard/Dedicated; SQL warehouse pro/serverless **2023.40+**; `CREATE CONNECTION` + `CREATE CATALOG`/`CREATE FOREIGN CATALOG`.

**Pushdowns:** filters, limit, projections, aggregates, sorting (w/ limit), arithmetic/boolean/misc operators, partial date/string/math functions; **Joins** = DBR 17.2+ / SQL warehouse, Public Preview (toggle required). **Windows functions: not supported.**

**How it helps the audit agent:** This is the connector that the **Fabric SQL analytics endpoint / Warehouse** *could* be pointed at (see the Fabric SQL-endpoint section below) because the endpoint speaks TDS on port 1433 with Entra/SP OAuth — which maps to this connector's OAuth/M2M auth. Also the direct path for any Azure SQL DB the customer uses to stage capacity/usage data.

---

## Azure Synapse (SQL Data Warehouse) — `sqldw`

**TITLE:** Run federated queries on Azure Synapse (SQL Data Warehouse) — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/sqldw

**Connection (TYPE `sqldw`):**
```sql
CREATE CONNECTION <connection-name> TYPE sqldw
OPTIONS ( host '<hostname>', port '<port>', user '<user>', password '<password>' );
```
Secrets variant supported (`user secret(...)`, `password secret(...)`).
**OPTIONS:** `host`, `port`, `user`, `password`; UI **Trust server certificate** (default off). Example host `*.database.windows.net`, port `1433`.
**Auth:** **username/password only** on this page (no OAuth/Entra/SP documented for Synapse federation).
**Foreign catalog:** `OPTIONS (database '<database-name>')`.
**Runtime/permissions:** same as SQL Server (DBR 13.3 LTS+, SQLW 2023.40+).
**Pushdowns:** filters, projections, limit, aggregates (Avg/Count/Max/Min/StddevPop/StddevSamp/Sum/VarianceSamp), arithmetic+misc functions (Alias/Cast/SortOrder), sorting. **Joins: not supported. Windows: not supported.**

**How it helps the audit agent:** If a customer parks Fabric/PBI usage exports or capacity logs in Synapse, the agent reads them as a governed read-only catalog. Note the weaker auth story (basic auth only) and no join pushdown.

---

## Snowflake (query federation + catalog/Iceberg federation)

**TITLE:** Run federated queries on Snowflake — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake
**Catalog (Iceberg) federation:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-catalog-federation

**Query vs catalog (verbatim):** Query federation = JDBC pushdown (on-demand reporting/PoC). Catalog federation = UC reads **Snowflake Managed Iceberg tables directly from cloud storage** (cost-effective; "Non-Iceberg Snowflake tables are not eligible for catalog federation and are always accessed using query federation").

**Connection (TYPE `snowflake`) — basic auth:**
```sql
CREATE CONNECTION <connection-name> TYPE snowflake
OPTIONS ( host '<hostname>', port '<port>', sfWarehouse '<warehouse-name>', user '<user>', password '<password>' );
```
**PEM key-pair auth:** add `pem_private_key '<key>'`, `expires_in_secs '<sec>'` (secrets-supported for `pem_private_key`).
**OPTIONS:** `host`, `port`, `sfWarehouse`, `user`, `password`, `pem_private_key`, `expires_in_secs`.
**Auth methods (separate doc pages):** Built-in OAuth (`/snowflake`), OAuth w/ Microsoft Entra ID (`/snowflake-entra`), OAuth w/ Okta (`/snowflake-okta`), OAuth access token (`/snowflake-oauth-access-token`), PEM private key (`/snowflake-pem`), Basic auth (`/snowflake-basic-auth`). Built-in OAuth uses a Snowflake `CREATE SECURITY INTEGRATION ... TYPE=oauth` with `OAUTH_REDIRECT_URI = 'https://<workspace-url>/login/oauth/snowflake.html'`. (No PAT method; the token method is "OAuth Access Token".)
**Foreign catalog:** `OPTIONS (database '<database-name>')` (case-sensitivity rules: bare → uppercased; quoted → preserved).
**Catalog/Iceberg federation:** reuses the same connection + auth; additionally needs a UC **storage credential + external location**, plus foreign-catalog **Authorized paths** and **Storage location**; Iceberg supported schemes `s3/s3a/s3n/abfs/abfss/gs/r2/wasb/wasbs`; users need `MODIFY` on UC Iceberg federated tables. Falls back to JDBC query federation if direct-access criteria fail.
**Runtime:** DBR 13.3 LTS+ (16.4 LTS+ / SQLW 2025.16+ for catalog federation).
**Pushdowns:** filters, projections, limit, **joins (GA, on by default)**, broad aggregates, **windows (DenseRank/Rank/RowNumber)**, functions, sorting. Tunable `partition_size_in_mb` for parallel reads.

**How it helps the audit agent:** Not Fabric/PBI-specific, but if a customer's BI estate spans Snowflake (e.g. semantic-layer source tables behind Power BI Import/DirectQuery), the agent can read them read-only and correlate with Fabric capacity findings. Strongest pushdown profile (joins + windows GA).

---

## PostgreSQL — `postgresql`

**TITLE:** Run federated queries on PostgreSQL — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/postgresql
```sql
CREATE CONNECTION <connection-name> TYPE postgresql
OPTIONS ( host '<hostname>', port '<port>', user '<user>', password '<password>' );
```
Secrets variant supported. **OPTIONS:** `host`, `port`, `user`, `password` (username/password only). **Foreign catalog:** `OPTIONS (database '<database-name>')`. **Pushdowns:** filters, limit, projections, aggregates, arithmetic/boolean/misc operators, partial functions; **Joins** = DBR 17.2+ Public Preview; **Windows: not supported.**

**How it helps the audit agent:** Reads any Postgres source feeding the BI estate (e.g. metadata/usage exports) as a governed catalog.

---

## MySQL — `mysql`

**TITLE:** Run federated queries on MySQL — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/mysql
"SSL is required to create a connection."
```sql
CREATE CONNECTION <connection-name> TYPE mysql
OPTIONS ( host '<hostname>', port '<port>', user '<user>', password '<password>' );
```
Secrets variant supported. **Foreign catalog:** optional `OPTIONS (tinyInt1isBit {'true'|'false'})`. **Pushdowns:** filters, limit, **offset**, projections, aggregates, arithmetic/boolean operators, bitwise AND (`&`), sorting; **Joins** = DBR 17.2+ Public Preview; **Windows: not supported.**

---

## Amazon Redshift — `redshift`

**TITLE:** Run federated queries on Amazon Redshift — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/redshift
```sql
CREATE CONNECTION <connection-name> TYPE redshift
OPTIONS ( host '<hostname>', port '<port>', user '<user>', password '<password>' );
```
Secrets variant supported (username/password only; UI "Disable SSL hostname verification"). **Foreign catalog:** `OPTIONS (database '<database-name>')`. **Pushdowns:** filters, projections, limit, **joins (GA, on by default)**, aggregates, functions, sorting; **Windows: not supported.** Limitation: "You cannot run federated queries on Amazon Redshift external data."

---

## Google BigQuery — `bigquery`

**TITLE:** Run federated queries on Google BigQuery — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/bigquery
Uses the **BigQuery Storage API** (not JDBC); requires **DBR 16.1+**.
```sql
CREATE CONNECTION <connection-name> TYPE bigquery
OPTIONS ( GoogleServiceAccountKeyJson '<GoogleServiceAccountKeyJson>' );
```
Secrets variant: `GoogleServiceAccountKeyJson secret ('<scope>','<key>')`. **Auth:** service-account key JSON only; SA needs **BigQuery User** + **BigQuery Data Viewer**. **Foreign catalog options:** `dataProjectId`, `materializationDataset`, `bigNumericDefaultScale`. Materialization (`spark.databricks.bigquery.enableMaterialization=true`, not on SQL warehouses) enables limit/aggregate/join/sort pushdown (joins DBR 16.1+); views/external tables always materialized. **Windows: not supported.**

---

## Oracle — `oracle`

**TITLE:** Run federated queries on Oracle — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/oracle
```sql
CREATE CONNECTION <connection-name> TYPE oracle
OPTIONS ( host '<hostname>', port '<port>', user '<user>', password '<password>', encryption_protocol '<protocol>' );
```
Secrets variant supported. **OPTIONS:** `host`, `port`, `user`, `password`, optional `encryption_protocol` (Native Network Encryption default, or TLS for Oracle Cloud). **Foreign catalog:** `OPTIONS (service_name '<service-name>')`. **Runtime:** DBR **16.1+**. **Pushdowns:** aggregates, cast, contains/startswith/endswith, filters, limit, offset, projections; **Joins** = DBR 17.2+ Public Preview.

---

## Salesforce Data 360 (formerly Data Cloud) — `salesforce_data_cloud`

**TITLE:** Run federated queries on Salesforce Data 360 — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/salesforce-data-cloud
Two zero-copy connectors: **query federation** (JDBC pushdown) and **file sharing** (DaaS API direct object-storage read — `salesforce-data-cloud-file-sharing`, listed under *catalog* federation). Auth = OAuth via a Salesforce **Connected App** (Consumer key/secret; scopes `cdp_api api cdp_query_api refresh_token offline_access`).
```sql
CREATE CONNECTION '<Connection name>' TYPE salesforce_data_cloud
OPTIONS (
  client_id '<consumer key>',
  client_secret '<consumer secret>',
  pkce_verifier '<pkce_verifier>',
  authorization_code '<auth_code>',
  oauth_redirect_uri "https://login.salesforce.com/services/oauth2/success",
  oauth_scope "cdp_api api cdp_query_api refresh_token offline access",
  is_sandbox "false"
);
```
Secrets variant supported for `client_id`/`client_secret`. **Foreign catalog:** `OPTIONS (dataspace '<dataspace>')` (one data space per catalog). **Runtime:** DBR 15.2+. **Pushdowns:** filters, projections, limit, aggregates, offset, cast, contains/startswith/endswith.

---

## Teradata — `teradata`

Listed as a supported query-federation source on the overview page (URL: https://learn.microsoft.com/en-us/azure/databricks/query-federation/teradata, not separately fetched). Confirmed by the performance page: Teradata supports the JDBC `fetchSize`/parallel-read tuning and is in the **Join Pushdown Public Preview** list (DBR 17.2+). TYPE keyword: `teradata`.

---

## Databricks-to-Databricks federation — `databricks`

**TITLE:** Run federated queries on another Databricks workspace — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/databricks
**Important guidance (verbatim intent):** intended for federating another workspace's **Hive or AWS Glue metastore**. For shared-UC-metastore data use normal UC queries; for read-only cross-metastore use **Delta Sharing**, not federation.
```sql
CREATE CONNECTION <connection-name> TYPE databricks
OPTIONS ( host '<workspace-instance>', httpPath '<sql-warehouse-path>', personalAccessToken '<pat>' );
```
Secrets variant supported for `personalAccessToken`. **Auth:** PAT only (recommended: a **service principal's** PAT). **Foreign catalog:** `OPTIONS (catalog '<external-catalog-name>')` (note `catalog`, not `database`). **Pushdowns:** filters, projections, limit, aggregates, sorting; **Joins/Windows: not supported.**

**How it helps the audit agent:** If the audit agent's UC metastore is separate from a customer's Databricks workspace that already ingests Fabric/PBI telemetry, this (or Delta Sharing) lets it read those tables read-only.

---

## Managing connections — generic lifecycle + permissions

**TITLE:** Manage connections for Lakehouse Federation — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/connections

- `SHOW CONNECTIONS [LIKE <pattern>];` — users with **`USE CONNECTION`** on the metastore see all; otherwise only owned/privileged ones.
- `DESCRIBE CONNECTION <name>;`
- `GRANT CREATE FOREIGN CATALOG ON CONNECTION <name> TO <user>;`
- `GRANT USE CONNECTION ON CONNECTION <name> TO <user>;` (`USE CONNECTION` also lets Lakeflow Spark Declarative Pipelines ingest; at metastore level it only allows *viewing* connection details).
- `REVOKE <privilege> ON CONNECTION <name> FROM <user>;`
- `DROP CONNECTION [IF EXISTS] <name>;` (owner only).
- REST API equivalents: `POST /api/2.1/unity-catalog/connections`, `POST /api/2.1/unity-catalog/catalogs`. Terraform: `databricks_storage_credential`, `databricks_connection`.

**Credential best practice (applies to every source):** use the **`secret('<scope>','<key>')`** function in OPTIONS instead of plaintext (exact syntax on the pages: `secret ('<secret-scope>','<secret-key>')`). Escape `$` as `\$` in plaintext SQL.

**How it helps the audit agent:** The agent (or its IaC/Asset Bundle) can enumerate, grant, and govern connections programmatically via SQL or REST; pairs with secrets so SP credentials for Fabric/SQL never sit in plaintext.

---

## Query pushdown & performance (read-only efficiency)

**TITLE:** Lakehouse Federation performance recommendations — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/query-federation/performance-recommendations

- **Predicate pushdown:** Databricks pushes filters to the remote engine to cut network rows. Non-pushable predicates (e.g. `ILIKE`, no MySQL translation) run locally, but a pushable part of an `AND` still pushes (e.g. `name ILIKE 'john' AND date > '2025-05-01'` → only the date filter is pushed).
- **Verify pushdown:** `EXPLAIN FORMATTED` shows `PushedFilters` / `PushedJoins` (actual query may differ due to AQE).
- **Batch fetch:** `WITH ('fetchSize' 100000)` for JDBC connectors (Databricks, SQL Server, Synapse, MySQL, Oracle, PostgreSQL, Salesforce, Teradata); DBR 16.1+/SQLW 2024.50.
- **Snowflake parallelism:** `WITH ('partition_size_in_mb' 1000)`.
- **Parallel JDBC reads:** `WITH ('numPartitions' 4, 'partitionColumn' 'id', 'lowerBound' 1, 'upperBound' 1000)` (DBR 17.1+/SQLW 2025.25). Parallel reads not supported on Databricks-created views over federated tables — create the view in the source DB instead.
- **Join pushdown (Public Preview):** sources = Oracle, PostgreSQL, MySQL, SQL Server, Teradata, Redshift, Snowflake, BigQuery. **GA + on by default for Redshift, Snowflake, BigQuery**; the others need DBR **17.2+** / SQLW **2025.30** + the **"Join Pushdown for Federated Queries"** Previews toggle. Only inner/left-outer/right-outer joins; below-join nodes limited to join/filter/sample/scan (no limit/offset/aggregate below a join; those are fine on top).
- **Caching:** Databricks Result Cache and Disk Cache are **not** used for federated queries (every run hits the source).

**How it helps the audit agent:** The agent should write SELECTs whose filters/aggregates push down (especially over Fabric warehouse tables) to minimize egress and Fabric-side load — important since this is a *capacity* audit and the agent must not itself become a capacity burden. Use `EXPLAIN FORMATTED` in development to confirm pushdown; set `fetchSize` for large scans.

---

## Can Databricks query Microsoft Fabric / Power BI directly? (investigated hard)

Four distinct mechanisms, with verdicts:

### (a) OneLake catalog federation — SUPPORTED ✅
`TYPE onelake` foreign catalogs over Fabric Lakehouse/Warehouse. See the dedicated section above. This is the recommended, governed, read-only, no-copy path. (Source: https://learn.microsoft.com/en-us/azure/databricks/query-federation/onelake)

### (b1) Fabric SQL endpoint via plain Spark JDBC — WORKS (ungoverned) ✅
**TITLE:** databricks-fabric-integrations / 01-sql-endpoint.md (Microsoft employee repo, memomsft)
**URL:** https://github.com/memomsft/databricks-fabric-integrations/blob/main/docs/01-sql-endpoint.md
- `spark.read.format("jdbc")` against the Fabric SQL analytics endpoint / Warehouse (TDS on port 1433). Driver `com.microsoft.sqlserver.jdbc.SQLServerDriver` (pre-installed). JDBC URL: `jdbc:sqlserver://{endpoint}:1433;databaseName={db};encrypt=true;trustServerCertificate=false;hostNameInCertificate=*.datawarehouse.fabric.microsoft.com;loginTimeout=30;authentication=ActiveDirectoryServicePrincipal`. Auth = `ActiveDirectoryServicePrincipal` (SP client ID + secret). Egress to `*.datawarehouse.fabric.microsoft.com:1433`.
- **Verdict: works.** Most reliable non-federation read of Fabric Warehouse/SQL-endpoint. Read-only, but NOT under UC governance unless wrapped.

### (b2) Fabric SQL endpoint as a `TYPE sqlserver` federation source — PARTIAL / UNDOCUMENTED ⚠️
**TITLE:** Running Federated Queries from Unity Catalog on Microsoft Fabric SQL Endpoint (Aitor Murguzur, Databricks)
**URL:** https://murggu.medium.com/running-federated-queries-from-unity-catalog-on-microsoft-fabric-sql-endpoint-1485da1d450b
- Creates a UC connection of **SQL Server type** at host `<id>.datawarehouse.fabric.microsoft.com`, port `1433`, Service Principal / OAuth 2.0 (authorize endpoint `https://login.microsoftonline.com/<tenant_id>/oauth2/v2.0/authorize`, scope `https://database.windows.net/.default offline_access`).
- Author caveats (verbatim): "I only validated that it worked; I did not test data mapping or pushdown support" and "Still official Fabric connection is not available... the connector may no longer function in the future."
- The `sqlserver` connector officially supports OAuth/M2M (Entra) auth, which *fits* the Fabric SP scenario — but **Microsoft Fabric is NOT in the supported-source list** (only "SQL Server, Azure SQL Database, Azure SQL Managed Instance"). 
- **Verdict: connects, but experimental/unsupported.** Prefer `TYPE onelake` for a governed path; this is a fallback for Warehouse-via-SQL-endpoint if OneLake federation isn't viable. (Sources: https://learn.microsoft.com/en-us/azure/databricks/query-federation/sql-server and the overview list https://learn.microsoft.com/en-us/azure/databricks/query-federation/)

### (c) Direct abfss OneLake Delta read (no federation) — WORKS, but ungoverned ✅ / UC external location NOT supported ❌
**TITLE:** Integrate OneLake with Azure Databricks — Microsoft Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/onelake/onelake-azure-databricks
- Standard cluster: set ABFS OAuth config (`fs.azure.account.auth.type=OAuth`, `fs.azure.account.oauth.provider.type=org.apache.hadoop.fs.azurebfs.oauth2.ClientCredsTokenProvider`, `fs.azure.account.oauth2.client.id/.client.secret/.client.endpoint=https://login.microsoftonline.com/{tenant}/oauth2/token`), then `spark.read.format("delta"/"parquet").load("abfss://<ws>@onelake.dfs.fabric.microsoft.com/<lakehouse>.lakehouse/Tables/<path>")`. SP needs Contributor on the Fabric workspace.
- Serverless: can't set `fs.azure.*` (`CONFIG_NOT_AVAILABLE`); use MSAL `ConfidentialClientApplication` (scope `https://onelake.fabric.microsoft.com/.default`) + Python `deltalake` with `storage_options={"bearer_token":..., "use_fabric_endpoint":"true"}`.
- **UC external location to OneLake is NOT supported.** Community thread reports: "onelake urls are not supported as external locations."
  - https://community.databricks.com/t5/data-engineering/azure-databricks-create-an-external-location-to-microsoft-fabric/td-p/87826
  - https://community.databricks.com/t5/get-started-discussions/connect-to-onelake-using-service-principal-unity-catalog-and/td-p/110503
- **Verdict:** direct Delta read works for raw Lakehouse files but bypasses UC governance; for *governed* OneLake access use `TYPE onelake` catalog federation, not external locations.

### (d) Power BI semantic model / dataset querying — NOT supported natively ❌
- **No `powerbi` / Analysis Services / XMLA connection TYPE exists.** The full federation source list (query: MySQL, PostgreSQL, Teradata, Oracle, Redshift, Salesforce Data 360, Snowflake, SQL Server, Azure Synapse, BigQuery, Databricks; catalog: Hive metastore, Salesforce, Snowflake, OneLake) contains nothing for Power BI semantic models. (Source: https://learn.microsoft.com/en-us/azure/databricks/query-federation/)
- **TITLE:** Semantic model connectivity and management with the XMLA endpoint — Power BI
  **URL:** https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-connect-tools
  - XMLA endpoint (`powerbi://api.powerbi.com/v1.0/myorg/<Workspace>`) speaks DAX/MDX/TMSL over Analysis Services; clients must use **MSOLAP/AMO/ADOMD.NET client libraries** ("Client applications and tools don't communicate directly with the XMLA endpoint"). **No JDBC/ODBC-from-Spark path.** Read-only XMLA on by default; needs Premium/PPU/Fabric capacity; SPs can connect but can't be RLS/OLS members.
- **Verdict:** Power BI semantic models are **not** reachable through Lakehouse Federation or any UC connector. The only way from Databricks is **custom code** — Power BI REST `POST /v1.0/myorg/datasets/{datasetId}/executeQueries` (DAX, with Entra token, subject to row/size limits). Existing Databricks↔Power BI docs go the *other* direction (Power BI DirectQuery/Import *into* Databricks SQL; build semantic models *from* Databricks):
  - https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-service
  - https://community.databricks.com/t5/data-engineering/connect-power-bi-desktop-semantic-model-output-to-databricks/td-p/68119

**Decision matrix for the audit agent**

| Path | Mechanism | Verdict | Auth | Governed by UC? |
|---|---|---|---|---|
| (a) OneLake catalog federation | `CREATE CONNECTION TYPE onelake` + foreign catalog (`data_item`, `item_type`) | **Supported** (read-only); DBR 18.0+/SQLW 2025.40+ | Managed identity or SP (cross-tenant) | **Yes** |
| (b1) Fabric SQL endpoint via Spark JDBC | `spark.read.format("jdbc")`, mssql driver | Works (ungoverned) | SP `ActiveDirectoryServicePrincipal` | No |
| (b2) Fabric SQL endpoint via `TYPE sqlserver` | `CREATE CONNECTION TYPE sqlserver` @ `*.datawarehouse.fabric.microsoft.com:1433` | Partial/undocumented | OAuth/M2M (Entra SP) | Yes (best-effort) |
| (c) Direct abfss OneLake Delta read | Spark OAuth config / MSAL+deltalake | Works (ungoverned) | SP OAuth client creds | No |
| (c′) UC external location to OneLake | `CREATE EXTERNAL LOCATION` @ onelake abfss | **NOT supported** | n/a | n/a |
| (d) Power BI semantic model | REST `executeQueries` (DAX) / XMLA | **Not native**; custom code only | Entra token / SP | No |

**Net recommendation:** Use **(a) OneLake catalog federation** as the primary governed path to Fabric Lakehouse/Warehouse table data. Keep **(b1) Spark JDBC to the Fabric SQL endpoint** as a fallback for Warehouse/SQL-endpoint reads where OneLake federation prerequisites aren't met. Treat **(b2)** as experimental. **(d) Power BI semantic models** are out of scope for federation — handle via the REST `executeQueries` path you've already researched.

---

## Partner Connect (read-only relevance)

**TITLE:** What is Databricks Partner Connect? — Azure Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/partner-connect/
- Lets you create trial accounts with select tech partners and connect a workspace from the UI; auto-provisions a SQL warehouse (`<PARTNER>_ENDPOINT`), a service principal (`<PARTNER>_USER`), and a PAT for that SP. Requires **Premium plan+** and workspace-admin sign-in.
- Supported partners include Fivetran, dbt, Alation, **Power BI**, Tableau, Rivery, Labelbox. Categories: Data Ingestion (can create DBs/tables in your workspace), BI, Data Governance. Manage via the **Marketplace** sidebar.
- API: https://github.com/databrickslabs/partner-connect-api
- **Relevance:** Partner Connect's Power BI tile configures Power BI to consume *from* Databricks (outbound BI), not Databricks reading Fabric/PBI. **Not a data-pull path for the audit agent** — it's the wrong direction. Note for completeness: do not rely on it to ingest Fabric/PBI data into Databricks.

---

## Reading external data without copying — summary for the agent

Every option above is **read-only / zero-copy** by design:
- **Query federation** (sqlserver/sqldw/postgresql/mysql/redshift/oracle/teradata/snowflake/bigquery/salesforce/databricks): live JDBC/Storage-API reads with pushdown; no data lands in Databricks.
- **Catalog federation** (onelake, snowflake-iceberg, salesforce file sharing, Hive/Glue): UC reads object storage directly on Databricks compute.
- **OneLake federation (`onelake`)** is the Fabric-specific zero-copy, governed read path — the centerpiece for this agent.
- Avoid materializing/caching: federated queries skip Result/Disk cache, so each audit run reflects live Fabric/source state (good for accuracy; mind source load — use pushdown + `fetchSize`).

---

## Flat URL list

- https://learn.microsoft.com/en-us/azure/databricks/query-federation/
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/onelake
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/connections
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/performance-recommendations
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/sql-server
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/sql-server-entra
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/sqldw
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-catalog-federation
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-basic-auth
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-pem
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-oauth-access-token
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-entra
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/snowflake-okta
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/postgresql
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/mysql
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/redshift
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/bigquery
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/oracle
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/teradata
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/salesforce-data-cloud
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/salesforce-data-cloud-file-sharing
- https://learn.microsoft.com/en-us/azure/databricks/query-federation/databricks
- https://learn.microsoft.com/en-us/fabric/onelake/onelake-azure-databricks
- https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-connect-tools
- https://learn.microsoft.com/en-us/azure/databricks/partners/bi/power-bi-service
- https://learn.microsoft.com/en-us/azure/databricks/partner-connect/
- https://github.com/databrickslabs/partner-connect-api
- https://github.com/memomsft/databricks-fabric-integrations/blob/main/docs/01-sql-endpoint.md
- https://murggu.medium.com/running-federated-queries-from-unity-catalog-on-microsoft-fabric-sql-endpoint-1485da1d450b
- https://community.databricks.com/t5/data-engineering/azure-databricks-create-an-external-location-to-microsoft-fabric/td-p/87826
- https://community.databricks.com/t5/get-started-discussions/connect-to-onelake-using-service-principal-unity-catalog-and/td-p/110503
- https://community.databricks.com/t5/data-engineering/connect-power-bi-desktop-semantic-model-output-to-databricks/td-p/68119

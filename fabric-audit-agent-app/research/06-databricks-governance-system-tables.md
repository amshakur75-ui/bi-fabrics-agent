# 06 — Databricks Governance, Identity & System Tables

Research focus for **bi-fabrics-audit-agent** (read-only Fabric/PBI capacity audit agent running in Databricks). This file covers the **Databricks-side** controls that (a) govern/audit/bill the agent's own footprint, and (b) give the agent a *second data source* — Databricks billing/usage/audit system tables — to correlate against Fabric/PBI capacity telemetry.

Scope per assignment: Unity Catalog privilege model; service principals + OAuth M2M / PAT / on-behalf-of; system tables (`access`, `billing`, `compute`, `query`, `lakeflow`, network); Lakehouse / data-quality monitoring; verbose audit logging; IP access lists; account vs workspace vs metastore admin.

> NOTE on sources: Microsoft Learn (`learn.microsoft.com/azure/databricks/*`) and `docs.databricks.com/aws/*` are the same docs content per cloud. Identifiers (column names, privilege names, SQL) are cloud-agnostic unless noted (e.g. `metastore_id` is `null` on Azure in `billing.usage`).

---

## A. Unity Catalog privilege model

### A1. Unity Catalog privileges reference (the full privilege list)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/privileges
(AWS mirror: https://docs.databricks.com/aws/en/data-governance/unity-catalog/access-control/privileges-reference)

**Summary:** Canonical table of every UC privilege, what it does, and which securable it applies to. The agent's read-only posture maps to a *small, explicit* subset of these.

**Exact privilege identifiers (name — what it grants — applies to):**
- `USE CATALOG` — required to "interact with" objects in a catalog (does not itself grant data access) — Catalog.
- `USE SCHEMA` — required to interact with objects in a schema — Catalog, Schema.
- `SELECT` — query tables/views/materialized views/shares — Catalog, Schema, Table, View, Materialized View, Share.
- `MODIFY` — insert/update/delete data; add lineage — Catalog, Schema, Table, External Metadata.
- `EXECUTE` — invoke UDFs / load registered models for inference — Catalog, Schema, Function.
- `READ VOLUME` — read files/dirs in a volume — Catalog, Schema, Volume.
- `WRITE VOLUME` — add/modify/delete files in a volume — Catalog, Schema, Volume.
- `READ FILES` / `WRITE FILES` — read/write cloud paths — External Location.
- `BROWSE` — discover objects, view metadata, and request access **without** `USE CATALOG`/`USE SCHEMA` and without data access — Catalog, External Location, External Metadata, Clean Room.
- `MANAGE` — manage privileges, transfer ownership, rename, drop (no implicit data read) — Catalog, Schema, Table, View, MV, Volume, Function, Connection, External Location, External Metadata, Service/Storage Credential, Clean Room.
- `APPLY TAG` — add/edit tags (incl. column tags) — Catalog, Schema, Table, View, MV, Volume, Function (models only), External Metadata.
- `REFRESH` — trigger materialized-view refresh — Catalog, Schema, Materialized View.
- `CREATE CATALOG` / `CREATE CLEAN ROOM` / `CREATE CONNECTION` / `CREATE EXTERNAL LOCATION` / `CREATE EXTERNAL METADATA` / `CREATE SERVICE CREDENTIAL` / `CREATE STORAGE CREDENTIAL` / `CREATE PROVIDER` / `CREATE RECIPIENT` / `MANAGE ALLOWLIST` — Metastore-level.
- `CREATE SCHEMA` / `CREATE TABLE` / `CREATE FUNCTION` / `CREATE VOLUME` / `CREATE MATERIALIZED VIEW` / `CREATE MODEL` — Catalog, Schema.
- `CREATE MODEL VERSION` — Model. `CREATE EXTERNAL TABLE` / `CREATE EXTERNAL VOLUME` / `CREATE MANAGED STORAGE` / `CREATE FOREIGN SECURABLE` — External Location. `CREATE FOREIGN CATALOG` — Connection.
- `USE CONNECTION` — list/view connection details; required for `remote_query` — Connection.
- `EXTERNAL USE SCHEMA` — obtain temp credentials via open/Iceberg REST APIs — Schema. `EXTERNAL USE LOCATION` — External Location. `ACCESS` — use a service credential — Service Credential.
- OpenSharing: `USE SHARE`, `USE PROVIDER`, `USE RECIPIENT`, `SET SHARE PERMISSION`, `USE MARKETPLACE ASSETS`, `USE PROVIDER` — Metastore/Share.
- `ALL PRIVILEGES` — all applicable privileges on object + children, **except** `MANAGE`, `EXTERNAL USE SCHEMA`, and `EXTERNAL USE LOCATION` (excluded to prevent privilege escalation).

**Exact SQL:**
```sql
GRANT privilege_type [, privilege_type ...] ON securable_type securable_name TO principal;
REVOKE privilege_type [, ...] ON securable_type securable_name FROM principal;
SHOW GRANTS [principal] ON securable_type securable_name;   -- inspect grants on an object
SHOW GRANTS principal_type principal_name;                  -- grants held by a principal
```
`REVOKE` is idempotent — succeeds even if the privilege was never granted.

**How it helps:** Defines the *exact least-privilege grant* for the agent's service principal. To read its second data source (system tables) it needs only `USE CATALOG` on `system` + `USE SCHEMA` on the relevant schemas + `SELECT` on specific tables (see C1). To read business tables for cross-checks it needs `USE CATALOG`+`USE SCHEMA`+`SELECT`. The agent must **never** be granted `MODIFY`/`WRITE VOLUME`/`MANAGE`/`ALL PRIVILEGES` — its read-only contract is literally the absence of those. `BROWSE` lets it enumerate catalog/schema metadata for inventory without seeing data — ideal for a discovery/audit pass.

---

### A2. Securable hierarchy, inheritance & ownership (permissions model)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/permissions-concepts
(AWS mirror: https://docs.databricks.com/aws/en/data-governance/unity-catalog/access-control/permissions-concepts)

**Summary:**
- **Hierarchy:** Metastore → Catalog → Schema → {Table, View, Materialized View, Volume, Function, Model, …}. Three-level namespace `catalog.schema.object`. Catalogs and schemas are *container* objects.
- **Inheritance:** privileges granted on a container flow down to **all current and future** children. `SELECT` on a catalog ⇒ `SELECT` on every table in it. **Exception:** privileges granted on the **metastore do NOT inherit** to children.
- **Required chain to read a table:** `USE CATALOG` (catalog) + `USE SCHEMA` (schema) + `SELECT` (table). All three required.
- **Ownership:** every securable has exactly one owner (user, SP, or group). Owner implicitly has all capabilities, can grant/revoke, transfer ownership, drop. Ownership does **not** inherit downward (catalog owner ≠ owner of child schemas, but can still manage them).
- **Who can grant:** the object owner or a principal holding `MANAGE`. Only catalog/schema owners or `MANAGE` holders can grant `USE CATALOG`/`USE SCHEMA`.

**How it helps:** Lets the agent reason about *blast radius and inheritance* on the Databricks side the same way it does for Fabric workspaces. When the agent reports "who can read X," it must account for inherited catalog/schema grants and ownership, not just direct table grants. Also tells the agent's deployer that a single `GRANT SELECT ON SCHEMA system.billing` covers `usage` + `list_prices` together via inheritance.

---

### A3. Admin privileges — account vs workspace vs metastore admin
**URL:** https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/admin-privileges
(AWS mirror: https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/admin-privileges)
Also: https://learn.microsoft.com/en-us/azure/databricks/admin/admin-concepts

**Summary — the three roles:**
| Role | Scope | Required | Key powers |
|---|---|---|---|
| **Account admin** | Whole account | Yes | Create metastores/workspaces, link metastores, assign all admin roles, grant metastore privileges, **enable system tables and control who can read them**, configure storage credentials |
| **Workspace admin** | Single workspace | Yes | Manage membership, jobs, job **Run as**, workspace objects/ACLs; for workspaces created after 2023-11-09, holds metastore `CREATE CATALOG`/`CREATE EXTERNAL LOCATION`/… by default; default owner of the workspace catalog |
| **Metastore admin** | One metastore (per region) | **Optional** | Owns the metastore: change ownership of / grant on objects they don't own, manage all metadata + tags, delete metastore |

Key facts:
- **By default, only users who are BOTH account admin AND metastore admin can read system tables.** Access for anyone else (e.g. the agent SP) requires explicit grants (see C1).
- Metastore admin is assignable to a **group** (strongly recommended) via account console → Catalog → metastore → Metastore Admin → Edit. Up to ~30 s caching delay.
- `RestrictWorkspaceAdmins` setting limits what workspace admins can do.
- Metastore-admin granting power is *indirect* on data: they can transfer ownership to themselves; "no direct data access by default" and **all such permission grants are audit-logged** (→ `system.access.audit`).

**How it helps:** Establishes that the audit agent's SP should be a *non-admin* principal with narrow grants — never an account/metastore admin. It also tells the agent that admin-driven grant/ownership changes are observable in the audit table, so the agent can *detect privilege escalations* (e.g. a workspace admin granting themselves data access) as a security finding.

---

## B. Databricks identity & authentication for the agent

### B1. OAuth M2M for service principals (the agent's primary auth)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/oauth-m2m
(AWS mirror: https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m)

**Summary / exact identifiers:**
- Create an **OAuth secret** for an SP: Settings → Identity and access → Service principals → (SP) → Secrets → **Generate secret** (max lifetime **730 days**; secret shown once; up to **5** secrets/SP). Yields a **client ID** + **client secret**.
- **Token endpoints:**
  - Workspace-level: `https://<workspace-host>/oidc/v1/token`
  - Account-level: `https://accounts.<cloud-host>/oidc/accounts/<account-id>/v1/token`
- **Token request** (client-credentials, scope `all-apis`):
  ```bash
  curl --request POST --url <token-endpoint> \
    --user "$CLIENT_ID:$CLIENT_SECRET" \
    --data 'grant_type=client_credentials&scope=all-apis'
  ```
  Response: `{ "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }`. **Tokens live 1 hour**; SDK auto-refreshes.
- **Account-level tokens** can call both account- and workspace-level REST APIs (in accessible workspaces); **workspace tokens** are scoped to one workspace.
- **SDK env vars** (auto-detected by `databricks-sdk`, CLI, Terraform): `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` (+ `DATABRICKS_ACCOUNT_ID` for account ops). Python:
  ```python
  from databricks.sdk import WorkspaceClient
  w = WorkspaceClient(host="https://<host>", client_id="...", client_secret="...")
  ```

**How it helps:** This is the recommended, secret-scope-friendly way for the agent's SP to authenticate to Databricks for SQL/system-table reads and REST calls (supersedes PATs). One-hour tokens minimise blast radius if leaked. The agent should source `client_secret` from a Databricks secret scope (covered in prior research) and rely on the SDK's unified auth chain.

### B2. PAT tokens, token management & on-behalf-of (OBO)
**URLs:**
- PAT (legacy): https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/pat
- Monitor/revoke + token mgmt + OBO: https://learn.microsoft.com/en-us/azure/databricks/admin/access-control/tokens
- PAT permissions: https://learn.microsoft.com/en-us/azure/databricks/security/auth/api-access-permissions

**Summary / identifiers:**
- PATs are **legacy**; OAuth M2M preferred. A PAT has a name + lifetime (days).
- **Disable PATs per workspace:** `PATCH /api/2.0/workspace-conf` with `enableTokensConfig` = `true|false`. Cap lifetime via `maxTokenLifetimeDays` (workspace-conf / CLI).
- **Token Management API** (`databricks token-management …`) lets workspace admins list/revoke all tokens.
- **On-behalf-of (OBO) token** for an SP: `databricks token-management create-obo-token <application-id> --lifetime-seconds <n>`. A workspace admin mints the SP's initial token; the SP can then create its own.

**How it helps:** If the agent must use a PAT (e.g. a tool that doesn't speak OAuth), OBO lets an admin issue a short-lived SP token without sharing the secret. The agent (or its governance report) can also *audit* token hygiene: are long-lived PATs enabled? Is `maxTokenLifetimeDays` set? Token create/revoke events appear in `system.access.audit`.

### B3. Databricks Apps authorization — SP identity + user OBO + scopes
**URL:** https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth
(AWS mirror: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)

**Summary:** A Databricks App gets an **auto-created service principal** as its default identity (app authorization). For **user / on-behalf-of-user authorization**, Databricks forwards the calling user's identity and access token to the app in HTTP headers — header **`x-forwarded-access-token`** — and the app uses it to act as the user. Apps using user authorization must declare explicit **authorization scopes**; Databricks blocks anything outside the declared scopes even if the user is otherwise permitted.

**How it helps:** Since the agent already runs as a Databricks App + MCP (prior research), this defines the two identities the agent can act under: its own SP (for system-table/usage reads) vs. the requesting user (OBO via `x-forwarded-access-token`, scope-limited) when answering a specific person's question. Read-only intent is enforceable by declaring only read scopes.

---

## C. System tables — the agent's billing/usage/audit data source

### C1. System tables reference + enablement + read-grant model
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/)

**Summary — the `system` catalog (one per metastore) and its schemas/tables:**
- `system.access`: `audit`, `table_lineage`, `column_lineage`, `clean_room_events`, `assistant_events`, `inbound_network`, `outbound_network`, `workspaces_latest`.
- `system.billing`: `usage`, `list_prices`.
- `system.compute`: `clusters`, `node_timeline`, `node_types`, `warehouses`, `warehouse_events`, `instance_events`, `instance_pools`.
- `system.query`: `history`.
- `system.lakeflow`: `jobs`, `job_tasks`, `job_run_timeline`, `job_task_run_timeline`, `pipelines`, `pipeline_update_timeline`, `zerobus_*`.
- `system.serving`: `served_entities`, `endpoint_usage`. `system.ai_gateway`: `usage`.
- `system.mlflow`: `experiments_latest`, `runs_latest`, `run_metrics_history`.
- `system.storage`: `predictive_optimization_operations_history`. `system.marketplace`, `system.sharing`, `system.data_classification`, `system.data_quality_monitoring`, `system.replication`.
- Many tables are **Public Preview**; some Beta/Private Preview.

**Enablement & access (exact model):**
- Tables live in the `system` catalog, auto-included with every UC metastore, but each schema must be **enabled** by an account admin via the Unity Catalog **`systemschemas`** REST API (`PUT /api/2.x/unity-catalog/metastores/{metastore_id}/systemschemas/{schema_name}`).
- **By default only account+metastore admins can read.** To grant the agent read access, an admin runs:
  ```sql
  GRANT USE CATALOG ON CATALOG system TO `audit-agent-sp`;
  GRANT USE SCHEMA  ON SCHEMA  system.billing TO `audit-agent-sp`;
  GRANT SELECT      ON SCHEMA  system.billing TO `audit-agent-sp`;   -- inherits to usage + list_prices
  -- repeat USE SCHEMA + SELECT for system.access, system.compute, system.query, system.lakeflow
  ```
- System tables are **read-only** (cannot be modified) — structurally aligned with the agent's read-only contract.
- **Data scope:** operational data for all workspaces in the account in the **same cloud region** (delivered via OpenSharing). Retention varies (≈30 days to 365 days; some indefinite).
- **Guardrail:** non-selective queries error with *"System Table query returned too much data. Please repeat query with more selective predicates."* → always filter on `usage_date`/`event_date`.

**How it helps:** This is the agent's **Databricks-native second data source**. With only `USE CATALOG`+`USE SCHEMA`+`SELECT` on `system`, the read-only agent can pull billing, audit, compute, query, and job telemetry to correlate with Fabric/PBI capacity findings — no admin role needed. The grant snippet above *is* the deployment recipe.

---

### C2. `system.billing.usage` — billable usage (the cost spine)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/billing)

**Full schema (`system.billing.usage`):**
`record_id` (string), `account_id` (string), `workspace_id` (string), `sku_name` (string, e.g. `STANDARD_ALL_PURPOSE_COMPUTE`), `cloud` (string: AWS/AZURE/GCP), `usage_start_time` (timestamp UTC), `usage_end_time` (timestamp UTC), `usage_date` (date — use for partition pruning), `custom_tags` (map), `usage_unit` (string, e.g. `DBU`), `usage_quantity` (decimal), `usage_metadata` (struct), `identity_metadata` (struct), `record_type` (string: `ORIGINAL`/`RETRACTION`/`RESTATEMENT`), `ingestion_date` (date), `billing_origin_product` (string), `product_features` (struct), `usage_type` (string: `COMPUTE_TIME`/`STORAGE_SPACE`/`NETWORK_BYTE`/`NETWORK_HOUR`/`API_OPERATION`/`TOKEN`/`GPU_TIME`/`ANSWER`).

**`usage_metadata` struct subfields (string IDs; only a subset populated per record):** `cluster_id`, `job_id`, `job_run_id`, `warehouse_id`, `instance_pool_id`, `node_type`, `notebook_id`, `notebook_path`, `dlt_pipeline_id`, `dlt_update_id`, `dlt_maintenance_id`, `endpoint_name`, `endpoint_id`, `app_id`, `app_name`, `run_name`, `job_name`, `central_clean_room_id`, `source_region`, `destination_region`, `private_endpoint_name`, `usage_policy_id` (`budget_policy_id` deprecated), `storage_api_type`, `ai_runtime_workload_id`, `uc_table_catalog`/`uc_table_schema`/`uc_table_name`, `database_instance_id`, `sharing_materialization_id`, `agent_bricks_id`, `base_environment_id`, `schema_id`, `table_id`, `catalog_id`. (`metastore_id` is always `null` on Azure.)

**`identity_metadata` struct:** `run_as` (who ran the workload — populated per workload type), `owned_by` (SQL-warehouse owner only), `created_by` (Apps / Agent Bricks creator email).

**`billing_origin_product` values:** `JOBS`, `DLT`, `SQL`, `ALL_PURPOSE`, `MODEL_SERVING`, `INTERACTIVE`, `DEFAULT_STORAGE`, `VECTOR_SEARCH`, `LAKEHOUSE_MONITORING`, `PREDICTIVE_OPTIMIZATION`, `FOUNDATION_MODEL_TRAINING`, `AGENT_EVALUATION`, `DATA_CLASSIFICATION`, `DATA_QUALITY_MONITORING`, `DATA_SHARING`, `AI_GATEWAY`, `AI_RUNTIME`, `NETWORKING`, **`APPS`**, `DATABASE`, `AI_FUNCTIONS`, `AGENT_BRICKS`, `CLEAN_ROOM`, `LAKEFLOW_CONNECT`, … (`APPS` = Databricks Apps cost = **the agent's own footprint**).

**`product_features` struct:** `jobs_tier`, `sql_tier` (CLASSIC/PRO), `dlt_tier`, `is_serverless`, `is_photon`, `serving_type`, `performance_target`, `networking.connectivity_type` (PUBLIC_IP/PRIVATE_IP), `ai_functions.ai_function`, etc.

**Corrections:** a fix adds a `RETRACTION` (negative `usage_quantity` cancelling the original) + a `RESTATEMENT` (corrected values). Always `SUM(usage_quantity)` across record types:
```sql
SELECT usage_metadata.job_id, usage_start_time, usage_end_time, SUM(usage_quantity) AS usage_quantity
FROM system.billing.usage
GROUP BY ALL HAVING usage_quantity != 0;
```

**How it helps:** The DBU-level cost spine. The agent can: (1) **isolate its own cost** with `WHERE billing_origin_product='APPS' AND usage_metadata.app_id=<agent-app-id>` (governance/self-accounting); (2) attribute spend by `custom_tags`, workspace, SKU, job, user (`identity_metadata.run_as`); (3) correlate Databricks DBU trends with Fabric capacity CU pressure in the same reporting window.

---

### C3. `system.billing.list_prices` + the canonical $ cost join
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/usage/system-tables
(reference page: https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing)

**`system.billing.list_prices` schema:** `price_start_time` (timestamp), `price_end_time` (timestamp, `null` = current), `account_id`, `sku_name`, `cloud`, `currency_code`, `usage_unit`, and **`pricing` struct** with `default`, `promotional`, and `effective_list` (e.g. `pricing.default`, `pricing.effective_list.default`). A new row is written each time a SKU price changes (historical price log).

**Canonical list-cost ($) query — join usage × prices on SKU within the price-validity window:**
```sql
SELECT
  u.workspace_id, u.sku_name,
  SUM(u.usage_quantity * p.pricing.default) AS list_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices p
  ON u.sku_name = p.sku_name
 AND p.price_start_time <= u.usage_start_time
 AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
 AND p.currency_code = 'USD'
WHERE u.usage_date >= current_date() - INTERVAL 30 DAYS
GROUP BY ALL;
```
(Equivalent forms use `pricing.effective_list.default`.)

**How it helps:** Converts DBUs → dollars (list price) so the agent reports **$ cost**, not just DBUs — directly comparable to Fabric capacity $ and to PBI Premium/F-SKU cost. The price-window join is the standard, correctness-preserving pattern; the agent should template it.

---

### C4. `system.access.audit` — audit log (governance & security source)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/audit-logs
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/audit-logs ; event catalog: https://learn.microsoft.com/en-us/azure/databricks/admin/account-settings/audit-logs)

**Full schema (`system.access.audit`, Public Preview):**
`account_id` (string), `workspace_id` (string), `version` (string, e.g. `2.0`), `event_time` (timestamp UTC), `event_date` (date), `source_ip_address` (string), `user_agent` (string), `session_id` (string), `user_identity` (struct: `email`, `subjectName`), `service_name` (string), `action_name` (string), `request_id` (string), `request_params` (map<string,string>), `response` (struct: `statusCode`, `errorMessage`, `result`), `audit_level` (string: `ACCOUNT_LEVEL` or workspace-level), `event_id` (string), `identity_metadata` (struct: `run_by`, `run_as`).

**Key facts:**
- **Account-level events have `workspace_id = 0`.** Most logs only available in the workspace's region.
- `service_name` values include `unityCatalog`, `accounts`, `clusters`, `jobs`, `notebook`, `sqlanalytics`/`databrickssql`, `genie`, `mlflowAcledArtifact`, etc.; `action_name` is the specific event (e.g. `getTable`, `createCluster`, `generateDbToken`).
- Latency is typically up to ~15 min (per general system-table delivery); retention per system-table defaults (~365 days). Statement/PII redaction applies on the **query.history** table, not audit.

**How it helps:** Lets the read-only agent **self-audit and security-audit on the Databricks side**: prove it only issued read (`getTable`/`get*`/SELECT) actions and never `update*`/`delete*`/`grant*`; detect privilege-escalation events (admin `grant`/ownership transfers — which are explicitly audit-logged); track who accessed which UC table; spot failed logins (`response.statusCode` / `action_name='login'`). Complements Fabric-side activity logs for an end-to-end access picture.

---

### C5. Verbose audit logging (notebook/SQL command capture)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/account-settings/verbose-logs
(AWS mirror: https://docs.databricks.com/aws/en/admin/account-settings/verbose-logs)

**Summary:** Verbose audit logs add **data-access-level events**. Toggle is workspace-conf key **`enableVerboseAuditLogs`** (`workspaceConfKeys=enableVerboseAuditLogs`, `workspaceConfValues=true|false`); enabling/disabling itself emits a `workspace` / `workspaceConfKeys` audit event. With it on:
- Notebook **`runCommand` / `submitCommand`** events are logged, and `submitCommand` includes the **`commandText`** request param.
- Databricks **SQL query** execution is logged (auto-included once verbose is on for notebooks).

**How it helps:** Without verbose logging, command-level activity is invisible in `system.access.audit`. If the agent (or its compliance report) needs to *prove* exactly which commands ran — including that it only ran read queries — verbose audit logging must be enabled. The agent can flag "verbose audit logging disabled" as a governance gap.

---

### C6. `system.query.history` — query execution + cost/perf
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/query-history
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/query-history)

**Schema highlights (`system.query.history`, Public Preview):**
`account_id`, `workspace_id`, `statement_id`, `session_id`, `execution_status` (FINISHED/FAILED/CANCELED), `compute` struct (`type`=WAREHOUSE/SERVERLESS_COMPUTE, `warehouse_id`, `cluster_id`), `executed_by_user_id`, `executed_by`, `executed_as_user_id`, `executed_as` (privileges used — SP-aware), `statement_text` (**redacted to `<Redacted>` unless account admin or member of `databricks_pii_access` group**), `statement_type` (SELECT/INSERT/ALTER/COPY/…), `error_message`, `client_application`, `client_driver`, `from_result_cache`, `total_duration_ms`, `waiting_for_compute_duration_ms`, `waiting_at_capacity_duration_ms`, `execution_duration_ms`, `compilation_duration_ms`, `result_fetch_duration_ms`, `total_task_duration_ms`, `start_time`, `end_time`, `read_partitions`, `read_files`, `pruned_files`, `read_rows`, `produced_rows`, `read_bytes`, `read_io_cache_percent`, `spilled_local_bytes`, `written_bytes`/`written_rows`/`written_files`, `shuffle_read_bytes`, `query_source` struct (`alert_id`, `sql_query_id`, `dashboard_id`, `notebook_id`, `genie_space_id`, `job_info.{job_id,job_run_id,job_task_run_id}`, `legacy_dashboard_id`), `query_parameters` struct, `query_tags` map.

Coverage: SQL warehouses + serverless (notebooks/jobs). Default access: **admins only**; statement text redacted unless privileged.

**How it helps:** The agent can surface **expensive / queued / spilling** queries (`waiting_at_capacity_duration_ms`, `spilled_local_bytes`, `read_bytes`) — the Databricks analogue of Fabric capacity throttling — and attribute them to a dashboard/job/Genie space via `query_source`. `from_result_cache`/`read_io_cache_percent` flag caching opportunities. Note redaction: the agent's SP must be a member of `databricks_pii_access` (or admin) to read `statement_text`; otherwise it gets `<Redacted>` (good for least-privilege).

---

### C7. `system.compute.*` — clusters, warehouses, utilization
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/compute
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/compute)

**Tables & key columns:**
- **`system.compute.clusters`** (SCD): `cluster_id`, `cluster_name`, `owned_by`, `create_time`/`delete_time`, `driver_node_type`, `worker_node_type`, `worker_count`, `min/max_autoscale_workers`, `auto_termination_minutes`, `enable_elastic_disk`, `tags` (map), `cluster_source` (UI/API/JOB/PIPELINE), `init_scripts`, `{aws,azure,gcp}_attributes`, `driver/worker_instance_pool_id`, `dbr_version`, `change_time`/`change_date`, `data_security_mode` (USER_ISOLATION/SINGLE_USER/…), `policy_id`.
- **`system.compute.node_timeline`** (per-minute utilization): `cluster_id`, `instance_id`, `start_time`/`end_time`, `driver` (bool), `cpu_user_percent`, `cpu_system_percent`, `cpu_wait_percent`, `mem_used_percent`, `mem_swap_percent`, `network_sent/received_bytes`, `disk_free_bytes_per_mount_point` (map), `node_type`, `private_ip`.
- **`system.compute.node_types`**: `node_type`, `core_count`, `memory_mb`, `gpu_count`.
- **`system.compute.instance_events`** (Preview): `instance_id`, `event_time`, `event_type` (INSTANCE_LAUNCHING/STATE_TRANSITION), `state`, `availability_type` (ON_DEMAND/SPOT).
- **`system.compute.instance_pools`** (Preview): pool config SCD. *(`warehouses` / `warehouse_events` exist for SQL-warehouse config & events but schema not enumerated here.)*

Notes: excludes serverless & SQL warehouses; nodes <10 min may not appear in `node_timeline`; records pre-2023-10-23 unavailable; compute is region-scoped while billing is cross-region.

**Cluster→cost attribution join:**
```sql
SELECT u.record_id, c.cluster_id, c.owned_by, u.usage_start_time, u.usage_quantity
FROM system.billing.usage u
JOIN system.compute.clusters c
  ON u.usage_metadata.cluster_id = c.cluster_id
 AND date_trunc('HOUR', c.change_time) <= date_trunc('HOUR', u.usage_start_time);
```

**How it helps:** Surfaces **idle/oversized/under-utilized compute** (low `cpu_user_percent` + high cost), long auto-termination windows, and ungoverned clusters (`policy_id` null) — the Databricks analogue of the agent's Fabric "unused/oversized capacity" detectors. Attributes DBU cost to a cluster owner for chargeback.

---

### C8. `system.lakeflow.*` — jobs/tasks/run timelines + cost-per-job
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/jobs
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/jobs)

**Tables & key columns:**
- **`system.lakeflow.jobs`** (SCD): `account_id`, `workspace_id`, `job_id`, `name`, `creator_id`, `change_time`, `delete_time`, `paused`.
- **`system.lakeflow.job_tasks`** (SCD): `job_id`, `task_key`, `depends_on_keys` (array), `change_time`, `timeout_seconds`.
- **`system.lakeflow.job_run_timeline`** (immutable): `job_id`, `run_id`, `period_start_time`, `period_end_time`, `result_state`, `run_type`, `job_parameters` (map).
- **`system.lakeflow.job_task_run_timeline`** (immutable): `job_id`, `run_id`, `job_run_id`, `task_key`, `period_start_time`/`period_end_time`, `result_state`, `compute_ids` (array).

Read privilege: account+metastore admin, or `USE`+`SELECT` on the schema.

**Cost-per-job-run (joins usage × list_prices × run timeline):**
```sql
WITH jobs_usage AS (
  SELECT *, usage_metadata.job_id, usage_metadata.job_run_id AS run_id,
         identity_metadata.run_as AS run_as
  FROM system.billing.usage WHERE billing_origin_product='JOBS'
),
jobs_usd AS (
  SELECT j.*, j.usage_quantity * p.pricing.default AS usage_usd
  FROM jobs_usage j
  LEFT JOIN system.billing.list_prices p
    ON j.sku_name = p.sku_name
   AND p.price_start_time <= j.usage_start_time
   AND (p.price_end_time >= j.usage_start_time OR p.price_end_time IS NULL)
   AND p.currency_code='USD'
)
SELECT workspace_id, job_id, run_id, FIRST(run_as,TRUE) run_as,
       SUM(usage_usd) usage_usd
FROM jobs_usd GROUP BY ALL ORDER BY usage_usd DESC LIMIT 100;
```

**How it helps:** Lets the agent flag **failing, runaway, or expensive jobs/pipelines** and attribute $ cost + `run_as` owner — the Databricks counterpart to PBI dataset-refresh / dataflow cost findings, and the natural place to schedule the agent itself as a Databricks Job.

---

### C9. Network access events — `system.access.outbound_network` / `inbound_network`
**URL:** https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/network
(AWS mirror: https://docs.databricks.com/aws/en/admin/system-tables/network)

**Summary:** Records events where serverless egress/ingress is **denied** by a network policy. Columns: `event_id`, `event_time`, `account_id`, `workspace_id`, `destination`, `destination_type`, `hostname`, `path`, `rejection_reason`, `access_type` (`DROP` = real denial, `DRY_RUN_DENIAL` = would-be denial under dry-run). Public Preview.

**How it helps:** The agent's own outbound calls to **Fabric/Power BI REST endpoints** run on serverless compute; if an egress (network) policy blocks `*.powerbi.com` / `api.fabric.microsoft.com` / Graph, the denial surfaces here. The agent can read this table to *self-diagnose connectivity failures* and to flag overly restrictive egress policies. (Related serverless egress control: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies)

---

## D. Monitoring & network controls

### D1. Lakehouse Monitoring / Data quality monitoring (data profiling)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/lakehouse-monitoring/
(redirects to Data profiling: https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/data-quality-monitoring/data-profiling/ ; metric tables: …/data-profiling/monitor-output ; API: …/data-profiling/create-monitor-api ; Python API ref: https://api-docs.databricks.com/python/lakehouse-monitoring/latest/index.html)

**Summary:**
- Three **profile types**: **Time series** (timestamp-windowed metrics), **Inference** (model request log: inputs+prediction+optional label), **Snapshot** (whole-table each refresh; max **4 TB**).
- Creating a monitor attaches a profile to a UC table and produces two **metric tables** (Delta, in a schema you choose) + an auto-generated dashboard: a **profile metrics table** (summary stats per column × window × slice × group) and a **drift metrics table** (distribution change vs previous window and/or a **baseline table**).
- **Requirements / privileges:** workspace UC-enabled + Databricks SQL access; **`USE CATALOG`+`USE SCHEMA`+`SELECT`+`MANAGE`** on the target table/schema/catalog. Only **Delta** tables (managed/external/view/MV/streaming).
- **Billing:** uses **serverless compute for jobs**; cost appears under `billing_origin_product` = `LAKEHOUSE_MONITORING` / `DATA_QUALITY_MONITORING` in `system.billing.usage`. Time-series/inference profiles compute metrics over the **last 30 days** by default.

**How it helps:** The agent can't monitor `system` tables, but it **can attach a monitor to its own output tables** (e.g. the findings/snapshot Delta tables it writes) to detect data-quality drift in its own pipeline, and it can *read other teams' monitor metric tables* as additional quality signals. It also knows monitoring itself is a billable line item it can attribute.

### D2. IP access lists (front-end network control)
**URLs:**
- Manage (concepts + API): https://learn.microsoft.com/en-us/azure/databricks/security/network/front-end/ip-access-list
- Workspace config: https://learn.microsoft.com/en-us/azure/databricks/security/network/front-end/ip-access-list-workspace
- Account console: https://docs.databricks.com/aws/en/security/network/front-end/ip-access-list-account
- CLI: https://docs.databricks.com/aws/en/dev-tools/cli/reference/ip-access-lists-commands

**Summary:** Allow/block lists of public IPs/CIDRs for the account console and workspaces. Configured via REST API / `databricks ip-access-lists` CLI. Each list has a `label` and `list_type` = `ALLOW` | `BLOCK`. **Block lists are evaluated first** (matching IP rejected); then, if any allow list exists, the IP must match an allow list. With the feature enabled but no lists, all IPs allowed; adding an allow entry then blocks everything else. Max **1000** IP/CIDR values combined; changes take a few minutes.

**How it helps:** The agent runs inside Databricks (egresses its IP/NAT). If an IP access list is in force, the agent's outbound source IP (and any callers reaching the workspace/account API) must be allow-listed. The agent can read/report IP-access-list config as a governance/security finding and explain `403`/blocked-connection failures of its own REST calls. IP-access-list changes are also audit-logged (`system.access.audit`, `service_name=accounts`/`workspace`).

---

## E. How this ties back to the audit agent (synthesis)

- **Least-privilege identity:** OAuth M2M SP (client ID/secret from a secret scope), 1-hour tokens, *no* admin role, *no* write privileges. Read scope = `USE CATALOG system` + `USE SCHEMA`/`SELECT` on `system.billing|access|compute|query|lakeflow`, plus `USE CATALOG`/`USE SCHEMA`/`SELECT` (and optionally `BROWSE`) on business catalogs for cross-checks.
- **Second data source:** `system.billing.usage`×`list_prices` (→ $ cost), `system.lakeflow.*` (job cost/health), `system.compute.*` (idle/oversized compute), `system.query.history` (throttling/spill), `system.access.audit` (who-did-what + privilege escalation), `outbound_network` (egress denials) — all correlatable with Fabric/PBI capacity findings in the same window.
- **Self-governance:** the agent attributes its own footprint via `billing_origin_product='APPS'` + `usage_metadata.app_id`, proves read-only behavior via `system.access.audit` (with verbose logging for command-level proof), and self-diagnoses connectivity via `outbound_network` + IP-access-list config.

---

## Flat URL list (all sources)

- https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/privileges
- https://docs.databricks.com/aws/en/data-governance/unity-catalog/access-control/privileges-reference
- https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/permissions-concepts
- https://docs.databricks.com/aws/en/data-governance/unity-catalog/access-control/permissions-concepts
- https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/admin-privileges
- https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/admin-privileges
- https://learn.microsoft.com/en-us/azure/databricks/admin/admin-concepts
- https://docs.databricks.com/aws/en/admin/admin-concepts
- https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/oauth-m2m
- https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m
- https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/pat
- https://docs.databricks.com/aws/en/dev-tools/auth/pat
- https://learn.microsoft.com/en-us/azure/databricks/admin/access-control/tokens
- https://docs.databricks.com/aws/en/admin/access-control/tokens
- https://learn.microsoft.com/en-us/azure/databricks/security/auth/api-access-permissions
- https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth
- https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/
- https://docs.databricks.com/aws/en/admin/system-tables/
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing
- https://docs.databricks.com/aws/en/admin/system-tables/billing
- https://learn.microsoft.com/en-us/azure/databricks/admin/usage/system-tables
- https://docs.databricks.com/aws/en/admin/usage/system-tables
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/audit-logs
- https://docs.databricks.com/aws/en/admin/system-tables/audit-logs
- https://learn.microsoft.com/en-us/azure/databricks/admin/account-settings/audit-logs
- https://learn.microsoft.com/en-us/azure/databricks/admin/account-settings/verbose-logs
- https://docs.databricks.com/aws/en/admin/account-settings/verbose-logs
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/query-history
- https://docs.databricks.com/aws/en/admin/system-tables/query-history
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/compute
- https://docs.databricks.com/aws/en/admin/system-tables/compute
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/jobs
- https://docs.databricks.com/aws/en/admin/system-tables/jobs
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/network
- https://docs.databricks.com/aws/en/admin/system-tables/network
- https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-network-policies
- https://learn.microsoft.com/en-us/azure/databricks/lakehouse-monitoring/
- https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/data-quality-monitoring/data-profiling/
- https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/data-quality-monitoring/data-profiling/monitor-output
- https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/data-quality-monitoring/data-profiling/create-monitor-api
- https://api-docs.databricks.com/python/lakehouse-monitoring/latest/index.html
- https://learn.microsoft.com/en-us/azure/databricks/security/network/front-end/ip-access-list
- https://learn.microsoft.com/en-us/azure/databricks/security/network/front-end/ip-access-list-workspace
- https://docs.databricks.com/aws/en/security/network/front-end/ip-access-list-account
- https://docs.databricks.com/aws/en/dev-tools/cli/reference/ip-access-lists-commands

# 15 — Fabric-Native Storage & Query Architecture: Eventhouse vs Lakehouse vs Warehouse

**Research focus:** The Fabric-native storage & query architecture decision for the **bi-fabrics-audit-agent** (READ-ONLY Fabric/PBI capacity audit agent). The agent must STORE three distinct data shapes:

- **(a)** High-frequency capacity telemetry — *Capacity Overview Events*, ~30s cadence (time-series, append-only, high volume).
- **(b)** Per-run audit history — one row per sweep (low volume, append, queried by date/run).
- **(c)** Curated capacity-reporting tables — star/dimensional, BI-facing (Power BI Direct Lake / DirectQuery).

**Scope boundary (already covered elsewhere, NOT duplicated here):** Databricks-side Delta tables (file 04 / 01); Databricks↔OneLake access (file 07); Fabric Workspace Monitoring Eventhouse *as a telemetry source*. **This file is the store-selection & interop decision.**

**TL;DR recommendation:**
- **(a) capacity telemetry → Eventhouse (KQL database)** — purpose-built for streaming time-series; KQL `make-series`/`summarize bin()`; cheap hot/cold tiering; turn on **OneLake availability** so the same rows are readable as Delta by Spark/Power BI/Databricks with zero copy.
- **(b) audit run-history + (c) reporting tables → Lakehouse** (Spark-written Delta + auto SQL analytics endpoint) is the best fit for a **Python/Spark-driven agent**. Use **Warehouse** instead only if the team is T-SQL-first or needs multi-table ACID transactions and full DML.
- **OneLake + Delta-Parquet is the interop fabric**: every store writes Delta to OneLake, so Power BI (Direct Lake) and Databricks read all three without ETL. Eventhouse joins the lake via **OneLake availability**; Lakehouse/Warehouse can **shortcut** to each other and to the Eventhouse's OneLake tables.

---

## Decision Matrix (store-per-purpose)

| Purpose | Data shape | **Recommended store** | Engine / query language | Write path | Why |
|---|---|---|---|---|---|
| **(a)** Capacity Overview Events, 30s telemetry | High-frequency, append-only, time-series, semi-structured | **Eventhouse / KQL DB** | **KQL** (+ T-SQL via SQL endpoint when OneLake availability on) | Eventstream → Eventhouse (direct or processed); SDK/Kafka; or query-acceleration shortcut | Built for "query billions of events in seconds"; native time-series ops; hot/cold cache tiering; autoscale; suspends when idle |
| **(b)** Per-run audit history (1 row/sweep) | Low-volume, append, queried by run/date | **Lakehouse** (Delta table via Spark) — *Warehouse if T-SQL-first* | **Spark / Spark SQL** + read-only **T-SQL** via SQL analytics endpoint | notebook / Spark job `df.write.format("delta")`; or pipeline | Agent is Python; a single append per sweep is trivial; no multi-table txn needed |
| **(c)** Curated capacity-reporting (star schema) | Structured, dimensional, BI-facing | **Lakehouse** (gold tables) — *Warehouse if dimensional T-SQL modeling / multi-table ACID* | **Spark** to write; **T-SQL** (SQL endpoint) + **Power BI Direct Lake** to read | Spark gold-layer build (medallion) or T-SQL `CTAS`/`INSERT` in Warehouse | Direct Lake reads Delta with no import; SQL endpoint serves analysts; Warehouse adds full DML + transactions if needed |

**One-line rule:** *Eventhouse for the fast time-series (a); Lakehouse for the Spark-written audit history (b) and gold reporting tables (c); Warehouse only swaps in for (b)/(c) when the team is T-SQL-first or needs multi-table transactions. All three land Delta in OneLake so Power BI and Databricks read everything with no copy.*

**Interop spine:** OneLake single-copy + Delta-Parquet open format. Eventhouse → **OneLake availability** (logical Delta copy). Lakehouse/Warehouse ↔ each other and ↔ Eventhouse OneLake tables via **shortcuts** (live, no copy). Power BI reads any of them via **Direct Lake**. Databricks reads/writes the same OneLake Delta via ADLS Gen2 API (file 07).

---

## Detailed Findings

### 1. Fabric decision guide — choose a data store
- **URL:** https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-data-store
- **Summary:** Top-level decision guide mapping use case → Fabric store. All stores expose data in **OneLake in open table format (Delta) by default**.
- **Exact mapping (verbatim use-case table):**
  - *Streaming event data, high granularity (in time, space, detail – JSON/Text) activity data for interactive analytics* → **Eventhouse** (OneLake: Yes)
  - *AI, NoSQL, and vector search* → **Cosmos DB in Fabric**
  - *Operational transactional database, OLTP* → **SQL database in Fabric**
  - *Enterprise data warehouse, SQL-based BI, OLAP, full SQL transaction support* → **Data Warehouse**
  - *Big data and machine learning, un/semi/structured data, data engineering* → **Lakehouse**
- **Personas / languages (verbatim):**
  - **Eventhouse** — App dev, Data scientist, Data engineer; *No code, KQL, SQL*; **KQL (Kusto Query Language), T-SQL**
  - **Data Warehouse** — DW dev, Data architect, Data engineer; star-schema design, SSMS, VS Code; **T-SQL, No code**
  - **Lakehouse** — Data engineer, Data scientist; PySpark, Delta Lake, notebooks; **Spark (Scala, PySpark, Spark SQL, R)**
- **Scenario 3 (most relevant to capacity telemetry):** Daisy needs to handle **billions of rows**, build dashboards, mixed structured/semi/unstructured → chooses **Eventhouse** for scalability, quick response times, **time series analysis**, geospatial functions, and **fast Direct Query mode in Power BI**.
- **How it helps:** This is the canonical Microsoft source justifying **Eventhouse for (a)** and **Lakehouse/Warehouse for (b)/(c)** in the agent's design doc.

### 2. Eventhouse overview
- **URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse
- **Summary:** "Eventhouses are databases designed for storing and analyzing **streaming data**, so you can query **billions of events in seconds**." Preferred engine for **semistructured and free-text** analysis. An Eventhouse is a **container that can hold multiple KQL databases**, sharing capacity/resources; provides unified monitoring per-database and across databases.
- **Key facts:**
  - Tailored to **time-based, streaming events** (structured, semistructured JSON/XML, unstructured free text). Data is *automatically organized for fast searching based on when it arrived*.
  - Ingestion sources: **Eventstream, SDKs, Kafka, Logstash, dataflows**, multiple formats.
  - Create a **KQL database** within an eventhouse — standard DB or a **database shortcut**. Each KQL DB gets an embedded **KQL queryset** for exploration.
  - **OneLake availability** ("Data availability in OneLake") can be enabled at database or table level.
  - **Eventhouse endpoint:** enabling it from a Lakehouse/Warehouse auto-creates an Eventhouse + KQL DB as **child items**; backend **schema sync** keeps them aligned.
  - System overview surfaces: Eventhouse storage, **Compute usage**, **Ingestion rate**, top queried/ingested DBs, schema changes.
  - **Capacity Planner / Capacity Scheduler:** Eventhouse **suspends when not in use** (saves cost; ~few seconds reactivation latency). Enable Capacity Planner for **100% uptime without extra premium storage cost**, or schedule a **7-day recurring minimum capacity in 60-minute blocks**; default minimum is **2 CUs** if none set; autoscale still scales up.
- **How it helps:** Confirms Eventhouse is the right home for (a). The **suspend-when-idle** behavior matters: a 30s telemetry stream keeps it warm, but if the agent batches, configure Capacity Planner to avoid cold-start latency on time-sensitive queries.

### 3. Turn on OneLake availability for an Eventhouse (the mirroring bridge)
- **URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability
- **Summary:** Creates a **logical copy of KQL data in Delta Lake format** so it's queryable by **Direct Lake (Power BI), Warehouse, Lakehouse, Notebooks**, and more. "Delta Lake is the unified data lake table format that makes seamless data access possible across all compute engines in Fabric."
- **Exact mechanics:**
  - Toggle at **database or table level** (details pane → **OneLake** section → **Availability = Enabled**). *Apply to existing tables* option backfills history.
  - **Retention applies to the OneLake copy** — data removed at end of retention is also removed from OneLake. Turning availability **off soft-deletes** from OneLake; turning back on restores all data including historic backfill.
  - **No extra storage cost** to enable OneLake availability (see resource consumption / storage-billing).
  - **Restrictions while ON:** cannot rename tables, alter a column type (add/delete column OK), apply row-level security, or delete/truncate/purge. (Turn off → do the op → turn on.)
  - **Adaptive Parquet batching:** writes batched into 200–256 MB Parquet files; default write delay **up to 3 hours** (`TargetLatencyInMinutes`), configurable **5 min–3 hr**. The OneLake table is **read-only** and can't be optimized after creation.
  - **Mirroring policy** auto-enabled; monitor latency via `.show table mirroring operations` (Latency `00:00:00` = fully synced).
- **Exact commands:**
  ```kusto
  // Reduce write-to-OneLake delay to 5 minutes for one table
  .alter-merge table <TableName> policy mirroring dataformat=parquet
      with (IsEnabled=true, TargetLatencyInMinutes=5);
  ```
  ```python
  # Read the Eventhouse OneLake Delta table from a Fabric/Spark notebook
  delta_table_path = 'abfss://<workspaceGuid>@onelake.dfs.fabric.microsoft.com/<eventhouseGuid>/Tables/<tableName>'
  df = spark.read.format("delta").load(delta_table_path)
  df.show()
  ```
- **How it helps:** This is **the single most important interop lever** for the agent: store (a) in Eventhouse for fast KQL, flip on OneLake availability, and Power BI + Databricks read the *same* telemetry as Delta with **no copy and no extra storage cost**. Note the **up-to-3-hour default latency** to OneLake — tune `TargetLatencyInMinutes` down (at the cost of smaller files) if BI needs fresher-than-3h telemetry; otherwise query KQL directly for real-time.

### 4. Change data policies — retention & caching (KQL DB)
- **URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-policies
- **Summary:** Two independent policies on a KQL DB, set via **Manage > Data policies** (or KQL).
  - **Retention policy** (governs `SoftDeletePeriod`, the queryable extent / OneLake Standard Storage): **default 3,650 days**, min **1 day**, max **36,500 days**. Removes age-based data automatically.
  - **Caching policy** (hot cache on local SSD / OneLake Cache Storage): **default 3,650 days**; the cache period **must be ≤ retention period**. More hot cache = faster queries + higher storage cost.
- **Caching vs retention (from cache-policy doc, https://learn.microsoft.com/en-us/kusto/management/cache-policy?view=microsoft-fabric):** caching = *how to prioritize resources*; retention = *extent of queryable data*. Cache uses ~95% of local SSD; on pressure, most-recent data preferentially kept.
- **Exact commands:**
  ```kusto
  .alter database <DBName> policy retention "{\"SoftDeletePeriod\": \"30.00:00:00\", \"Recoverability\": \"Enabled\"}"
  .alter database <DBName> policy caching hot = 7d        // keep 7 days hot
  .alter table <TableName> policy caching hot = 3d
  ```
  (Retention: https://learn.microsoft.com/en-us/kusto/management/alter-database-retention-policy-command?view=microsoft-fabric ; Caching: https://learn.microsoft.com/en-us/kusto/management/alter-table-cache-policy-command?view=microsoft-fabric)
- **How it helps:** For (a) the agent should set a **short caching period** (e.g. 7–14 days — the window analysts actually query interactively) and a **longer retention** (e.g. 90–365 days) to keep cost low while retaining history. This is the core cost knob for telemetry.

### 5. Eventhouse & KQL database consumption (cost/CU)
- **URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/real-time-intelligence-consumption
- **Summary:** Compute billed via **Eventhouse UpTime** = seconds active × vCores used (autoscaled). *E.g. 4 vCores active 30s = 120 CU-seconds.* For a multi-DB Eventhouse, UpTime rolls up to the **eventhouse item** (sub-DBs not shown separately).
- **Storage billed separately** in two tiers:
  - **OneLake Cache Storage** — premium SSD tier (ADLS premium-equivalent), governed by the **caching policy**; faster queries.
  - **OneLake Standard Storage** — standard tier (ADLS hot-equivalent), governed by the **retention policy**; persists all queryable data.
  - With **Capacity Scheduler** enabled, you're **not charged for OneLake Cache Storage** (cache cost folded into capacity charges).
- **Throttling levels:** Proactive (queries throttled, ingestion continues) → Reactive (both paused, no loss) → Extreme reactive (paused, possible loss after period). **Workspace-level surge protection**: per-workspace CU% thresholds over rolling 24h, automatic blocking, mission-critical exemption.
- **Monitoring:** **Microsoft Fabric Capacity Metrics app** (Eventhouse UpTime on compute page; OneLake Storage on storage page) — *requires capacity admin*.
- **How it helps:** Frames the agent's own cost footprint. Because Eventhouse **autoscales and suspends**, a steady 30s stream is cheap. The agent (which itself audits CU%) should read the **same Capacity Metrics app semantic model / surge-protection signals** it relies on, and be aware its Eventhouse UpTime contributes to capacity CU.

### 6. Eventhouse endpoint for Lakehouse and Data Warehouse
- **URL:** https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse-as-endpoint
- **Summary:** Add a **KQL query experience on top of existing Lakehouse/Warehouse Delta tables without duplication**. Auto-creates an Eventhouse + read-only KQL DB as **child items**; each source table attaches via a **OneLake shortcut** with **Query acceleration policies**. Schema syncs **within seconds**.
- **Key facts:** Read-only KQL DB named `<Source>_EventhouseEndpoint`; queryset `<Source>_EventhouseEndpoint_queryset`. Default **cache period 30 days** per shortcut (min 1, max 36,500). Sync statuses: `synced` / `workInProgress` / `warmingUp`. Permissions inherited from parent source (contributor/owner). Cannot enable from inside an open Lakehouse.
- **How it helps:** The **reverse bridge** — if the agent stores audit history (b)/reporting (c) in a **Lakehouse/Warehouse** but later wants ad-hoc **KQL time-series exploration** over it (anomaly detection, `series_decompose`), enable the Eventhouse endpoint with **no data copy**. Gives KQL analytics over Delta without moving (b)/(c) into Eventhouse.

### 7. What is a lakehouse?
- **URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-overview
- **Summary:** Combines data-lake scale with warehouse querying. **One copy of data**; **Delta Lake** (ACID, schema enforcement, time travel); **Spark + SQL** access; auto **SQL analytics endpoint**.
- **Lakehouse vs Warehouse table (verbatim):**

  | - | Lakehouse | Data warehouse |
  |---|---|---|
  | Primary development tool | Apache Spark (Python, Scala, SQL, R) | T-SQL |
  | Data types | Structured and unstructured | Structured |
  | Multi-table transactions | No | Yes |
  | Data ingestion | Notebooks, pipelines, dataflows, OneLake shortcuts (live access without copy) | T-SQL (`COPY INTO`, `INSERT`, `CTAS`), pipelines |
  | Best for | Data engineering, data science, medallion architectures | BI reporting, dimensional modeling, SQL-first teams |

- **Key facts:**
  - Two top-level folders: **Tables** (managed Delta) and **Files** (unstructured/non-Delta). Files dropped in **Tables** auto-validated as Delta, metadata extracted, registered in metastore — **no manual `CREATE TABLE`**.
  - **SQL analytics endpoint** is **read-only**, doesn't support full Warehouse T-SQL surface; only **Delta tables** (including via shortcuts) appear — Parquet/CSV must be converted to Delta.
  - **Note:** since **Sep 5, 2025**, default semantic models are **no longer auto-created** for new lakehouses (decoupled by Nov 30, 2025) — create a Power BI semantic model explicitly.
  - **Analyze data with** dropdown: SQL analytics endpoint, **Eventhouse endpoint**, Notebook.
- **Ingestion tools:** OneLake shortcuts, Lakehouse explorer, Notebooks (Spark), Pipelines (copy activity), Spark job definitions, **Dataflows Gen2**.
- **How it helps:** For a **Python/Spark agent**, the Lakehouse is the natural home for (b) and (c): write Delta from a notebook/Spark job, get a free read-only T-SQL endpoint for analysts, and serve Power BI via Direct Lake. The auto file-to-table registration removes DDL boilerplate.

### 8. What is Fabric Data Warehouse?
- **URL:** https://learn.microsoft.com/en-us/fabric/data-warehouse/data-warehousing
- **Summary:** "Enterprise scale relational warehouse on a data lake foundation." Stores data in **Delta tables (Parquet + transaction log)** in OneLake. **Primarily T-SQL**; **full multi-table ACID transactions**, materialized views, functions, stored procedures. Data **automatically replicated to OneLake Files** for external access. Storage and compute separated; **no-knobs** autonomous workload management.
- **Two items:** **Warehouse** (full DDL+DML) and **SQL analytics endpoint** (auto-generated on Lakehouse/SQL DB/Warehouse — *query/define objects but not modify data*).
- **Ingestion:** `COPY INTO`, Pipelines, Dataflows, `CTAS` (`CREATE TABLE AS SELECT`), `INSERT..SELECT`, `SELECT INTO`. **Cross-database queries** with **three-part names** (zero data duplication).
- **Choose Warehouse vs Lakehouse:** Warehouse = enterprise scale, open format, no-knobs, minimal setup, structured/semi-structured, beginner-and-pro; Lakehouse = large unstructured, Spark-first, with optional SQL endpoint. Both use the **same SQL engine**.
- **How it helps:** If the audit team is **T-SQL-first** or wants **multi-table transactional** integrity for (b)/(c) (e.g. atomically write a run row + update several dimension tables), Warehouse is the swap-in. Otherwise Lakehouse wins for the Python agent.

### 9. Decision guide — Warehouse vs Lakehouse (capability table)
- **URL:** https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-lakehouse-warehouse
- **Decision tree (verbatim):** Develop in **Spark → Lakehouse**, **T-SQL → Warehouse**. Need **multi-table transactions → Warehouse**, else **Lakehouse**. **Unstructured+structured / don't know → Lakehouse**; **structured only → Warehouse**.
- **Warehouse vs SQL analytics endpoint of Lakehouse (verbatim rows):**
  - *Primary capabilities:* Warehouse = **ACID, full DW with T-SQL transactions**; SQL endpoint = **Read-only**, system-generated, T-SQL querying over Delta tables + shortcut Delta folders.
  - *Data loading:* Warehouse = SQL, pipelines, dataflows; SQL endpoint = Spark, pipelines, dataflows, shortcuts.
  - *Delta support:* Warehouse = **reads and writes** Delta; SQL endpoint = **reads** Delta.
  - *T-SQL capabilities:* Warehouse = **Full DQL, DML, DDL + full transactions**; SQL endpoint = **Full DQL, No DML, limited DDL** (SQL Views and TVFs only).
  - *Storage layer:* both = **Open Data Format — Delta**.
- **How it helps:** The crisp confirmation that the Lakehouse SQL endpoint is **read-only** (writes go through Spark) while Warehouse offers **full DML**. Drives the (b)/(c) Lakehouse-vs-Warehouse choice.

### 10. OneLake shortcuts (share without copying)
- **URL:** https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts
- **Summary:** Shortcuts are OneLake objects that **point to other storage** (internal or external) — symbolic links; deleting a shortcut leaves the target intact. Make OneLake the single virtual lake.
- **Where:** created in **Lakehouses** and **KQL databases**. In Lakehouse **Tables** folder, top-level only; Delta-format targets auto-recognized as tables. In **Files** folder, any level, any format.
- **Internal shortcut targets:** KQL databases, Lakehouses, **Mirrored Azure Databricks Catalogs**, Mirrored Databases, Semantic models, SQL databases, **Warehouses**. *Item types need not match* — e.g. a **Lakehouse shortcut pointing at a Warehouse** (or at an Eventhouse OneLake table).
- **External targets:** Amazon S3 (+ S3-compatible), **ADLS Gen2**, Azure Blob, Dataverse, Google Cloud Storage, Iceberg, OneDrive/SharePoint; on-prem via OPDG gateway.
- **Cross-engine read examples (verbatim):**
  ```python
  # Spark
  df = spark.read.format("delta").load("Tables/MyShortcut"); display(df)
  ```
  ```sql
  -- SQL analytics endpoint
  SELECT TOP (100) * FROM [MyLakehouse].[dbo].[MyShortcut]
  ```
  ```kusto
  // KQL database — shortcuts are external tables
  external_table('MyShortcut') | take 100
  ```
- **Limits:** up to **100,000 shortcuts per item**; **10 shortcuts per OneLake path**; max **5** direct shortcut-to-shortcut links; no `%`/`+`/space/non-Latin chars; Table API may take up to a minute to recognize new shortcuts. **Direct Lake over SQL / Delegated identity mode** doesn't pass the calling user's identity through to the shortcut target (use **Direct Lake over OneLake** or **User identity mode**).
- **How it helps:** Shortcuts let the agent expose all three stores under one Lakehouse without copying: shortcut the **Eventhouse's OneLake telemetry table** + the **audit-history table** + **gold reporting** into one lakehouse for a unified Power BI semantic model, and **shortcut to Databricks Delta** (mirrored catalog) likewise.

### 11. OneLake security — data access roles / RBAC / service principals
- **URLs:**
  - Access control model: https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model
  - Get started with OneLake security: https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-onelake-security
  - SQL analytics endpoint OneLake security: https://learn.microsoft.com/en-us/fabric/onelake/security/sql-analytics-endpoint-onelake-security
- **Summary:** **OneLake security = RBAC** on OneLake data. Roles have four components: **Data** (tables/folders), **Permission**, **Members** (any Entra identity — user, group, or **non-user/SPN**), **Constraints** (row/column exclusions → RLS/CLS). Tables are folders under `Tables/`; **Read** grants access to data + metadata.
- **Warehouse / SQL endpoint enforcement:** **User identity mode** passes the signed-in user's identity to OneLake and read access is governed by OneLake roles/policies; this enables table/row/column-level security defined once in OneLake to flow through the SQL endpoint.
- **How it helps:** The agent is **read-only**; least-privilege via an SPN with **Read** OneLake roles (or T-SQL `GRANT SELECT`) on exactly the audit/reporting/telemetry tables. RLS/CLS via role Constraints if certain capacity/user attribution columns are sensitive.

### 12. Service principals in Fabric Data Warehouse (SP/automation access)
- **URL:** https://learn.microsoft.com/en-us/fabric/data-warehouse/service-principals
- **Summary:** SPN = non-interactive app identity for automation. **Enable** via tenant admin → "**Service principals can use Fabric APIs**" + workspace Admin grants the SPN access via **Manage access**.
- **Key facts:**
  - SPN with Admin/Member/Contributor workspace role can **create/read/update/delete** Warehouse via REST APIs. **Create warehouses with an SPN** (not a user) so they don't break when the owner leaves; SPNs avoid the 30-day interactive sign-in problem.
  - **Connect** via SSMS 19+ as **Microsoft Entra Service Principal**: username = App (client) ID, password = secret.
  - **Data-plane perms** via T-SQL: `GRANT SELECT ON <table> TO <service principal name>;` — least privilege.
  - **Token init:** SPNs can't bootstrap via portal — must make a **first Fabric REST API call** to establish the control-plane token (else external-storage / `COPY INTO` auth fails). Tokens renew every **30 days** (OAuth2 client-credentials). Store secret in **Azure Key Vault**.
  - **Monitoring:** SPN appears in `sys.dm_exec_sessions.login_name`, Query Insights `login_name`, DW Monitor `submitter`, Capacity Metrics **Client ID** under User.
  - **Limitations:** SPNs not supported for **Git APIs** (only Deployment pipeline APIs); SPNs **can't execute T-SQL Notebooks**.
- **Bootstrap commands (verbatim, Azure CLI / PowerShell):**
  ```azurecli
  az login --service-principal -u <APP_ID> -p <SECRET> --tenant <TENANT_ID>
  $accessToken = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
  # First API call establishes the control-plane token:
  $url = "https://api.fabric.microsoft.com/v1/workspaces/$workspaceId/items"
  Invoke-RestMethod -Method GET -Uri $url -Headers @{ Authorization = "Bearer $accessToken" }
  ```
- **How it helps:** The agent runs unattended → use an **SPN** to read/write its stores. Note the **token-bootstrap gotcha** (must hit a Fabric REST API once before SQL/COPY-INTO works) and that **SPNs can't run T-SQL notebooks** — so the agent's write path to (b)/(c) should be Spark notebooks/jobs or pipelines, or REST/TDS, not T-SQL notebooks.

### 13. Lakehouse Delta table maintenance — OPTIMIZE / VACUUM / V-Order
- **URLs:**
  - Table maintenance: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-table-maintenance
  - VACUUM: https://learn.microsoft.com/en-us/fabric/data-engineering/delta-lake-vacuum
  - V-Order: https://learn.microsoft.com/en-us/fabric/data-engineering/delta-optimization-and-v-order
- **Summary:** Keep Delta tables healthy by compacting small files, applying read optimizations, removing obsolete files.
  - **OPTIMIZE** — compacts small files; run after large ingestion/transformation.
  - **VACUUM** — removes stale files after updates/deletes/merges/OPTIMIZE; reclaims storage. **Don't set retention below 7 days** (affects time travel/readers/writers/recovery). Run **after** OPTIMIZE.
  - **V-Order** — write-time Parquet optimization (sorting/encoding/compression) for faster reads across Fabric engines (esp. Direct Lake/Power BI); ~**15% slower writes**, up to **50% more compression**.
  - **Execution:** ad-hoc (Lakehouse **Maintenance** action), or scheduled via notebooks/pipelines/REST; **Lakehouse Maintenance activity** (pipeline, Preview) runs OPTIMIZE (+optional V-Order) and VACUUM.
- **How it helps:** Because the audit-history table (b) is **append-per-sweep** (many small files over time) it will fragment — schedule **OPTIMIZE + VACUUM** (and V-Order on the gold reporting tables (c) for best Direct Lake/Power BI scan speed). *Contrast:* the Eventhouse handles this automatically via adaptive Parquet batching, so no manual maintenance for (a).

### 14. KQL time-series analysis (the query advantage for telemetry)
- **URLs:**
  - Time-series analysis: https://learn.microsoft.com/en-us/kusto/query/time-series-analysis?view=microsoft-fabric
  - Example queries in RTI: https://learn.microsoft.com/en-us/fabric/real-time-intelligence/query-table
  - `summarize`: https://learn.microsoft.com/en-us/kusto/query/summarize-operator?view=microsoft-fabric
  - `bin()`: https://learn.microsoft.com/en-us/kusto/query/bin-function?view=microsoft-fabric
- **Summary:** KQL natively creates/analyzes **thousands of time series in seconds** — built for near-real-time monitoring. `make-series` builds regular series and fills gaps; `bin()` rounds to intervals; `summarize` aggregates by group.
- **Exact query patterns:**
  ```kusto
  // Telemetry rollup: avg CU per 1-minute bucket over last 2 hours
  CapacityOverviewEvents
  | where Timestamp > ago(2h)
  | summarize avgCU = avg(CapacityUnits), maxCU = max(CapacityUnits)
      by bin(Timestamp, 1m), CapacityId

  // Regular series for anomaly detection across many capacities
  CapacityOverviewEvents
  | make-series cu = avg(CapacityUnits) on Timestamp
      from ago(7d) to now() step 5m by CapacityId
  | extend (anomalies, score, baseline) = series_decompose_anomalies(cu)

  // Throttling spike detection
  CapacityOverviewEvents
  | where Throttling > 0
  | summarize spikes = count() by bin(Timestamp, 30s), WorkspaceId
  ```
- **How it helps:** This is the **why-Eventhouse** for (a): `make-series` + `series_decompose_anomalies` + `bin()` are first-class for capacity/throttling anomaly detection at 30s granularity — far more natural and faster than equivalent windowed T-SQL/Spark over the same telemetry.

### 15. Eventstream → Eventhouse ingestion (the write path for (a))
- **URLs:**
  - Add Eventhouse destination: https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-destination-kql-database
  - Eventstreams overview: https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview
  - Custom app → KQL: https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/stream-real-time-events-from-custom-app-to-kusto
- **Summary:** Eventstream routes events to an Eventhouse destination from the **default or a derived stream**, in two modes:
  - **Direct ingestion** — events land in the Eventhouse unprocessed.
  - **Event processing before ingestion** — apply filter/aggregation/transform first (or after a derived stream).
- **How it helps:** If the 30s Capacity Overview Events arrive via Eventstream, route them **directly** to the Eventhouse KQL table; use **processed** mode if the agent wants to pre-filter/aggregate before storage. Alternatively the agent can push via SDK/REST or query-acceleration shortcut.

### 16. Data ingestion options in Microsoft Fabric (cross-store ingestion reference)
- **URL:** https://learn.microsoft.com/en-us/fabric/fundamentals/get-data
- **Summary:** Central index of get-data paths per store — **Eventstream** (real-time → Eventhouse/Lakehouse), **Pipelines** (copy activity), **Dataflows Gen2** (low-code), **Notebooks/Spark** (Lakehouse), **`COPY INTO`/T-SQL** (Warehouse), **shortcuts** (no-copy). Pairs with the pipeline-vs-dataflow-vs-Eventstream-vs-Spark decision guide: https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-pipeline-dataflow-spark
- **How it helps:** Confirms the per-store write paths used in the decision matrix above: Eventstream→Eventhouse for (a); Spark notebook/pipeline→Lakehouse for (b)/(c); `COPY INTO`/`INSERT`/`CTAS`→Warehouse if chosen.

---

## How the three stores interoperate (the OneLake spine)

```
                         ┌──────────────────────────── OneLake (single copy, open Delta-Parquet) ────────────────────────────┐
                         │                                                                                                    │
  Eventstream / SDK ───► │  EVENTHOUSE (KQL DB)  ──[OneLake availability: logical Delta copy, read-only, retention-bound]──►  │
   (30s telemetry, a)    │     KQL hot/cold cache                                                                             │
                         │                                                                                                    │
  Spark notebook/job ──► │  LAKEHOUSE  Tables/ (Delta)  ── auto SQL analytics endpoint (read-only T-SQL) ──────────────────►  │
   (audit hist b,        │     OPTIMIZE / VACUUM / V-Order maintenance                                                        │
    gold report c)       │                                                                                                    │
                         │  WAREHOUSE (optional swap for b/c)  ── full T-SQL DML + ACID ── auto-replicate to OneLake Files ─► │
  COPY INTO / CTAS ────► │                                                                                                    │
                         └────────────────────────────────────────────────────────────────────────────────────────────────┘
                              ▲ shortcuts (no-copy, any item→any item)        ▲ Direct Lake (no import)        ▲ ADLS Gen2 API
                              │                                               │                                │
                         (unify all 3 under one Lakehouse)              POWER BI semantic model           DATABRICKS (file 07)
```

- **Eventhouse → lake:** `OneLake availability` mirrors KQL data to Delta (no extra storage cost; retention-bound; up-to-3h default latency, tunable). Read by Spark/Power BI/Warehouse/Lakehouse.
- **Lakehouse/Warehouse → KQL:** `Eventhouse endpoint` shortcuts their Delta into a read-only KQL DB for time-series/anomaly KQL — no copy.
- **Any → any:** `shortcuts` (internal & external incl. Mirrored Databricks catalogs and ADLS Gen2) unify everything under one Lakehouse with **no duplication**.
- **→ Power BI:** **Direct Lake** reads Delta directly (no import/refresh) from Lakehouse, Warehouse, or Eventhouse-OneLake tables.
- **→ Databricks:** reads/writes the same OneLake Delta via the ADLS Gen2 API (detailed in file 07).

---

## Concrete recommendation for the bi-fabrics-audit-agent

1. **(a) Capacity Overview Events (30s) → Eventhouse / KQL database.**
   - Ingest via Eventstream (direct mode) or SDK/REST. Set **caching ≈ 7–14d**, **retention ≈ 90–365d** (cost control). Enable **OneLake availability** at the table level so Power BI (Direct Lake) and Databricks read the same telemetry as Delta — no copy, no extra storage cost. If BI needs <3h freshness, lower `TargetLatencyInMinutes`; for real-time, query KQL directly. Use `make-series`/`series_decompose_anomalies` for throttling/CU anomaly detection. Enable **Capacity Planner** if cold-start latency on time-sensitive queries is unacceptable.

2. **(b) Per-run audit history + (c) curated reporting tables → Lakehouse** (default, since the agent is Python/Spark).
   - Write Delta from Spark notebooks/jobs (append one row per sweep for b; medallion gold build for c). Free **read-only SQL analytics endpoint** serves analysts; **Direct Lake** serves Power BI. Schedule **OPTIMIZE + VACUUM** (b is append-heavy → small-file fragmentation) and **V-Order** on (c) for fastest Power BI scans.
   - **Swap to Warehouse** only if the team is **T-SQL-first** or needs **multi-table ACID transactions / full DML** for the run+dimension writes. Warehouse adds `COPY INTO`/`CTAS`/`INSERT` and cross-DB three-part-name queries.

3. **Unify for BI:** create **shortcuts** in one Lakehouse pointing at the Eventhouse OneLake telemetry table, the audit-history table, the gold tables, and (if relevant) the **Mirrored Databricks catalog** — then build a single Power BI semantic model in Direct Lake.

4. **Automation identity:** run the agent under an **SPN** with least-privilege OneLake **Read** roles (and T-SQL `GRANT SELECT` if Warehouse). Remember the **token-bootstrap** first-API-call requirement, the **30-day renewal**, Key Vault for the secret, and that **SPNs can't run T-SQL notebooks** (use Spark/pipelines/REST/TDS for writes).

---

## Flat URL list (all sources)

```
https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-data-store
https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-lakehouse-warehouse
https://learn.microsoft.com/en-us/fabric/fundamentals/store-data
https://learn.microsoft.com/en-us/azure/architecture/data-guide/technology-choices/fabric-analytical-data-stores
https://learn.microsoft.com/en-us/fabric/fundamentals/get-data
https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-pipeline-dataflow-spark
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse-as-endpoint
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/real-time-intelligence-consumption
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-policies
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/manage-monitor-database
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse-smart-capacity-control
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/one-logical-copy
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/query-acceleration-overview
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/create-database
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/database-shortcut
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-destination-kql-database
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/stream-real-time-events-from-custom-app-to-kusto
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/get-data-eventstream
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/query-table
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/kusto-query-set
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/query-cold-data-hot-windows
https://learn.microsoft.com/en-us/kusto/query/time-series-analysis?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/query/summarize-operator?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/query/bin-function?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/cache-policy?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/alter-table-cache-policy-command?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/alter-database-retention-policy-command?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/retentionpolicy?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/mirroring-policy?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/show-table-mirroring-operations-command?view=microsoft-fabric
https://learn.microsoft.com/en-us/kusto/management/alter-merge-mirroring-policy-command?view=microsoft-fabric
https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-overview
https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint
https://learn.microsoft.com/en-us/fabric/data-warehouse/get-started-lakehouse-sql-analytics-endpoint
https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-table-maintenance
https://learn.microsoft.com/en-us/fabric/data-engineering/delta-lake-vacuum
https://learn.microsoft.com/en-us/fabric/data-engineering/delta-optimization-and-v-order
https://learn.microsoft.com/en-us/fabric/data-factory/lakehouse-maintenance-activity
https://learn.microsoft.com/en-us/fabric/fundamentals/table-maintenance-optimization
https://learn.microsoft.com/en-us/fabric/fundamentals/delta-lake-overview
https://learn.microsoft.com/en-us/fabric/data-warehouse/data-warehousing
https://learn.microsoft.com/en-us/fabric/data-warehouse/get-started-lakehouse-sql-endpoint
https://learn.microsoft.com/en-us/fabric/data-warehouse/query-warehouse
https://learn.microsoft.com/en-us/fabric/data-warehouse/service-principals
https://learn.microsoft.com/en-us/fabric/data-warehouse/transactions
https://learn.microsoft.com/en-us/sql/t-sql/statements/copy-into-transact-sql?view=fabric
https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts
https://learn.microsoft.com/en-us/fabric/onelake/onelake-overview
https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model
https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-onelake-security
https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security
https://learn.microsoft.com/en-us/fabric/onelake/security/sql-analytics-endpoint-onelake-security
https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sharing
https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
https://learn.microsoft.com/en-us/fabric/enterprise/azure-billing
https://learn.microsoft.com/en-us/fabric/enterprise/surge-protection
```

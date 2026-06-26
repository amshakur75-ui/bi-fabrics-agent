# 19 — Fabric Data Engineering & Orchestration as a Runtime

Research for **bi-fabrics-audit-agent** (read-only Fabric/PBI capacity-audit agent, currently
Databricks-targeted) evaluating whether the Python collector/orchestrator could run **natively
inside Microsoft Fabric** as an alternative or hybrid runtime.

Scope of THIS doc = Fabric **Data Engineering + orchestration compute**. Deliberately NOT
re-covered (handled in earlier docs): Databricks Jobs/notebooks/Delta (docs 03/04), OneLake
security (doc 07/10), Eventhouse/Lakehouse/Warehouse storage choice (doc 15), Fabric Data Agents
(doc 10/MCP).

Reference date: 2026-06-23. All docs are learn.microsoft.com unless noted.

---

## 0. Executive takeaways (for the agent)

- A Fabric **notebook** (PySpark/Python) or **Spark Job Definition (SJD)** is a fully viable
  native home for the read-only collector. Both run on the same managed Spark compute, both can
  be parameterized, both can be triggered on-demand or scheduled via the **Fabric REST Job
  Scheduler API**, and both can be orchestrated by **Data Factory pipelines**.
- **Auth is the single biggest decision.** Inside a Fabric notebook you get a token to the
  Power BI / Fabric REST surface for free via `notebookutils.credentials.getToken('pbi')`. BUT
  under a **service principal** that token is scoped to only ~7 item scopes (no admin/scanner
  scopes); under a **user** it currently has full Fabric scope. For broad/admin read access
  (tenant scanner, capacity metrics) you must use **MSAL** with an explicit SP, exactly as on
  Databricks. So the collector's auth layer is largely portable.
- **Orchestration is strictly simpler than Databricks** for the "schedule N collectors, fan-out,
  collect exit values" pattern: `notebookutils.notebook.runMultiple(DAG)` gives in-session
  parallel DAG execution with retries; Data Factory pipelines give cross-item control flow; the
  Job Scheduler REST API gives external triggering. No cluster JSON, no jobs API polling glue.
- **Cost model differs fundamentally.** Fabric Spark bills against **capacity units** (the very
  thing the agent audits) with **bursting (3x) + smoothing**, not per-VM DBU. Running the audit
  agent inside Fabric consumes the audited capacity — a notable observer-effect consideration.

---

## 1. Fabric Notebooks (Spark / PySpark / Python)

### 1.1 Languages, kernels, parameter cells

**TITLE:** Develop, execute, and manage notebooks
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/author-execute-notebook
**Summary:** Authoritative notebook authoring/execution reference. Four languages: **PySpark
(Python), Spark (Scala), Spark SQL, SparkR**, plus a pure-Python kernel. Cells run remotely on a
Spark cluster.
**Exact identifiers:**
- Language cell magics: `%%pyspark`, `%%spark` (Scala), `%%sql`, `%%sparkr`, `%%html`, `%%csharp`.
- **Parameters cell**: "Toggle parameter cell" (ellipsis menu). Pipeline/`run()` execution injects
  a new cell beneath it overriding defaults. Supported parameter types: `int`, `float`, `bool`,
  `string` only. Complex `list`/`dict` must be JSON-serialized to a string and `json.loads`'d
  inside.
- Pipeline assigns values under **Base parameters** of the Notebook activity Settings.
- `%run <notebook>` references another notebook in the **same workspace** (shares variables); max
  nesting depth **5**; params `int/float/bool/string` only. `%run -b script.py` runs a file from
  the notebook's built-in **Resources** folder.
- **Secret redaction**: cell output auto-replaces secrets with `[REDACTED]` (Python/Scala/R).
- **Magic commands allowed in pipeline runs (only these):** `%%pyspark`, `%%spark`, `%%csharp`,
  `%%sql`, `%%configure`.
- IPython widgets supported (Python only); variable explorer (Python only).
**How it helps:** The collector can be a single parameterized notebook (tenant id, capacity ids,
look-back window passed as Base parameters), with shared helper logic in a referenced notebook via
`%run`. Secret redaction is a built-in safety net for a security-audit tool.

### 1.2 `%%configure` — per-session Spark/compute/lakehouse config

Same doc. The `%%configure` cell magic (must be the **first** cell, or session restarts) sets:
- `driverMemory` / `driverCores` / `executorMemory` / `executorCores` (recommended driver==executor)
- `jars` (abfss/wasbs paths), `conf` {} (Spark props; note `spark.driver.cores`,
  `spark.executor.cores`, `*.memory`, `spark.executor.instances` are **ignored** in `conf`)
- `defaultLakehouse` {name,id,workspaceId} — overrides pinned lakehouse for the session
- `mountPoints` [], `environment` {id,name}, `sessionTimeoutInSeconds`, `useStarterPool` (bool),
  `useWorkspacePool` (pool name).
- **Parameterized `%%configure`** for pipelines: each value can be
  `{"parameterName": "...", "defaultValue": ...}` overridden by the Notebook activity.
  (Note: scheduled notebook runs do **not** support parameterized session config.)
- Can also inject **Variable Library** values: `"variableName": "$(/**/myVL/LHname)"`.
**How it helps:** lets the collector notebook self-select a small pool / starter pool and a target
lakehouse for writing audit results, with dev/test/prod values via Variable Library.

### 1.3 NotebookUtils (formerly MSSparkUtils) — the runtime SDK

**TITLE:** NotebookUtils (former MSSparkUtils) for Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-utilities
**Summary:** Built-in package in every Fabric Spark notebook (and usable in pipelines). MsSparkUtils
renamed to **NotebookUtils** (backward compatible; `mssparkutils` namespace will retire). Requires
**Spark 3.4 / Runtime v1.2+**.
**Modules & namespaces (exact):**
- `notebookutils.fs` — file system (ADLS Gen2, Blob, Lakehouse) + mount/unmount
- `notebookutils.notebook` — run / runMultiple / exit / management
- `notebookutils.credentials` — `getToken`, `getSecret`, `putSecret`, `isValidToken`
- `notebookutils.lakehouse` — CRUD Lakehouse items/tables
- `notebookutils.runtime` — session context
- `notebookutils.session` — stop/restart interpreter
- `notebookutils.udf` — invoke User Data Functions
- `notebookutils.variableLibrary` — read Variable Library values
- Helpers: `notebookutils.help()`, `notebookutils.fs.help()`, etc.
- **Known issue:** `fabricClient` / `PBIClient` APIs listed by `help()` are **not yet supported**
  in runtime > 1.2 (future release).
**How it helps:** This is the in-Fabric equivalent of the dbutils surface. The collector uses
`credentials` for auth, `runtime` for self-context, `fs` for OneLake/ADLS reads, `notebook` for
orchestration.

### 1.4 `notebookutils.notebook` — run / runMultiple / exit (in-session orchestration)

**TITLE:** NotebookUtils notebook run and orchestration for Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-notebook-run
**Summary:** In-session notebook chaining + parallel DAG execution.
**Exact signatures:**
- `run(path: str, timeout_seconds: int = 90, arguments: dict = None, workspace: str = "") -> str`
  — runs a child notebook on the **caller's Spark pool**, returns the child's `exit()` string
  (empty string if no exit). Cross-workspace by passing a workspace **ID**; needs runtime **1.2+**.
- `runMultiple(dag: Any, config: dict = None) -> dict[str, dict[str, Any]]` — parallel/topological
  execution via **multithreading inside one Spark session** (shared compute). `config` Python-only.
- `validateDAG(dag) -> bool` — checks duplicate names, missing deps, cycles. Call before
  runMultiple in production.
- `exit(value: str)` — terminates current notebook with a string. Do **NOT** wrap in try/except
  (won't take effect); put in its own cell (overwrites cell output).
**DAG JSON fields (exact):** `activities[]` each with `name`(unique), `path`(notebook),
`timeoutPerCellInSeconds`(default 90), `args`{}, `workspace`(name|id), `retry`(default 0),
`retryIntervalInSeconds`(default 0), `dependencies`[]. Root: `timeoutInSeconds`(default 43200=12h),
`concurrency`(default = 3x CPU cores; `0`=unlimited).
- Pass exit values between activities: `args: {"data_path": "@activity('Extract').exitValue()"}`.
- `runMultiple` config example: `{"displayDAGViaGraphviz": False}`.
- **Failure handling:** `from notebookutils.common.exceptions import RunMultipleFailedException`;
  on raise, `ex.result` holds partial results. Each result dict = `{"exitVal": str,
  "exception": err|None}`.
- Reference-run constraint: child must use the **same lakehouse** as parent (or inherit/none) —
  bypass with `useRootDefaultLakehouse: True` in args.
- **NotebookUtils is NOT applicable to Spark Job Definitions (SJD).**
**How it helps:** The agent's "run all collectors, fan-out per capacity/workspace, gather results"
pattern maps directly onto a dynamically-built `runMultiple` DAG (the doc even shows a
`create_fan_out_dag(partitions)` pattern). Retries + per-activity exit values replace bespoke
Databricks task-orchestration glue. One shared session = cost-efficient.

### 1.5 `notebookutils.credentials` — AUTH (critical for Fabric/PBI API collection)

**TITLE:** NotebookUtils credentials utilities for Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-credentials
**Summary:** Entra token acquisition + Key Vault secrets, no creds in code.
**Exact methods:**
- `getToken(audience: str) -> str`. Audience keys: **`storage`** (ADLS/Blob), **`pbi`** (Power BI
  AND Fabric REST APIs), **`keyvault`** (AKV), **`kusto`** (RTA/ADX KQL).
- `getSecret(akvName: str, secret: str) -> str` (uses caller's user creds against AKV).
- `putSecret(akvName, secretName, secretValue)` (not in Scala API).
- `isValidToken(token) -> bool` (not in Scala API).
**Identity semantics (THE key finding):**
- `getToken('pbi')` under a **user** identity → full Fabric service scope (today).
- `getToken('pbi')` under a **service principal** → restricted to: `Lakehouse.ReadWrite.All`,
  `MLExperiment.ReadWrite.All`, `MLModel.ReadWrite.All`, `Notebook.ReadWrite.All`,
  `SparkJobDefinition.ReadWrite.All`, `Workspace.ReadWrite.All`, `Dataset.ReadWrite.All`. No admin
  /scanner/capacity scopes.
- For full Fabric scope under an SP → use **MSAL for Python** directly, not getToken.
- Fabric notebooks do **NOT** support `DefaultAzureCredential`; doc gives a custom
  `TokenCredential` wrapper class that feeds `getToken` into Azure SDK clients.
- Worked sample: `getToken('pbi')` → `requests.get('https://api.powerbi.com/v1.0/myorg/datasets')`.
**How it helps:** For ordinary workspace-scoped reads the collector can drop the entire MSAL/SP
secret dance and just call `getToken('pbi')`. For tenant-admin reads (Admin scanner APIs, capacity
metrics, per-user attribution) it still needs an SP via MSAL + Key Vault secret via `getSecret` —
i.e. the existing Databricks auth code is reusable nearly verbatim.

### 1.6 `notebookutils.runtime.context` — self-context

**TITLE:** NotebookUtils runtime context for Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-runtime
**Summary:** Read-only session context dict. Keys: `currentNotebookName/Id`,
`currentWorkspaceName/Id`, `defaultLakehouseName/Id/WorkspaceName/WorkspaceId`,
`currentRunId`/`parentRunId`/`rootRunId`, `isForPipeline`/`isForInteractive`/`isReferenceRun`,
`rootNotebookId/Name`, `activityId` (Livy job id), `clusterId`, `poolName`, `environmentId`/
`environmentWorkspaceId`, `userId`, `userName`, `currentKernel`, `productType` (e.g. `Fabric`).
**How it helps:** The collector self-identifies its workspace/capacity/pool for lineage and
audit-trail tagging, and branches on `isForPipeline` (strict error handling in scheduled runs vs
lenient interactive). `userName`/`userId` support attribution.

### 1.7 Notebook public REST API (CRUD + run + exit value)

**TITLE:** Manage and execute Fabric notebooks with public APIs
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-public-api
**Summary:** CRUD via Items API; execution via Job Scheduler.
**Exact identifiers:**
- Run on demand: `POST .../v1/workspaces/{workspaceId}/notebooks/{notebookId}/jobs/execute/instances?jobType=RunNotebook`
  with executionData supporting **parameterization, session/Spark settings, environment + runtime
  selection, target Lakehouse**.
- Status + exit: `GET .../jobs/execute/instances/{jobInstanceId}?beta=true` → response has
  `status` (Completed/Failed), `startTimeUtc`, `endTimeUtc`, `failureReason`, **`exitValue`**.
- Exit value set by `mssparkutils.notebook.exit("...")` (string; can be JSON).
- **Service principal auth supported** for both Items CRUD and Job Scheduler (add SP to workspace
  as Admin/Member/Contributor). Enables unattended CI/CD.
**How it helps:** An **external** orchestrator (Azure Function, Databricks, GitHub Action, the
agent's own control plane) can trigger the in-Fabric collector and poll its `exitValue` for
conditional branching — the hybrid runtime pattern.

---

## 2. Spark Job Definitions (SJD) — submit .py/.jar as a batch job

**TITLE:** Apache Spark job definition / Create / Run
**URLs:**
- https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-definition
- https://learn.microsoft.com/en-us/fabric/data-engineering/create-spark-job-definition
- https://learn.microsoft.com/en-us/fabric/data-engineering/run-spark-job-definition
**Summary:** An SJD is a Fabric code item that submits **batch/streaming** jobs to Spark. Upload a
PySpark `.py` (Language=PySpark) as the main definition file, or a `.jar` (Language=Spark
Scala/Java) with a **Main class name**. Add extra libraries + **command-line arguments**. Requires
**at least one associated lakehouse**.

### 2.1 SJD via REST API (programmatic create/run)

**TITLE:** Apache Spark job definition API tutorial
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-definition-api
**Summary:** 3 steps: (1) create SJD item via Items API, (2) upload main+lib files to OneLake via
the OneLake DFS API, (3) update SJD definition with abfss URLs.
**Exact identifiers:**
- Create: `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items` with
  `type:"SparkJobDefinition"`, `definition.format:"SparkJobDefinitionV1"`, part path
  `SparkJobDefinitionV1.json`, `payloadType:"InlineBase64"`.
- **SJD definition JSON keys:** `executableFile` (abfss path to main), `defaultLakehouseArtifactId`,
  `mainClass`, `additionalLakehouseIds[]`, `retryPolicy`, `commandLineArguments`,
  `additionalLibraryUris[]`, `language` (e.g. `"Python"`), `environmentArtifactId`.
- OneLake upload: 3 ops (create `?resource=file`, append `?action=append&position=`, flush
  `?action=flush&position={size}`) against
  `https://onelake.dfs.fabric.microsoft.com/{workspaceId}/{sjdArtifactId}/Main/main.py`. Needs a
  **storage**-audience token (`https://storage.azure.com/.default`).
- Update: `POST .../items/{sjdArtifactId}/updateDefinition`.
- Also see **SJD API v2** (single-call create/update of main + lib files):
  https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-definition-api-v2
- Run on-demand uses the Job Scheduler (see §4); SJD job type is `sparkjob`.
**How it helps:** If the collector is packaged as a standalone Python module/wheel rather than a
notebook, an SJD is the cleaner home — versioned `.py` + libs + CLI args, no interactive cell
state. `commandLineArguments` carries the audit parameters. Note **NotebookUtils does not work in
SJDs** — use MSAL directly for auth there.

---

## 3. Fabric Spark runtime, pools, concurrency, capacity consumption

### 3.1 Runtime versions

**TITLE:** Apache Spark runtime in Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/runtime
**Exact versions:**
| Component | Runtime 1.3 (GA, default) | Runtime 2.0 (Public Preview) |
|---|---|---|
| Apache Spark | 3.5.5 | 4.1 |
| OS | Mariner 2.0 | Mariner 3.0 |
| Java | 11 | 21 |
| Scala | 2.12.17 | 2.13.16 |
| Python | **3.11** | 3.13 |
| Delta Lake | 3.2 | 4.1 |
- New workspaces default to **Runtime 1.3**. Set per workspace (Workspace Settings > Data
  Engineering/Science > Spark > Environment) or per **Environment** item.
- **Native Execution Engine** (Gluten+Velox, C++ vectorized): toggle `spark.native.enabled`; up to
  ~6x faster on TPC-DS SF1000 Delta, ~83% compute-cost savings; falls back to JVM per-operator.
- V-Order on by default; ~100 built-in query optimizations; intelligent cache.
**How it helps:** Python **3.11** on Runtime 1.3 sets the floor for the collector's dependency
pins. NEE is a free perf/cost win if the agent does heavy Delta scans of audit data.

### 3.2 Compute: starter pools vs custom pools, node sizes, autoscale

**TITLE:** Apache Spark compute for Data Engineering and Data Science
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/spark-compute
**Exact facts:**
- **Starter pools**: always-on prewarmed **Medium** nodes; session start **5–10 s** with no custom
  libs/props. Medium-only — any other node size triggers on-demand start (2–5 min). Custom libs in
  Quick mode add 30 s–5 min; Full mode +1–3 min; **custom live pool** (Full snapshot pre-hydrated)
  back to ~5 s. Private Link / Managed VNet → starter pools unsupported, on-demand 2–5 min.
- **Custom pools**: choose node size, autoscale (min/max nodes), dynamic executor allocation.
  ~3 min cold start (live pool exception ~5 s). Default session expiry **20 min**; deallocates 2
  min after expiry.
- **Node sizes:** Small 4 vCore/32 GB; Medium 8/64; Large 16/128; X-Large 32/256; XX-Large 64/512.
  X-Large/XX-Large only on non-trial SKUs. Single-node pools allowed (min nodes = 1; driver+executor
  share, resources halved).
- **Capacity mapping:** **1 capacity unit = 2 Spark vCores**; **3x burst multiplier**. F64 = 64 CU
  = 128 base vCores → **384 with burst**. Example F64 max nodes: Small 96 / Medium 48 / Large 24 /
  X-Large 12 / XX-Large 6.
- **Billing:** charged only for active session compute; not for idle pool, cluster acquisition, or
  Spark-context init. (Creating pools is free.)
- Node:executor ratio always 1:1 (one node = driver).
**Supporting:** Create custom pools —
https://learn.microsoft.com/en-us/fabric/data-engineering/create-custom-spark-pools ;
Configure starter pools —
https://learn.microsoft.com/en-us/fabric/data-engineering/configure-starter-pools
**How it helps:** The collector is light → a **single-node Small custom pool** or the **starter
pool** is ideal (fast start, minimal CU burn). The agent should also surface these knobs because
they're exactly what it audits on the capacities it inspects.

### 3.3 Concurrency, queueing, bursting, smoothing, throttling

**TITLE:** Concurrency limits and queueing in Apache Spark for Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-concurrency-and-queueing
**Exact facts:**
- Core-based throttling; **FIFO** queue; auto-retry when cores free.
- Capacity-full submit → `[TooManyRequestsForCapacity] HTTP Response code 430`.
- **Queueing applies to background jobs** (pipeline-triggered notebooks, scheduler, SJDs); does
  **NOT** apply to interactive notebook jobs or the notebook public API (those get 430 instead).
- **Queue expiration = 24 h**, then resubmit manually. Throttled capacity → new jobs rejected, not
  queued.
- **Optimistic admission** by default (jobs admitted at minimum core requirement). See Job
  admission: https://learn.microsoft.com/en-us/fabric/data-engineering/job-admission-management
- **SKU table (Spark vCores / max-with-burst / queue limit):** F2 4/20/4 · F4 8/24/4 · F8 16/48/8 ·
  F16 32/96/16 · F32 64/192/32 · F64 128/384/64 · F128 256/768/128 · F256 512/1536/256 · F512
  1024/3072/512 · F1024 2048/6144/1024 · F2048 4096/12288/2048 · Trial 128/128/NA · FTL4 4/8/8.
- **Job-level bursting** can be disabled per capacity (Admin portal > Capacity settings > Data
  Engineering/Science > Spark Compute) so one job can't monopolize burst cores.

**TITLE:** Understand your Fabric capacity throttling / billing & utilization
**URLs:**
- https://learn.microsoft.com/en-us/fabric/enterprise/throttling
- https://learn.microsoft.com/en-us/fabric/data-engineering/billing-capacity-management-for-spark
- https://learn.microsoft.com/en-us/fabric/data-engineering/autoscale-billing-for-spark-overview
**Summary:** **Bursting** lets ops use up to 3x provisioned vCores; **smoothing** averages CU usage
forward (interactive 5–64 min; background over 24 h). With **Autoscale Billing for Spark** enabled,
Spark runs **pay-as-you-go**, separate from capacity — **bursting & smoothing do NOT apply** (total
Spark vCores = 2x max CU in autoscale settings).
**How it helps:** Directly informs both (a) the agent's *own* footprint when running in Fabric, and
(b) its audit logic — these are the consumption/throttling rules it must reason about. A scheduled
in-Fabric collector is a **background job** → queued + smoothed over 24 h, minimizing spike impact
on the very capacity under audit.

---

## 4. Fabric REST Job Scheduler API (run on-demand / schedule ANY item)

**TITLE:** Job Scheduler — Run On Demand Item Job (Core REST API)
**URL:** https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job
**Exact identifiers:**
- `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/instances`
  (jobType now in path; legacy `?jobType=` query still works).
- **jobType values:** `RunNotebook` (notebook), `sparkjob` (SJD), `Pipeline` (pipeline),
  `DefaultJob` (e.g. lakehouse table maintenance), `Execute`, etc.
- Body: `executionData` (fixed static data per item type) + `parameters[]` (`name`, `type`,
  `value`). **ItemJobParameterType** enum: `VariableReference`, `Integer`, `Number`, `Text`,
  `Boolean`, `DateTime` (UTC `YYYY-MM-DDTHH:mm:ssZ`), `Guid`, `Automatic`. Note `parameters` is
  **not** supported for all item types (returns `FeatureNotAvailable`).
- Response **202 Accepted** with `Location` header (job instance URL) + `Retry-After` (seconds).
- Errors: `InsufficientPrivileges`, `InvalidJobType`, `TooManyRequestsForJobs`, `ItemNotFound`,
  `429 Too Many Requests`.
- **Required delegated scopes:** generic `Item.Execute.All` or specific `{itemType}.Execute.All`
  (e.g. `Notebook.Execute.All`). **Identities supported: User AND Service principal / Managed
  identity.**
**Related ops (same Job Scheduler service):** Get Item Job Instance, List Item Job Instances,
Cancel Item Job Instance, **Create/Get/List/Update/Delete Item Schedule** (max **20 schedulers per
item**). Schedule body: `enabled`, `configuration` {`startDateTime`, `endDateTime`,
`localTimeZoneId`, `type`:`Cron`, `interval`}.
**URL (scheduler index):** https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler
**How it helps:** This is the universal external trigger for the in-Fabric collector — one endpoint
works for notebook, SJD, or pipeline. SP/MI support means the agent's control plane can run it
unattended. Schedules replace any need for an external cron when the agent runs natively.

---

## 5. Fabric Data Factory — Data pipelines (orchestration)

### 5.1 Activities

**TITLE:** Activity overview (Data Factory in Fabric)
**URL:** https://learn.microsoft.com/en-us/fabric/data-factory/activity-overview
**Activity inventory (exact):**
- **Movement:** Copy data, Copy job.
- **Transform/compute:** Dataflow Gen2, **Fabric Notebook**, **Spark Job Definition**, HDInsight,
  Stored Procedure, SQL script, Delete data.
- **Control flow:** Append/Set variable, Approval, Azure Batch, **Azure Databricks** (Notebook/Jar/
  Python), Azure ML, Deactivate, **Fail**, **Filter**, **ForEach**, **Functions** (Azure Function),
  **Get metadata**, **If condition**, **Invoke pipeline** (Execute Pipeline), **KQL**, Lakehouse
  maintenance, Refresh Materialized Lake View, Refresh SQL Endpoint, **Lookup**, Set Variable,
  **Switch**, **Teams** (post message), **Until**, **Wait**, **Web** (custom REST), **Webhook**
  (callback-gated).
- **General settings:** Timeout default **12 h**, max **7 days** (D.HH:MM:SS); **Enable retries**
  (1–1000, default 1), **Retry interval** default 30 s, **Retry conditions (preview)** match on
  Error message / Failure type / Error code (e.g. `429`). Secure input/output. Deactivate activity.
  **Up to 120 activities per pipeline** (incl. inner).
**How it helps:** A pipeline can orchestrate the whole audit: **Web/Functions** activities to hit
PBI/Fabric admin REST endpoints, **Notebook/SJD** for the heavy collection, **ForEach** over
capacities/workspaces, **If/Switch** on findings severity, **Teams** to push alerts (ties to doc
12), **Invoke pipeline** for modular sub-audits, **Lookup** to read a config table. Retry-on-429 is
built-in — important against PBI API rate limits.

### 5.2 Notebook activity specifics

**TITLE:** Notebook activity
**URL:** https://learn.microsoft.com/en-us/fabric/data-factory/notebook-activity
**Exact identifiers:** Settings tab → **Connection** (auth method), **Notebook** dropdown, **Base
parameters**. **Session tag** reuses an existing Spark session to cut startup (requires **High
concurrency mode for pipelines** in Workspace settings).
- **Workspace Identity (WI) auth in the activity:** create WI in the pipeline's workspace; enable
  tenant setting *Service principals can call Fabric public APIs*; grant WI **Contributor** on the
  workspace(s). Lets the pipeline run the notebook as the WI (no user/SP secret).
- Known issue: SP running a notebook with **Semantic Link** code has limited functionality.
**How it helps:** Confirms a pipeline can run the collector notebook under a **Workspace Identity**
— a keyless SP-equivalent — which is the cleanest unattended-auth story for a scheduled audit.

### 5.3 Pipeline REST API (run / schedule / monitor)

**TITLE:** REST API capabilities for Fabric Data Factory
**URL:** https://learn.microsoft.com/en-us/fabric/data-factory/pipeline-rest-api-capabilities
**Exact identifiers:**
- Create pipeline: `POST .../v1/workspaces/{workspaceId}/items` with `type:"DataPipeline"`
  (optionally `definition.parts[]` base64 `pipeline-content.json`, `payloadType:"InlineBase64"`).
- Run on demand: `POST .../items/{itemId}/jobs/instances?jobType=Pipeline` with
  `executionData.parameters`/`pipelineName`/`OwnerUserPrincipalName`/`OwnerUserObjectId` → **202**.
- Get/Cancel job instance; **Schedule** ops (Create/Update/Delete/List/Get Pipeline Schedule),
  config `type:"Cron"`, `interval`, `localTimeZoneId`, start/end.
- **Query activity runs:** `POST .../datapipelines/pipelineruns/{jobId}/queryactivityruns` (filters,
  orderBy, lastUpdatedAfter/Before) → per-activity status/timings/errors.
- **Token scopes:** `Workspace.ReadWrite.All`, `Item.ReadWrite.All`. **SPN supported.**
- Limitation noted: "Run APIs can be invoked, but the actual run never succeeds" for some items
  just like UI run/refresh (verify per item type).
**How it helps:** External programmatic trigger + fine-grained run telemetry (`queryactivityruns`)
the agent can ingest to monitor its own collection pipeline.

---

## 6. Dataflows Gen2 (Power Query ingestion)

**TITLE:** Differences between Dataflow Gen1 and Dataflow Gen2
**URL:** https://learn.microsoft.com/en-us/fabric/data-factory/dataflows-gen2-overview
**Summary:** Low-code Power Query ingestion/transform; 300+ transforms; hundreds of connectors.
**Destinations:** Azure SQL DB, Azure Data Explorer (Kusto), ADLS Gen2, **Fabric Lakehouse Tables**,
Fabric Lakehouse Files, **Fabric Warehouse**, Fabric KQL DB, Fabric SQL DB, SharePoint, Snowflake.
- **Refresh only changed data** (incremental); Monitoring Hub + Refresh History; runs in pipelines
  via the **Dataflow activity** or on a **schedule**.
- High-perf compute auto-creates `DataflowsStagingLakehouse` / `DataflowsStagingWarehouse`.
- As of **April 2026** all new Dataflow Gen2 = **CI/CD + Git** by default (classic retired for new).
- Requires Fabric / trial / Power BI Premium capacity.
**How it helps:** Lowest-code option for pulling tabular admin/REST data (e.g. an OData/REST feed of
workspaces, capacities, activity events) straight into a Lakehouse the collector then analyzes —
but it's GUI-centric and less expressive than the Python collector. Best as an optional ingestion
front-end, not the core engine.

---

## 7. Environments (custom libraries / pip / Spark properties)

### 7.1 Environment item

**TITLE:** Create, Configure, and Use an Environment in Fabric
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/create-and-use-environment
**Summary:** Workspace item defining reusable Spark session config: **Spark runtime + compute**,
**Libraries**, **Resources** (small files, real-time, no publish). Attach to a notebook or SJD via
the **Environment** dropdown, or set as **workspace default** (Workspace settings > Data
Engineering/Science > Spark > Environment > **Set default environment** toggle).
- **Publish modes:** **Quick** (~5 s publish, libs install at session start, good for dev) vs
  **Full** (stable reproducible snapshot, 3–6 min publish + 1–3 min session start; for
  pipelines/scheduled/shared). Full + **custom live pool** → ~5 s starts.
- Cross-workspace attach: same capacity + network settings required; compute config from the other
  workspace is **ignored** (uses current workspace pool).
- Delete via REST: `DELETE .../v1/workspaces/{workspaceId}/environments/{environmentId}` (perm:
  `Environment.ReadWrite.All` or `Item.ReadWrite.All`).
**Supporting:** Compute config —
https://learn.microsoft.com/en-us/fabric/data-engineering/environment-manage-compute ;
Library mgmt in environments —
https://learn.microsoft.com/en-us/fabric/data-engineering/environment-manage-library ;
Environment Public API —
https://learn.microsoft.com/en-us/fabric/data-engineering/environment-public-api
**How it helps:** Pin the collector's deps (e.g. `msal`, `azure-identity`, `requests`, custom
audit wheel) in a **Full-mode Environment** so every scheduled run is reproducible — the Fabric
analogue of a Databricks cluster library spec / job environment.

### 7.2 Library management (public / custom / inline)

**TITLE:** Manage Apache Spark libraries
**URL:** https://learn.microsoft.com/en-us/fabric/data-engineering/library-management
**Exact facts:**
- **Public:** PyPI & Conda (env-managed or inline). **Custom:** `.whl` (Python), `.jar`, `.tar.gz`
  (R only) — env-managed or inline. R public (CRAN) is **inline only** (`install.packages`).
- **Inline:** `%pip install` / `%conda install` (session-scoped, lost across sessions). Use
  **`%pip` not `!pip`** (`!pip` = driver only, ignores conflicts; `%pip` = driver+executor).
  Restarts the Python interpreter → put at top of notebook; **not supported in High Concurrency
  mode** or in `runMultiple` reference runs.
- Inline pip is **disabled in pipeline runs by default** — enable via notebook-activity boolean
  param **`_inlineInstallationEnabled = True`**.
- Custom wheel from Resources folder: `%pip install "builtin/wheel_file_name.whl"`.
- Add JARs via `%%configure -f` → `conf.spark.jars` = abfss path.
**How it helps:** Tells the agent exactly how to ship its own Python package into Fabric (Full-mode
Environment with the `.whl` for prod; `%pip` for quick experiments), and the pipeline gotcha
(`_inlineInstallationEnabled`).

---

## 8. Running the collector in Fabric vs Databricks — comparison

| Dimension | Fabric notebook / SJD | Databricks (current target) |
|---|---|---|
| **Auth to Fabric/PBI REST** | `notebookutils.credentials.getToken('pbi')` free (user=full scope; **SP=7 item scopes only**). Admin/scanner/capacity scopes → **MSAL+SP** (Key Vault secret via `getSecret`). Pipeline can run as **Workspace Identity** (keyless SP). | MSAL/SP token from secret scope; identical pattern for admin scopes. |
| **Auth to ADLS/OneLake** | `getToken('storage')` or Workspace Identity + trusted access. | SP / OAuth passthrough. |
| **Orchestration** | `runMultiple` DAG (in-session, retries, exit values); Data Factory pipelines; Job Scheduler REST (`jobType` = RunNotebook/sparkjob/Pipeline). | Jobs API + tasks DAG; needs polling glue. |
| **Scheduling** | Item schedules (≤20/item, Cron) or pipeline schedule; no external cron needed. | Jobs schedules / external. |
| **Cost model** | **Capacity CU**, 1 CU = 2 vCores, 3x burst + smoothing; or Autoscale Billing PAYG. Bills active session only. **Consumes the audited capacity** (observer effect). | Per-VM DBU on separate compute; no impact on audited Fabric capacity. |
| **Compute simplicity** | Starter pool ~5–10 s start; single-node Small pool for light collector; no VM mgmt. | Cluster config / pools / autoscale to manage. |
| **Packaging** | Notebook + `%run` helpers, or SJD `.py`/`.jar`; deps via Full-mode Environment `.whl`. NotebookUtils **not** in SJDs. | Wheel + cluster libs. |
| **Data landing** | Native Lakehouse/Warehouse/Eventhouse write (no egress). | Write back to OneLake via connector. |
| **Maturity gaps** | `fabricClient`/`PBIClient` helpers not yet GA (>1.2); `parameters[]` not supported for all item types; SP+Semantic Link limited. | Mature Jobs/REST surface. |

**Net:** Fabric-native is **simpler to orchestrate and cheaper to wire** (no cross-cloud egress,
built-in scheduler/DAG/auth), but it **runs on and consumes the audited capacity** and inherits
SP-scope limits that still force MSAL for admin/scanner reads. A **hybrid** model is attractive:
keep the agent's control plane external (Databricks/Function), trigger an in-Fabric notebook/SJD
via the Job Scheduler REST API for cheap close-to-data collection, and read its `exitValue`.

---

## 9. Flat URL list

1. https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-utilities
2. https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-notebook-run
3. https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-credentials
4. https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-runtime
5. https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-notebook-management
6. https://learn.microsoft.com/en-us/fabric/data-engineering/author-execute-notebook
7. https://learn.microsoft.com/en-us/fabric/data-engineering/how-to-use-notebook
8. https://learn.microsoft.com/en-us/fabric/data-engineering/using-python-experience-on-notebook
9. https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-public-api
10. https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-definition
11. https://learn.microsoft.com/en-us/fabric/data-engineering/create-spark-job-definition
12. https://learn.microsoft.com/en-us/fabric/data-engineering/run-spark-job-definition
13. https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-definition-api
14. https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-definition-api-v2
15. https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/spark-job-definition
16. https://learn.microsoft.com/en-us/fabric/data-factory/spark-job-definition-activity
17. https://learn.microsoft.com/en-us/fabric/data-engineering/runtime
18. https://learn.microsoft.com/en-us/fabric/data-engineering/spark-compute
19. https://learn.microsoft.com/en-us/fabric/data-engineering/create-custom-spark-pools
20. https://learn.microsoft.com/en-us/fabric/data-engineering/configure-starter-pools
21. https://learn.microsoft.com/en-us/fabric/data-engineering/spark-job-concurrency-and-queueing
22. https://learn.microsoft.com/en-us/fabric/data-engineering/job-queueing-for-fabric-spark
23. https://learn.microsoft.com/en-us/fabric/data-engineering/job-admission-management
24. https://learn.microsoft.com/en-us/fabric/data-engineering/job-concurrency-queue-monitoring
25. https://learn.microsoft.com/en-us/fabric/data-engineering/billing-capacity-management-for-spark
26. https://learn.microsoft.com/en-us/fabric/data-engineering/autoscale-billing-for-spark-overview
27. https://learn.microsoft.com/en-us/fabric/enterprise/throttling
28. https://learn.microsoft.com/en-us/fabric/data-engineering/capacity-settings-management
29. https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler
30. https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job
31. https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/create-item-schedule
32. https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/get-item-job-instance
33. https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/list-item-job-instances
34. https://learn.microsoft.com/en-us/fabric/data-factory/activity-overview
35. https://learn.microsoft.com/en-us/fabric/data-factory/notebook-activity
36. https://learn.microsoft.com/en-us/fabric/data-factory/spark-job-definition-activity
37. https://learn.microsoft.com/en-us/fabric/data-factory/pipeline-rest-api-capabilities
38. https://learn.microsoft.com/en-us/fabric/data-factory/dataflows-gen2-overview
39. https://learn.microsoft.com/en-us/fabric/data-engineering/create-and-use-environment
40. https://learn.microsoft.com/en-us/fabric/data-engineering/environment-manage-library
41. https://learn.microsoft.com/en-us/fabric/data-engineering/environment-manage-compute
42. https://learn.microsoft.com/en-us/fabric/data-engineering/environment-public-api
43. https://learn.microsoft.com/en-us/fabric/data-engineering/library-management
44. https://learn.microsoft.com/en-us/fabric/data-engineering/native-execution-engine-overview
45. https://learn.microsoft.com/en-us/fabric/security/workspace-identity
46. https://learn.microsoft.com/en-us/fabric/security/workspace-identity-authenticate
47. https://learn.microsoft.com/en-us/rest/api/fabric/articles/identity-support
48. https://learn.microsoft.com/en-us/rest/api/fabric/core/items
49. https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-1-3
50. https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-2-0

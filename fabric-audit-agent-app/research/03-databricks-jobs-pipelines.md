# Research 03 — Databricks Jobs/Workflows + Lakeflow Declarative Pipelines (DLT)

**Scope:** Scheduling, orchestration, and alerting for the **read-only Fabric/PBI capacity audit sweep** (`bi-fabrics-audit-agent`). Focus: Lakeflow Jobs (formerly Databricks Workflows / Jobs) task types, triggers, serverless compute, parameters + dynamic value references, retries/timeouts/notifications/alerts, dependencies + run-if, compute models, the Jobs REST API, and Lakeflow Declarative Pipelines (DLT) overview/when-to-use.
**Date:** 2026-06-23. **Product naming note:** "Databricks Workflows / Jobs" is now branded **Lakeflow Jobs**; "Delta Live Tables (DLT)" is now **Lakeflow Spark Declarative Pipelines (SDP / LDP)**. Existing DLT/`@dlt` code still works unchanged.
**Out of scope (already researched elsewhere — not re-covered):** Asset Bundle YAML representation, UC volumes basics, secrets, OAuth/scopes, Fabric/PBI REST, Databricks Apps/MCP/Mosaic AI, databricks-sdk basics. Where a doc only existed in the bundle/YAML form, the *feature semantics* are described and YAML examples are kept minimal.

> NOTE ON METHOD: Direct page fetch (WebFetch) was blocked by the sandbox network for the entire session (ECONNREFUSED across all hosts); the firecrawl CLI was not installed. All findings below are drawn from Databricks/Microsoft Learn documentation via web search result extraction. Exact identifiers (field/key names, enum values) are reproduced verbatim where surfaced. A few low-level API fields (e.g. `idempotency_token`) were not surfaced in snippets and are flagged as "verify on the API reference page" with the canonical URL.

---

## A. Lakeflow Jobs — overview & how it anchors the audit agent

### A1. Lakeflow Jobs (index / overview)
- **URL:** https://docs.databricks.com/aws/en/jobs/ (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/)
- **Summary:** A job is the primary unit for scheduling and orchestrating production workloads on Databricks. A job has one or more **tasks**; tasks form a DAG via dependencies. Jobs can be triggered manually, on a schedule, by source-table updates, on file arrival, or run continuously. Jobs run on job clusters, serverless compute, or (less recommended for prod) all-purpose compute. Created/managed via UI, Jobs REST API, Databricks SDK, CLI, or Asset Bundles.
- **How it helps:** This is the scheduling/operating backbone for the read-only sweep. A single "Fabric capacity audit" job can sequence collectors → detectors → reasoner → alerter as tasks, run nightly (or on a tighter cadence), and fan out per-capacity work with `for_each`.

### A2. Configure & edit Lakeflow Jobs
- **URL:** https://docs.databricks.com/aws/en/jobs/configure-job (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/configure-job)
- **Summary:** Job-level settings: name, tags, tasks, schedules/triggers, **job parameters**, **`max_concurrent_runs`**, notifications, job-level timeout/health rules, run-as identity, permissions, queueing. Job parameters are pushed down to all tasks.
- **How it helps:** Set `max_concurrent_runs=1` so a slow audit run never overlaps the next scheduled one; attach job parameters (`reference_date`, `tenant_id`, capacity scope) consumed by every task.

---

## B. Task types (exact identifier keys)

Primary docs:
- **Configure & edit tasks:** https://docs.databricks.com/aws/en/jobs/configure-task (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/configure-task)
- **Task type reference (bundle page, but enumerates every key):** https://docs.databricks.com/aws/en/dev-tools/bundles/job-task-types (Azure: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/job-task-types)

**Full task type list (UI names):** Notebook, Python script, Python wheel, SQL, Pipeline, SQL Alert, Dashboards, Power BI, dbt, dbt platform, JAR, Spark Submit, Run Job, If/else, For each.

Exact API/identifier keys per task type:

| Task type | Key | What it does / key sub-fields |
|---|---|---|
| Notebook | `notebook_task` | Runs a notebook; fields `notebook_path`, `base_parameters` (key→value map; consumed via `dbutils.widgets`), `source` (WORKSPACE/GIT), optional `warehouse_id` for SQL notebooks. |
| Python script | `spark_python_task` | Runs a `.py` script; fields `python_file`, `parameters` (positional list, passed as CLI argv), `source`. |
| Python wheel | `python_wheel_task` | Runs an entry point in an installed wheel; fields `package_name`, `entry_point`, `parameters` (positional) and/or `named_parameters` (key→value). |
| SQL | `sql_task` | Runs a Databricks SQL **query**, **file** (`.sql` with `;`-separated statements), **legacy dashboard**, or **alert**; requires `warehouse_id`; `parameters` key→value (alerts excepted). Sub-objects: `sql_task.query`, `sql_task.file`, `sql_task.dashboard`, `sql_task.alert`. |
| Pipeline | `pipeline_task` | Triggers a Lakeflow Declarative Pipeline (materialized view / streaming table); fields `pipeline_id`, `full_refresh`. |
| dbt | `dbt_task` | Runs dbt CLI commands; fields `commands` (e.g. `dbt deps`, `dbt seed`, `dbt run`), `project_directory`, `profiles_directory`, `warehouse_id`, `catalog`, `schema`. |
| JAR | `spark_jar_task` | Runs a JAR main class; `main_class_name`, `parameters`. |
| Spark Submit | `spark_submit_task` | `parameters` (spark-submit args). |
| Run Job | `run_job_task` | Runs **another existing job** as a task; fields `job_id`, plus param passthrough (`job_parameters`). Enables job composition/modularization. |
| If/else (condition) | `condition_task` | Branch the DAG on a boolean; fields `op`, `left`, `right` (see §C). |
| For each | `for_each_task` | Loop a nested task over an input array; fields `inputs`, `concurrency`, `task` (the nested task) (see §C). |
| Power BI | (Power BI task) | Triggers a Power BI semantic-model refresh from a job (relevant adjacency — but Fabric/PBI REST is out of scope here). |

- **How it helps:** The audit sweep maps cleanly: `python_wheel_task` or `spark_python_task` to run the collector/detector package; `notebook_task` for ad-hoc/exploratory steps; `sql_task` (file) to query the `system.*` billing/lakeflow tables or a Kusto-backed materialization; `run_job_task` to compose a reusable "alert" sub-job; `pipeline_task` if telemetry is materialized via LDP. `for_each` + `condition` give per-capacity fan-out and "only alert if breach" branching.

---

## C. Control flow — dependencies, run-if, condition (if/else), for_each

### C1. Control the flow of tasks within Lakeflow Jobs
- **URL:** https://docs.databricks.com/aws/en/jobs/control-flow (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/control-flow)
- **Summary:** Overview of building DAGs with `depends_on`, conditional execution (`run_if`), branching (`condition_task` / If-else), and looping (`for_each_task`).

### C2. Task dependencies + Run-if conditions
- **URL:** https://docs.databricks.com/aws/en/jobs/run-if (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/run-if)
- **Summary:** `depends_on` lists upstream tasks. **`run_if`** controls whether a task runs given upstream outcomes. Exact enum values:
  - `ALL_SUCCESS` — **All succeeded** (default): all dependencies ran and succeeded.
  - `AT_LEAST_ONE_SUCCESS` — At least one dependency succeeded.
  - `NONE_FAILED` — None of the dependencies failed, and at least one ran.
  - `ALL_DONE` — All dependencies ran regardless of result (use to always run cleanup/notify).
  - `AT_LEAST_ONE_FAILED` — At least one dependency failed.
  - `ALL_FAILED` — All dependencies failed.
  - **Behavior:** Skipped/excluded upstream tasks are treated as **successful** when evaluating `run_if`. If all dependencies are excluded, the task is also excluded regardless of its `run_if`.
- **How it helps:** Wire a "send alert" task with `run_if = AT_LEAST_ONE_FAILED` (or branch on detector output) so notifications fire only on a real capacity breach; a final `ALL_DONE` task can always write the run summary/heartbeat.

### C3. If/else (condition) task
- **URL:** https://docs.databricks.com/aws/en/jobs/if-else (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/if-else)
- **Summary:** `condition_task` evaluates `left <op> right` to true/false; downstream tasks attach to the **true**/**false** branch. **Operators (`op`):** `EQUAL_TO`, `NOT_EQUAL`, `GREATER_THAN`, `GREATER_THAN_OR_EQUAL`, `LESS_THAN`, `LESS_THAN_OR_EQUAL`. `left`/`right` are strings that commonly embed **dynamic value references** (e.g. `{{job.repair_count}}`, `{{tasks.<t>.values.<k>}}`). Numeric comparison if both parse as numbers, else lexicographic.
- **Example:** `condition_task: { op: LESS_THAN, left: "{{job.repair_count}}", right: "5" }`.
- **How it helps:** Branch on a detector's task value, e.g. `left={{tasks.detect.values.max_cu_pct}} op=GREATER_THAN right=90` → true branch runs the Teams/email alert task; false branch ends quietly.

### C4. For each task
- **URL:** https://docs.databricks.com/aws/en/jobs/for-each (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/for-each)
- **Lookup-table variant (large arrays):** https://docs.databricks.com/aws/en/jobs/for-each-lookup-example
- **Summary:** `for_each_task` runs a single **nested task** once per element of an input array. Fields: **`inputs`** (JSON array of values/objects; may use `{{tasks.<task_name>.values.<task_value_name>}}` to source the array from an upstream task value), **`concurrency`** (parallel iterations, default **1**), and **`task`** (the nested task). Inside the nested task, reference the current element with **`{{input}}`** (scalar) or **`{{input.<key>}}`** (object field). For very large arrays, use the lookup-table pattern to avoid array-size limits.
- **How it helps:** Iterate over every Fabric capacity / workspace: `inputs` = list of capacity IDs (possibly produced by an upstream "enumerate capacities" task value), nested task = "audit one capacity" with `{{input.capacity_id}}`. Set `concurrency` > 1 to sweep many capacities in parallel while keeping it read-only.

---

## D. Triggers & schedules

### D1. Automate jobs with schedules and triggers (hub)
- **URL:** https://docs.databricks.com/aws/en/jobs/triggers (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/triggers)
- **Summary:** Five trigger types: **Scheduled**, **Table update**, **File arrival**, **Model update**, **Continuous**. Managed in the "Schedules & Triggers" section of the job, or via the `trigger` object on `jobs/create`, `jobs/update`, `jobs/reset`. **Databricks enforces a minimum 10-second interval between subsequent scheduled runs** regardless of the cron expression.

### D2. Scheduled (cron / quartz)
- **URL:** https://docs.databricks.com/aws/en/jobs/scheduled (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/scheduled)
- **Summary:** Trigger type = **Scheduled**, with **Schedule type** = **Simple** (every N minutes/hours/days/weeks at a time) or **Advanced**. The **"Show cron syntax"** checkbox exposes a **Quartz cron** expression + **timezone ID**. API field: `schedule` with `quartz_cron_expression`, `timezone_id`, and `pause_status` (UNPAUSED/PAUSED). Quartz format is 6–7 fields (seconds minutes hours day-of-month month day-of-week [year]).
- **How it helps:** Primary mechanism — schedule the nightly read-only sweep, e.g. Quartz `0 0 6 * * ?` (06:00 daily) with `timezone_id` set to the tenant's reporting timezone. Use `pause_status` to disable the sweep without deleting the job.

### D3. Periodic trigger
- **URL:** https://docs.databricks.com/aws/en/jobs/triggers (periodic section) + bundle reference https://docs.databricks.com/aws/en/dev-tools/bundles/job-task-types
- **Summary:** A simpler interval trigger expressed as **`periodic: { interval: <int>, unit: <UNIT> }`** where **`unit` ∈ {`MINUTES`, `HOURS`, `DAYS`, `WEEKS`}**. Example: `trigger: { periodic: { interval: 1, unit: DAYS } }` runs exactly one day from the last run (interval measured run-to-run, not wall-clock-aligned like cron).
- **How it helps:** When the audit cadence is "every N hours from last completion" rather than a fixed clock time, `periodic` is cleaner than cron and naturally avoids drift/overlap.

### D4. File arrival trigger
- **URL:** https://docs.databricks.com/aws/en/jobs/file-arrival-triggers (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/file-arrival-triggers)
- **Summary:** Trigger when new files appear under a **Unity Catalog external location** or **UC volume** (root or subpath) — set in **Storage location**. Requires a UC volume or external location; Databricks recommends enabling **managed file events** on the external location (volumes get file events by default). Advanced options:
  - **`min_time_between_triggers`** (a.k.a. "Minimum time between triggers in seconds") — min wait after a previous run completes before a new run; files arriving in this window trigger only after it expires (rate-limits run creation).
  - **`wait_after_last_change`** (a.k.a. "Wait after last change in seconds") — wait after the latest file arrival before firing; another arrival in the window **resets the timer** (batches up bursts).
  - API config object: `file_arrival: { url, min_time_between_triggers_seconds, wait_after_last_change_seconds }`.
- **How it helps:** If Fabric/PBI telemetry exports (or capacity metric dumps) land in a UC volume/external location, trigger the audit the moment a fresh export arrives, with `wait_after_last_change` to wait for the whole batch.

### D5. Table update trigger
- **URL:** https://docs.databricks.com/aws/en/jobs/trigger-table-update (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/trigger-table-update)
- **Summary:** Fire when one or more monitored **tables** change (update/merge/delete). Supported sources: UC **Delta and Iceberg managed tables**, UC external tables backed by Delta Lake, **materialized views**, **streaming tables**, and UC views / metric views depending on supported tables. With **multiple tables**, choose **all tables updated** vs **any table updated**. Advanced timing: same **min-time-between-triggers** and **wait-after-last-change** semantics as file arrival. Benefits from file events when enabled.
- **How it helps:** If capacity telemetry is materialized into a Delta table (e.g. from Kusto/Log Analytics ingestion or a Lakeflow pipeline), trigger the audit automatically whenever that table refreshes — no polling.

### D6. Continuous jobs
- **URL:** https://docs.databricks.com/aws/en/jobs/continuous (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/continuous)
- **Summary:** Trigger type = **Continuous** keeps exactly **one** run active at all times (`max_concurrent_runs` is effectively 1). On failure, the job restarts automatically using **exponential backoff** at the **job level** — you **cannot** use task retry policies in continuous mode. The UI surfaces consecutive-failure count, the no-error duration required to be considered healthy, and time-to-next-retry. API: `continuous: { pause_status }`.
- **How it helps:** Generally **not** the right model for a periodic read-only sweep (that's scheduled/periodic). Continuous is relevant only if a streaming telemetry consumer must run nonstop; for the audit agent, prefer scheduled/periodic + `max_concurrent_runs=1`.

---

## E. Parameters & dynamic value references

### E1. Configure job parameters
- **URL:** https://docs.databricks.com/aws/en/jobs/job-parameters (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/job-parameters)
- **Summary:** A **job parameter** is a key→value pair defined at the **job** level and pushed down to all tasks; default value can embed dynamic references (e.g. `"{{job.start_time.iso_date}}"`). **Job parameters take precedence over task parameters** when keys collide. At `run-now` you can override or add job parameters.

### E2. Configure task parameters
- **URL:** https://docs.databricks.com/aws/en/jobs/task-parameters (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/task-parameters)
- **Summary:** A **task parameter** is a key→value pair (or JSON array) defined per task. Notebook tasks receive them via `dbutils.widgets`; Python script/wheel receive positional args (and wheel `named_parameters`); SQL tasks reference them in the query (alerts excepted).

### E3. Parameterize jobs (concepts hub)
- **URL:** https://docs.databricks.com/aws/en/jobs/parameters (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/parameters)
- **Summary:** Distinguishes **job parameters** vs **task parameters** vs **task values**; explains override precedence and dynamic references.

### E4. Access parameter values from a task
- **URL:** https://docs.databricks.com/aws/en/jobs/parameter-use (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/parameter-use)
- **Summary:** Notebook: `dbutils.widgets.get("<name>")`. Python script: read `sys.argv`. Python wheel: function args / argparse. SQL: `:name` / parameter markers. Retrieve job params via `{{job.parameters.<name>}}`.

### E5. Dynamic value references
- **URL:** https://docs.databricks.com/aws/en/jobs/dynamic-value-references (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/dynamic-value-references)
- **Summary:** Variables wrapped in **double curly braces `{{ }}`** that are substituted with string literals at run time; usable in parameter defaults, `condition_task` operands, `for_each` inputs, etc. Documented categories and exact references include:
  - **Job:** `{{job.id}}`, `{{job.name}}`, `{{job.run_id}}`, `{{job.repair_count}}`, `{{job.trigger.type}}`, `{{job.trigger.file_arrival.location}}`, `{{job.parameters.<name>}}`.
  - **Time-based (UTC):** `{{job.start_time.[arg]}}` with args including `iso_date`, `iso_datetime`, `year`, `month`, `day`, `hour`, `minute`, `second`, `timestamp_ms` (all UTC). (Verify the full arg list on the page; the doc enumerates the time-arg table.)
  - **Task:** `{{task.name}}`, `{{task.run_id}}`.
  - **Task values:** `{{tasks.<task_name>.values.<value_name>}}` — values written upstream via `dbutils.jobs.taskValues.set(...)`.
  - **Task output (SQL):** `{{tasks.<task_name>.output.<argument>}}` — reference an upstream SQL task's output downstream.
- **How it helps:** Pass `run_date={{job.start_time.iso_date}}` so every collector tags telemetry by audit date; have a detector `taskValues.set("max_cu_pct", x)` then branch with `{{tasks.detect.values.max_cu_pct}}` in a `condition_task`; record `{{job.id}}`/`{{job.run_id}}` in audit output for traceability.

### E6. Task values (pass info between tasks)
- **URL:** https://docs.databricks.com/aws/en/jobs/task-values (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/task-values) — referenced from E3/E5.
- **Summary:** `dbutils.jobs.taskValues.set(key, value)` in an upstream task; read with `dbutils.jobs.taskValues.get(taskKey, key, default, debugValue)` or via `{{tasks.<t>.values.<k>}}` dynamic reference.
- **How it helps:** The collector emits the worst capacity/CU% as a task value; downstream detector/condition/alert tasks consume it without re-querying.

---

## F. Retries, timeouts, health rules, notifications, alerts

### F1. Retries & timeouts (task config)
- **URL:** https://docs.databricks.com/aws/en/jobs/configure-task (+ API 2.0 ref https://docs.databricks.com/aws/en/reference/jobs-2.0-api)
- **Summary:** Per-task fields: **`max_retries`** (UI "Retries"; default behavior retries up to 3 in some contexts — set explicitly), **`min_retry_interval_millis`** (ms between failed run start and the retry), **`retry_on_timeout`** (whether a timeout counts as retryable), **`timeout_seconds`** (per-task timeout; **applies to each retry** when both are set). Job-level **timeout** also available. Retry interval is measured from the start of the failed run to the retry.
- **How it helps:** Make collectors resilient to transient Fabric/PBI REST or Kusto throttling: e.g. `max_retries=3`, `min_retry_interval_millis=60000`, `retry_on_timeout=true`, `timeout_seconds` bounded so a hung collector doesn't block the sweep.

### F2. Metric thresholds / health rules
- **URL:** https://docs.databricks.com/aws/en/jobs/configure-task (Metric thresholds) + monitor doc (§G)
- **Summary:** In the task panel, **Metric thresholds** let you set **Run duration** (expected + maximum completion times) and **streaming backlog** thresholds (backlog seconds/bytes/records/files). Job/task **health rules** (API `health.rules` with metric `RUN_DURATION_SECONDS` and an operator/value) drive **Duration warning** notifications. (Exact metric enum names — e.g. `RUN_DURATION_SECONDS`, streaming backlog metrics — appear on the API reference; verify there.)
- **How it helps:** Set an expected duration on the sweep so a **Duration warning** fires if the audit runs abnormally long (often a sign of API throttling or a runaway `for_each`).

### F3. Add notifications on a job
- **URL:** https://docs.databricks.com/aws/en/jobs/notifications (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/notifications)
- **Summary:** Notifications can be set at **job** and **task** level for events: **Start, Success, Failure, Duration warning, Streaming backlog**. Destination = **Email address** or a **system destination** (webhook, Slack). **Up to 3 system destinations per event type per job.** Option **"Mute notifications until the last retry"** suppresses noise until the final retry. **Job-level notifications are NOT sent while failed tasks are retrying**; for per-task-failure alerts, use **task** notifications. Streaming-metric thresholds can also raise notifications.
- **How it helps:** This is the core alerting surface for the audit agent. Wire **Failure** + **Duration warning** to a Teams/Slack webhook and an ops email; use task-level Failure notifications on each collector so a single failing capacity is reported even if the job retries.

### F4. Manage notification destinations (admin)
- **URL:** https://docs.databricks.com/aws/en/admin/workspace-settings/notification-destinations (Azure: https://learn.microsoft.com/en-us/azure/databricks/admin/workspace-settings/notification-destinations)
- **Summary:** Admins register **system destinations**: **Email**, **Slack** (incoming webhook), **Microsoft Teams** (webhook), **PagerDuty**, and generic **Webhook**. These named destinations are then selectable in job/task notifications and SQL alerts.
- **How it helps:** Register a **Teams** webhook destination once (matches the agent's existing Teams push surface) and reference it from the audit job's Failure/Duration-warning notifications — no secrets embedded in the job.

### F5. SQL alert task for jobs
- **URL:** https://docs.databricks.com/aws/en/jobs/alert (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/alert)
- **Summary:** Add a **Databricks SQL alert** as a job task: it runs a query, evaluates a condition, and (a) sends the alert's own notifications and (b) lets **downstream tasks branch on its result**. **SQL alert tasks do not support parameters** (use a `sql_task` query if you need parameterized SQL) and **support only modern Databricks SQL alerts** (legacy alerts unsupported). Related: Databricks SQL alerts https://docs.databricks.com/aws/en/sql/user/alerts/ ; legacy alerts https://docs.databricks.com/aws/en/sql/user/alerts/legacy.
- **How it helps:** If capacity telemetry is queryable from a SQL warehouse (e.g. `system.billing.*` joined to `system.lakeflow.*`, or a materialized metrics table), a SQL Alert task can be the breach detector itself — condition like "max CU% > 90 over last 24h" — and feed a downstream condition/notify task.

---

## G. Monitoring & observability

### G1. Monitoring and observability for Lakeflow Jobs
- **URL:** https://docs.databricks.com/aws/en/jobs/monitor (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/monitor)
- **Summary:** Monitor via the **Jobs & Pipelines runs UI** (currently running + recently completed runs across the workspace, including externally orchestrated runs), the run-details page (per-task durations, logs, **serverless query metrics**: rows read/written per task run, total queries per task run; **streaming observability**: backlog seconds/bytes/records/files), and notifications. Streaming metric thresholds can raise alerts.

### G2. Jobs system table reference
- **URL:** https://docs.databricks.com/aws/en/admin/system-tables/jobs (Azure: https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/jobs)
- **Summary:** The **`system.lakeflow`** schema exposes account-wide records of jobs, job runs, and tasks; can be **joined with billing system tables** to attribute cost to jobs. Tables cover job/run details, resource utilization, and associated costs.
- **How it helps:** Two wins for the audit agent: (1) operationally, query `system.lakeflow` to confirm the sweep ran and to alert on missed/failed runs from outside the job itself; (2) as a **telemetry source** — joining `system.lakeflow.*` to billing tables is itself a capacity/cost signal the auditor can read.

### G3. Observability best practices (Jobs + LDP + Lakeflow Connect)
- **URL:** https://docs.databricks.com/aws/en/data-engineering/observability-best-practices (Azure: https://learn.microsoft.com/en-us/azure/databricks/data-engineering/observability-best-practices)
- **Summary:** Cross-feature observability guidance: system tables, event logs, run UI, notifications, and cost monitoring patterns.
- **How it helps:** Pattern library for the "is the audit healthy and what did it cost" meta-monitoring layer.

### G4. Troubleshoot & repair job failures
- **URL:** https://docs.databricks.com/aws/en/jobs/repair-job-failures (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/repair-job-failures)
- **Summary:** **Repair run** re-executes only failed/skipped tasks of a run (incrementing `{{job.repair_count}}`), preserving successful task outputs/values. Combine with `condition_task` on `{{job.repair_count}}` to cap repair loops.
- **How it helps:** If only one capacity's collector fails, repair re-runs just that branch instead of re-sweeping everything — cheaper and faster recovery.

---

## H. Compute models for jobs

### H1. Configure compute for jobs
- **URL:** https://docs.databricks.com/aws/en/jobs/compute (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/compute)
- **Summary:** Three options: (1) **Job clusters** — ephemeral, created per run, terminated after; cheapest/most isolated for scheduled prod jobs. (2) **Serverless compute for workflows** — Databricks-managed, no cluster config (see H2). (3) **All-purpose (interactive) clusters** — shared, more expensive, **not recommended for production jobs**. Tasks in a job can mix compute.

### H2. Serverless compute for workflows
- **URL:** https://docs.databricks.com/aws/en/jobs/run-serverless-jobs (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
- **Summary:** No infrastructure to configure/deploy; **autoscaling + Photon auto-enabled**; continuously auto-optimizes instance type/memory/engine to the workload. **Auto-optimization** automatically optimizes compute and **retries failed tasks** (on by default; recommended to leave on). Requirements: workspace must have **Unity Catalog enabled**; **no cluster-create permission needed** (all users can use it). **No compute policies and no init scripts** — install custom Python deps via the **Environment** side pane (individually, or via a shareable **base environment**). Dependency config: https://docs.databricks.com/aws/en/compute/serverless/dependencies. Automatable via Jobs API, Asset Bundles, and the Databricks SDK for Python.
- **How it helps:** Strong default for the read-only audit sweep — zero cluster management, fast startup, pay-per-use, fits a lightweight Python collector/detector workload. Declare the agent's pip dependencies in the serverless Environment. Note the init-script/compute-policy restrictions if the agent needs custom system setup (use a base environment or fall back to a job cluster).

---

## I. Jobs REST API (create / run-now / get-run / list-runs)

Canonical reference index: https://docs.databricks.com/api/workspace/jobs/ (Azure: https://learn.microsoft.com/en-us/azure/databricks/reference/). Current major version is **2.2**; 2.1 → 2.2 migration: https://docs.databricks.com/aws/en/reference/jobs-api-2-2-updates. (2.0 legacy ref: https://docs.databricks.com/aws/en/reference/jobs-2.0-api.)

### I1. Create a job — `POST /api/2.2/jobs/create`
- **URL:** https://docs.databricks.com/api/workspace/jobs/create
- **Summary:** Body defines `name`, `tasks[]` (each with `task_key`, a task-type object, `depends_on`, `run_if`, `timeout_seconds`, `max_retries`, `min_retry_interval_millis`, `retry_on_timeout`, `email_notifications`/`webhook_notifications`, `health`), `job_clusters`/`compute`, `schedule`|`trigger`|`continuous`, `parameters[]` (job params), `max_concurrent_runs`, `queue`, `tags`, `run_as`. Returns `job_id`.

### I2. Trigger a new run — `POST /api/2.2/jobs/run-now`
- **URL:** https://docs.databricks.com/api/workspace/jobs/runnow
- **Summary:** Body: `job_id`, plus parameter overrides — `job_parameters` (key→value, preferred), or legacy positional/named: `notebook_params` (key→value map), `python_params` (list), `jar_params` (list), `python_named_params`, `sql_params`, `pipeline_params`, `dbt_commands`. **Only one of jar/python/notebook params per call**, matching the task type. `idempotency_token` (≤64 chars, dedupes retried submissions — verify on page), `queue`. Returns `run_id`.

### I3. One-time run — `POST /api/2.2/jobs/runs/submit`
- **URL:** https://docs.databricks.com/api/workspace/jobs/submit
- **Summary:** Submit a workload **without creating a persistent job** (inline `tasks` + compute). Returns `run_id`. Use when you don't need a saved/scheduled job.
- **How it helps:** Good for ad-hoc, on-demand "audit this capacity now" invocations (e.g. triggered by the conversational/Teams surface) without polluting the job list.

### I4. Get a single run — `GET /api/2.2/jobs/runs/get`
- **URL:** https://docs.databricks.com/api/workspace/jobs/getrun
- **Summary:** Returns run state: **`state.life_cycle_state`** ∈ PENDING, RUNNING, TERMINATING, TERMINATED, SKIPPED, INTERNAL_ERROR (and BLOCKED/WAITING_FOR_RETRY in some versions); **`state.result_state`** ∈ SUCCESS, FAILED, TIMEDOUT, CANCELED, (MAXIMUM_CONCURRENT_RUNS_REACHED, etc.). When `life_cycle_state=TERMINATED`, `result_state` is guaranteed; for PENDING/RUNNING/SKIPPED it's absent. `expand_tasks` adds per-task/cluster detail; paginate task arrays with `page_token`.

### I5. List runs — `GET /api/2.2/jobs/runs/list`
- **URL:** https://docs.databricks.com/api/workspace/jobs/listruns
- **Summary:** Lists runs newest-first. Query params: `job_id`, **`active_only`** (true → only active runs), `completed_only`, **`run_type`** ∈ `JOB_RUN`, `SUBMIT_RUN`, `WORKFLOW_RUN`, `start_time_from`/`start_time_to`, **`expand_tasks`** (include task+cluster detail), **`page_token`** + `next_page_token` for pagination, `limit`, `offset` (legacy).
- **How it helps (I1–I5 together):** The agent's own orchestrator/SDK code can programmatically create/update the audit job, trigger an on-demand `run-now` (or `runs/submit`) from the conversational surface, poll `runs/get` for `life_cycle_state`/`result_state`, and use `runs/list` (`active_only`, `run_type`) to detect missed/stuck sweeps and feed its self-monitoring + alerting.

### I6. Automate job creation and management
- **URL:** https://docs.databricks.com/aws/en/jobs/automate (Azure: https://learn.microsoft.com/en-us/azure/databricks/jobs/automate)
- **Summary:** Overview of automating jobs via CLI, SDK, REST, and Bundles; links the `databricks jobs` CLI command group (https://docs.databricks.com/aws/en/dev-tools/cli/reference/jobs-commands).

---

## J. Lakeflow Spark Declarative Pipelines (LDP / SDP, formerly DLT)

### J1. What is Lakeflow Spark Declarative Pipelines (concepts)
- **URL:** https://docs.databricks.com/aws/en/ldp/concepts (Azure: https://learn.microsoft.com/en-us/azure/databricks/ldp/concepts/) ; hub: https://docs.databricks.com/aws/en/ldp
- **Summary:** A **declarative** framework for batch + streaming pipelines in **SQL or Python**. Core concepts: **pipelines, flows, streaming tables, materialized views, sinks**. You declare the target datasets/transformations; the engine handles **orchestration, dependency resolution, incremental processing, and checkpointing**.
  - **Streaming table** — a UC managed table that is also a streaming target; each input row processed once; best for **ingestion** and low-latency/append-only/CDC/event-driven.
  - **Materialized view** — a batch flow that incrementally reprocesses only new/changed source data; results pre-computed and kept fresh; best for **complex transformations, joins, aggregations** and fast analytical reads.
  - Medallion fit: Bronze = streaming tables (raw ingest); Silver = streaming tables (row-level) + materialized views (enrichment/agg); Gold = materialized views (metrics/summaries).

### J2. "What happened to Delta Live Tables (DLT)?"
- **URL:** https://docs.databricks.com/aws/en/ldp/concepts/where-is-dlt (Azure: https://learn.microsoft.com/en-us/azure/databricks/ldp/where-is-dlt)
- **Summary:** DLT was rebranded to Lakeflow Spark Declarative Pipelines. **No migration required** — existing `@dlt.table` / `dlt` code keeps working.

### J3. Configure pipelines
- **URL:** https://docs.databricks.com/aws/en/ldp/configure-pipeline (Azure: https://learn.microsoft.com/en-us/azure/databricks/ldp/configure-pipeline)
- **Summary:** UI/API settings: **source code**, **target catalog + schema**, **compute** (classic vs serverless), **run-as identity**, **product edition** (Core / Pro / Advanced), and **pipeline mode** (**triggered** vs **continuous**). **Serverless** (recommended for new pipelines) removes compute config and uses **enhanced autoscaling** (scales executors horizontally + vertically). **Editions:** *Core* = streaming ingest; *Pro* = Core + CDC/update-from-source; *Advanced* = Pro + **expectations** (data-quality constraints). Triggered pipelines run once per update and stop; continuous pipelines run nonstop and auto-restart on identity/config change. Classic compute config: https://docs.databricks.com/aws/en/ldp/configure-compute.

### J4. Python language reference + dataset functions
- **URLs:** https://docs.databricks.com/aws/en/ldp/developer/python-ref ; https://docs.databricks.com/aws/en/ldp/developer/definition-function
- **Summary:** Decorators/functions to define datasets (`@dlt.table` / materialized view, `@dlt.view`, streaming-table creation, `@dlt.expect*` for expectations, `apply_changes` / CDC). Pipelines are invoked from a job via the **`pipeline_task`** (§B).

### J5. Run a pipeline update / triggered vs continuous
- **URL:** https://docs.databricks.com/aws/en/ldp/updates
- **Summary:** A pipeline **update** refreshes its tables; can be **full refresh** (recompute all) or incremental. Triggerable manually, on a schedule, or as a job `pipeline_task`.

### J6. Best practices
- **URL:** https://learn.microsoft.com/en-us/azure/databricks/ldp/best-practices
- **Summary:** Guidance on serverless, expectations, modularization, environment separation (dev/staging/prod) via parameters.

**When to use LDP for the audit agent:** Use LDP **only if** the agent needs to *materialize and continuously maintain* a curated capacity-telemetry layer (e.g. ingest Fabric/PBI metrics + `system.billing`/`system.lakeflow` into bronze→silver→gold tables with expectations enforcing data quality). The audit *sweep itself* (REST/MCP/Kusto collectors + Python detectors + reasoner) is a procedural, read-only, scheduled workload — that belongs in **Lakeflow Jobs** (notebook/wheel/python tasks), not LDP. If telemetry is materialized via LDP, a job can chain a `pipeline_task` (refresh telemetry) → audit tasks (read it) → alert task; and a **table update trigger** on the gold table can kick off the audit automatically.

---

## K. Quick recommendation for the audit agent's scheduling/alerting design
1. **Job** "fabric-capacity-audit" on **serverless compute for workflows**, `max_concurrent_runs=1`, job params `reference_date={{job.start_time.iso_date}}`, tenant scope.
2. **Schedule:** Quartz cron (nightly) or `periodic` (every N hours). Optionally a **table update trigger** on the materialized telemetry table for event-driven runs.
3. **Tasks (DAG):** enumerate-capacities → `for_each_task` (concurrency N) running per-capacity collect+detect (python_wheel) which `taskValues.set` breach signals → `condition_task` on the worst signal → alert task (`run_if=AT_LEAST_ONE_FAILED` or true-branch) → always-run summary task (`run_if=ALL_DONE`).
4. **Resilience:** per-collector `max_retries`/`retry_on_timeout`/`timeout_seconds`; **Repair run** for partial failures.
5. **Alerting:** job/task **Failure** + **Duration warning** notifications to a registered **Teams** (and email) system destination; "Mute until last retry" to cut noise; optionally a **SQL Alert task** as the breach detector.
6. **Self-monitoring:** query `system.lakeflow` (+ billing) to confirm runs happened and attribute cost; use `runs/list` (`active_only`, `run_type`) from the SDK for external watchdog alerting.

---

## L. Flat URL list (all sources)

- https://docs.databricks.com/aws/en/jobs/
- https://learn.microsoft.com/en-us/azure/databricks/jobs/
- https://docs.databricks.com/aws/en/jobs/configure-job
- https://learn.microsoft.com/en-us/azure/databricks/jobs/configure-job
- https://docs.databricks.com/aws/en/jobs/configure-task
- https://learn.microsoft.com/en-us/azure/databricks/jobs/configure-task
- https://docs.databricks.com/aws/en/dev-tools/bundles/job-task-types
- https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/job-task-types
- https://docs.databricks.com/aws/en/jobs/sql
- https://learn.microsoft.com/en-us/azure/databricks/jobs/sql
- https://docs.databricks.com/aws/en/jobs/alert
- https://learn.microsoft.com/en-us/azure/databricks/jobs/alert
- https://docs.databricks.com/aws/en/sql/user/alerts/
- https://docs.databricks.com/aws/en/sql/user/alerts/legacy
- https://docs.databricks.com/aws/en/jobs/control-flow
- https://learn.microsoft.com/en-us/azure/databricks/jobs/control-flow
- https://docs.databricks.com/aws/en/jobs/run-if
- https://learn.microsoft.com/en-us/azure/databricks/jobs/run-if
- https://docs.databricks.com/aws/en/jobs/if-else
- https://learn.microsoft.com/en-us/azure/databricks/jobs/if-else
- https://docs.databricks.com/aws/en/jobs/for-each
- https://learn.microsoft.com/en-us/azure/databricks/jobs/for-each
- https://docs.databricks.com/aws/en/jobs/for-each-lookup-example
- https://docs.databricks.com/aws/en/jobs/triggers
- https://learn.microsoft.com/en-us/azure/databricks/jobs/triggers
- https://docs.databricks.com/aws/en/jobs/scheduled
- https://learn.microsoft.com/en-us/azure/databricks/jobs/scheduled
- https://docs.databricks.com/aws/en/jobs/file-arrival-triggers
- https://learn.microsoft.com/en-us/azure/databricks/jobs/file-arrival-triggers
- https://docs.databricks.com/aws/en/jobs/trigger-table-update
- https://learn.microsoft.com/en-us/azure/databricks/jobs/trigger-table-update
- https://docs.databricks.com/aws/en/jobs/continuous
- https://learn.microsoft.com/en-us/azure/databricks/jobs/continuous
- https://docs.databricks.com/aws/en/jobs/job-parameters
- https://learn.microsoft.com/en-us/azure/databricks/jobs/job-parameters
- https://docs.databricks.com/aws/en/jobs/task-parameters
- https://learn.microsoft.com/en-us/azure/databricks/jobs/task-parameters
- https://docs.databricks.com/aws/en/jobs/parameters
- https://learn.microsoft.com/en-us/azure/databricks/jobs/parameters
- https://docs.databricks.com/aws/en/jobs/parameter-use
- https://learn.microsoft.com/en-us/azure/databricks/jobs/parameter-use
- https://docs.databricks.com/aws/en/jobs/dynamic-value-references
- https://learn.microsoft.com/en-us/azure/databricks/jobs/dynamic-value-references
- https://docs.databricks.com/aws/en/jobs/task-values
- https://docs.databricks.com/aws/en/jobs/notifications
- https://learn.microsoft.com/en-us/azure/databricks/jobs/notifications
- https://docs.databricks.com/aws/en/admin/workspace-settings/notification-destinations
- https://learn.microsoft.com/en-us/azure/databricks/admin/workspace-settings/notification-destinations
- https://docs.databricks.com/aws/en/jobs/monitor
- https://learn.microsoft.com/en-us/azure/databricks/jobs/monitor
- https://docs.databricks.com/aws/en/admin/system-tables/jobs
- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/jobs
- https://docs.databricks.com/aws/en/data-engineering/observability-best-practices
- https://learn.microsoft.com/en-us/azure/databricks/data-engineering/observability-best-practices
- https://docs.databricks.com/aws/en/jobs/repair-job-failures
- https://learn.microsoft.com/en-us/azure/databricks/jobs/repair-job-failures
- https://docs.databricks.com/aws/en/jobs/compute
- https://learn.microsoft.com/en-us/azure/databricks/jobs/compute
- https://docs.databricks.com/aws/en/jobs/run-serverless-jobs
- https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs
- https://docs.databricks.com/aws/en/compute/serverless/dependencies
- https://docs.databricks.com/aws/en/jobs/automate
- https://learn.microsoft.com/en-us/azure/databricks/jobs/automate
- https://docs.databricks.com/aws/en/dev-tools/cli/reference/jobs-commands
- https://docs.databricks.com/api/workspace/jobs/
- https://docs.databricks.com/api/workspace/jobs/create
- https://docs.databricks.com/api/workspace/jobs/runnow
- https://docs.databricks.com/api/workspace/jobs/submit
- https://docs.databricks.com/api/workspace/jobs/getrun
- https://docs.databricks.com/api/workspace/jobs/listruns
- https://docs.databricks.com/api/workspace/jobs/get
- https://docs.databricks.com/api/workspace/jobs/list
- https://docs.databricks.com/aws/en/reference/jobs-api-2-2-updates
- https://learn.microsoft.com/en-us/azure/databricks/reference/jobs-api-2-2-updates
- https://docs.databricks.com/aws/en/reference/jobs-2.0-api
- https://docs.databricks.com/aws/en/ldp
- https://docs.databricks.com/aws/en/ldp/concepts
- https://learn.microsoft.com/en-us/azure/databricks/ldp/concepts/
- https://docs.databricks.com/aws/en/ldp/concepts/where-is-dlt
- https://learn.microsoft.com/en-us/azure/databricks/ldp/where-is-dlt
- https://docs.databricks.com/aws/en/ldp/configure-pipeline
- https://learn.microsoft.com/en-us/azure/databricks/ldp/configure-pipeline
- https://docs.databricks.com/aws/en/ldp/configure-compute
- https://docs.databricks.com/aws/en/ldp/developer/python-ref
- https://docs.databricks.com/aws/en/ldp/developer/definition-function
- https://docs.databricks.com/aws/en/ldp/updates
- https://learn.microsoft.com/en-us/azure/databricks/ldp/best-practices

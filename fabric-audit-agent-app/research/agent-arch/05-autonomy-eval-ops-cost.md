# 05 — Autonomy, Evaluation, Ops & Cost for the bi-fabrics-audit-agent (Phase 2 Watchdog)

> Scope: how to **run the read-only Fabric/PBI capacity investigator proactively**, **evaluate** it, **operate/observe** it, and **keep it cheap**, on Databricks with an in-tenant Claude reasoner. Read-only is absolute; cost-sensitivity is a first-class constraint (the audited capacity may be small/trial). Currency: 2025–2026 Databricks/MLflow docs. Sister docs cover reasoner choice, tools, and Fabric-side data acquisition.
>
> **Headline recommendation (TL;DR):** Run the watchdog as a **scheduled Lakeflow Job on serverless compute that executes the agent loop directly in a Python task** (no always-on serving endpoint), running **as a least-privilege, read-only service principal**. Persist baselines/findings to **two Delta tables in Unity Catalog** (state + append-only findings). Alert humans via a **Databricks SQL Alert → MS Teams notification destination** (and/or **Fabric Activator** on the Fabric side). Evaluate with **MLflow 3 scorers** offline in the same job (cheap, sampled), and only graduate to a **deployed serving endpoint + Production Monitoring** if/when the agent becomes interactive or multi-consumer. This is the cheapest shape that stays governable, observable, and read-only.

---

## 0. The first architectural fork: serving endpoint vs. agent-loop-in-a-job

There are two ways to "run" a Mosaic AI agent, and for an **autonomous, scheduled, single-consumer watchdog** they have very different cost/ops profiles.

| | (a) Deploy as a **serving endpoint**, job *calls* it | (b) Run the **agent loop directly** in a scheduled job/notebook task |
|---|---|---|
| Infra | A persistent Model Serving endpoint (REST API) + the scheduled job that calls it | Just a Lakeflow Job on serverless compute |
| Cost | Job compute **+** serving-endpoint DBUs (billed while provisioned; scale-to-zero possible but adds cold start) | Job compute only (serverless scales to zero between runs) |
| Observability "for free" | MLflow tracing + inference tables + Production Monitoring auto-enabled by `agents.deploy()` | You enable tracing explicitly (`mlflow.<lib>.autolog()` / `ENABLE_MLFLOW_TRACING`) |
| Governance | Endpoint ACLs (`CAN_QUERY`), automatic auth passthrough / OBO | Job runs as a service principal with UC grants |
| Best when | Agent is **shared, real-time, multi-consumer**, or interactive | Agent is a **single, infrequent, batch/scheduled** consumer — exactly the watchdog |

- Databricks positions **Model Serving** as "a unified interface to deploy, govern, and query AI models for real-time and batch inference, with each model served as a REST API" — geared to real-time/scalable serving with governance and monitoring; notebooks are framed for development and smaller-scale processing. ([docs.databricks.com/.../model-serving](https://docs.databricks.com/aws/en/machine-learning/model-serving/))
- The cost trade-off is explicit in Databricks' own AI-agent cost guidance: serving endpoints provide always-available low-latency access but **"Models loaded (even when idle) still incur charges,"** while **"Jobs Compute is optimized for scheduled batch processing"** and **"Lakeflow Jobs … is the most cost-efficient compute type for production ETL and batch workloads."** ([Demystifying Databricks pricing for AI agents](https://community.databricks.com/t5/technical-blog/demystifying-databricks-pricing-for-ai-agents/ba-p/122281); [model-serving](https://docs.databricks.com/aws/en/machine-learning/model-serving/))

**Verdict for the watchdog:** a once-or-few-times-a-day sweep does **not** justify a standing endpoint. **Pattern (b)** — agent loop in a serverless job — is the cost-minimal default. Keep pattern (a) in reserve for if the audit agent becomes an interactive chat experience or is consumed by multiple apps/users (then the endpoint amortizes and brings governance/monitoring out of the box).

> **Naming note:** "Databricks Jobs / Workflows" is now **Lakeflow Jobs** ("The product known as Databricks Jobs is now Lakeflow Jobs. No migration is required"). REST/SDK resources still use `jobs`. Use "Lakeflow Jobs" in the design doc. ([release-notes 2025/06](https://docs.databricks.com/aws/en/release-notes/product/2025/june); [jobs landing](https://docs.databricks.com/aws/en/jobs/))

---

## 1. Running the agent proactively — triggers & scheduling

Lakeflow Jobs supports six trigger types (Trigger type / Add trigger): **Scheduled**, **Table update**, **File arrival**, **Model update** (Beta), **Continuous**, and **None (manual / API)**. ([jobs/triggers](https://docs.databricks.com/aws/en/jobs/triggers))

### 1.1 Scheduled (cron) — the watchdog's primary trigger
- Two modes: **Simple** ("specify an interval and unit of time") and **Advanced** ("specify the period, starting time, and time zone"), with a **"Show Cron Syntax"** checkbox exposing **Quartz Cron**. ([jobs/scheduled](https://docs.databricks.com/aws/en/jobs/scheduled))
- **Minimum interval is 10 seconds** between schedule-triggered runs, but **"The job scheduler is not intended for low-latency jobs"** — expect delays of several minutes. Use **UTC** to avoid DST skips. For a capacity audit, hourly/daily is the right cadence; sub-minute is neither needed nor supported well. ([jobs/scheduled](https://docs.databricks.com/aws/en/jobs/scheduled))

### 1.2 Event-driven autonomy (optional)
- **Table update trigger** — fire when a UC Delta/Iceberg table is updated; supports **All tables updated** or **Any table updated**, with **Minimum time between triggers** + **Wait after last change**; **≤10 tables per trigger**. Good if a Fabric→Delta ingestion lands new capacity metrics and you want the sweep to react. ([jobs/trigger-table-update](https://docs.databricks.com/aws/en/jobs/trigger-table-update))
- **File arrival trigger** — best-effort check **every minute**; requires UC external location/volume; without file-events: **≤50 such jobs/workspace**, location **≤10,000 files**. ([jobs/file-arrival-triggers](https://docs.databricks.com/aws/en/jobs/file-arrival-triggers))
- **Continuous mode** keeps a run always going — *avoid for a cost-sensitive watchdog*; it's for near-real-time streaming loops. ([jobs/triggers](https://docs.databricks.com/aws/en/jobs/triggers))

### 1.3 Compute: serverless, scale-to-zero between runs
- **"Serverless compute for workflows lets you run your job without configuring and deploying infrastructure"**; Databricks **"recommends using serverless compute for all job tasks."** Supported task types include notebook, Python script, Python wheel. Requires **Unity Catalog**. ([jobs/run-serverless-jobs](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs))
- Cost lever: **standard** performance mode **"consumes fewer DBUs"** than performance-optimized, at the price of higher startup latency (**4–6 min**). For a watchdog, latency is irrelevant — **use standard mode**. ([jobs/run-serverless-jobs](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs))
- Serverless removes idle/provisioning cost and scales to zero between runs — the cheapest fit for an infrequent sweep. Classic job clusters only win for long/heavy custom compute. ([jobs/compute](https://docs.databricks.com/aws/en/jobs/compute))

### 1.4 Identity & how the job authenticates
- **Run the job as a service principal:** "the job will run with the identity of the service principal, instead of the identity of the job owner." Use a **dedicated, least-privilege SP**. ([jobs/triggers](https://docs.databricks.com/aws/en/jobs/triggers))
- Production auth recommendation: **machine-to-machine (M2M) OAuth**; **"Each access token is valid for one hour,"** auto-refreshed by unified client auth; env vars `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`. Inside Databricks compute, `WorkspaceClient()` picks up the running identity automatically. ([dev-tools/auth/oauth-m2m](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m); [score-custom-model-endpoints](https://docs.databricks.com/aws/en/machine-learning/model-serving/score-custom-model-endpoints))
- If you *do* call an endpoint (pattern a), Databricks recommends the **DatabricksOpenAI client** to query a deployed agent (`client.responses.create(model=endpoint, input=...)`), or `mlflow.deployments.get_deploy_client("databricks").predict(...)`, or REST `POST /serving-endpoints/<name>/invocations`. ([query-agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/query-agent))

---

## 2. Reading memory & writing findings (state across runs)

A watchdog is only "autonomous" if it remembers baselines between sweeps and records what it found. For a **scheduled batch** agent the idiomatic store is **Delta in Unity Catalog**, not a chat-memory service.

### 2.1 Two-table pattern (recommended)
- **State / baseline table** — one row per monitored entity (capacity, workspace, dataset), upserted via `MERGE`; holds last-seen CU%, refresh-time baselines, prior anomaly state.
- **Append-only findings table** — immutable event log; Delta **time travel** gives you a free, queryable history of how findings evolved across runs.

This mirrors Databricks' own anomaly-detection design: **"The metric tables are Delta tables and are stored in a Unity Catalog schema that you specify … query them using Databricks SQL, and create dashboards and alerts based on them,"** and **"if a baseline table is provided, drift is also profiled relative to the baseline values."** It even **"builds a per-table model from commit history to predict the next expected commit time"** — a ready template for "store a baseline, compare new data." ([UC anomaly-detection](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/data-quality-monitoring/anomaly-detection/))

### 2.2 MERGE for dedup & reconciliation
From the canonical merge doc ([delta/merge](https://learn.microsoft.com/en-us/azure/databricks/delta/merge)):
- **Upsert** baselines: `MERGE INTO … WHEN MATCHED THEN UPDATE … WHEN NOT MATCHED THEN INSERT`.
- **Dedup so a re-run doesn't double-write findings** (insert-only merge): `MERGE INTO findings USING new ON findings.uniqueId = new.uniqueId WHEN NOT MATCHED THEN INSERT *`. Caveat: the incoming batch must be self-deduped; constrain the match window (`AND findings.date > current_date() - INTERVAL 7 DAYS`) so it scans only recent partitions.
- **Resolve/close stale findings** with `WHEN NOT MATCHED BY SOURCE THEN UPDATE SET status='inactive'` against a current-state snapshot.
- **Time travel** (`VERSION AS OF` / `TIMESTAMP AS OF`) for history and rollback of a bad merge.

### 2.3 Lakebase (managed Postgres) — when Delta isn't enough
- **Lakebase** is "a fully managed Postgres database integrated into the Databricks platform … Use Lakebase as an online feature store for ML models, or as a **state store for AI agents**," with **scale-to-zero** autoscaling compute. Native **LangGraph / OpenAI Agents SDK checkpointers** persist resumable session state; low-latency reads on the order of **tens of milliseconds**. ([oltp/projects](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/); [state-management](https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/state-management); [product/lakebase](https://www.databricks.com/product/lakebase))
- **Choose Delta-in-UC for the watchdog** (batch reads/writes once per run, throughput-oriented, governed + time-travelable, cheapest). **Choose Lakebase** only for low-latency point lookups, transactional row writes, or resumable interactive sessions.
- **Status caveat (matters for an Azure/Fabric stack):** Lakebase is **GA on AWS (early Feb 2026)** but **still Public Preview on Azure** as of that date — a reason to default to Delta now. ([Azure Lakebase GA blog](https://www.databricks.com/blog/azure-databricks-lakebase-generally-available); pricing scale-to-zero: [pricing/lakebase](https://www.databricks.com/product/pricing/lakebase))

---

## 3. Alerting humans (Teams / email / Fabric Activator)

### 3.1 Databricks-side: SQL Alert → MS Teams notification destination (recommended)
- A workspace admin creates a **notification destination**; supported types are exactly **Email, Slack, Webhook, MS Teams, PagerDuty**. MS Teams/Slack are configured by pasting an **incoming-webhook URL**. **Constraint:** "You can only configure notifications for Databricks SQL and jobs," and "non-email destinations such as Slack and MS Teams do not support HTML formatting." ([notification-destinations](https://learn.microsoft.com/en-us/azure/databricks/admin/workspace-settings/notification-destinations))
- **Databricks SQL Alerts** "run queries on a schedule and notify you when a condition that you define is met." Evaluations resolve to **`OK` / `TRIGGERED` / `ERROR`**. Documented patterns include **"Detect data quality issues and anomalies"** and **"Catch failures in AI agents."** An alert can also run as a **SQL alert task inside a Lakeflow Job**, so downstream tasks can branch on `TRIGGERED`. ([sql/user/alerts](https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/); [jobs/tasks/alert](https://learn.microsoft.com/en-us/azure/databricks/jobs/tasks/alert))
- **Pattern:** point a SQL Alert at the **findings Delta table** (e.g., "any new `severity = high` finding in the last hour") → MS Teams + email. This decouples *detection* (the agent writing findings) from *notification* (SQL Alert reading them), which is robust and cheap.

### 3.2 Job-run alerts (the agent run itself)
- **Lakeflow Jobs notifications** fire on **Start, Success, Failure, Duration warning, Streaming backlog** → email or system destinations (Teams/Slack/PagerDuty/webhook). **Gotchas:** job-level failure notifications **aren't sent when failed tasks are retried** (use **task-level** notifications for per-failure reliability); a **"Succeeded with failures"** run counts as success. Max **3 system destinations per event type**. Webhook payload `event_type`s: `jobs.on_start`, `jobs.on_success`, `jobs.on_failure`, `jobs.on_duration_warning_threshold_exceeded`. ([jobs/notifications](https://learn.microsoft.com/en-us/azure/databricks/jobs/notifications))

### 3.3 Fabric-side: Fabric Activator (formerly "Data Activator")
Since the audited system **is** Microsoft Fabric, the native Fabric alerting engine is a strong complement (or alternative) — especially when the watched signal lives in Fabric, not yet in Delta.
- **"Fabric Activator is a no-code event detection engine … It continuously monitors these data sources with low latency (subsecond for stateless rules on streaming data)."** ([activator-introduction](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-introduction))
- It can watch **Eventstreams, Power BI report visuals ("notify when a new row appears in a table visual"), Fabric events (pipeline failure, semantic-model refresh), and Fabric Data Warehouse SQL query results (preview, scheduled, no streaming required)**, and trigger **Send Teams message / Send email / Power Automate / run pipelines/notebooks**. Rules can be **stateless** (`value < threshold`) or **stateful** (`BECOMES`, `INCREASES`, `EXIT RANGE`, heartbeat/absence-of-data), fire only on state entry (noise control), and offer **impact estimates on historical data before activation**. A listed use case is literally **"Monitor pipeline health and … alert teams when anomalies or failures are detected."** ([activator-introduction](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-introduction))
- **Recommendation:** use **Activator for Fabric-native real-time conditions** (e.g., capacity throttling/overload events on an eventstream) and **Databricks SQL Alerts for findings persisted in Delta**. They are complementary; don't duplicate the same rule in both.

---

## 4. Evaluation — Mosaic AI Agent Evaluation, scorers/judges, MLflow Tracing

MLflow 3's eval-and-monitor stack spans dev→prod: **MLflow Tracing**, **offline `mlflow.genai.evaluate()`**, **Evaluation Datasets**, **Scorers/LLM judges**, **Production Monitoring (Beta)**, and the **Review App**. The same scorer runs both offline and in production, keeping evaluation consistent. ([eval-monitor](https://docs.databricks.com/aws/en/mlflow3/genai/eval-monitor/))

### 4.1 MLflow Tracing (the observability substrate)
- A trace **"records inputs, outputs, intermediate steps, and metadata"** for agent systems; spans carry a `SpanType` (`LLM`, `TOOL`, `RETRIEVER`, `AGENT`, `CHAIN`). OpenTelemetry-compatible. ([tracing](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/); [trace concepts](https://mlflow.org/docs/latest/genai/concepts/trace/))
- **Token usage & cost are captured automatically:** per-call in span attribute `mlflow.chat.tokenUsage` (`input_tokens`/`output_tokens`/`total_tokens`); trace total in `mlflow.trace.tokenUsage` (`trace.info.token_usage`). **Aggregated cost & trend charts** appear on the experiment Overview tab — directly useful for tracking the watchdog's own LLM spend. ([token-usage-cost](https://mlflow.org/docs/latest/genai/tracing/token-usage-cost/))
- **Auto-instrumentation:** one line — `mlflow.openai.autolog()` / `mlflow.langchain.autolog()` (20+ frameworks). **On serverless compute, GenAI autolog is NOT auto-enabled — call `autolog()` explicitly.** For pattern (b) this is exactly the line you add to the job. ([app-instrumentation/automatic](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/app-instrumentation/automatic))
- For pattern (a), `agents.deploy()` enables tracing automatically; custom CPU serving uses `ENABLE_MLFLOW_TRACING=true` + `MLFLOW_EXPERIMENT_ID`. ([prod-tracing](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/prod-tracing))

### 4.2 Scorers & LLM judges
Four scorer kinds: **built-in judges**, **custom LLM judges (`make_judge`)**, **code-based scorers (`@scorer`)**, and **third-party**. ([concepts/scorers](https://docs.databricks.com/aws/en/mlflow3/genai/eval-monitor/concepts/scorers))
- **Built-in single-turn judges** (import from `mlflow.genai.scorers`): `RelevanceToQuery`, `Safety`, `RetrievalGroundedness`, `RetrievalRelevance`, `Guidelines` (no ground truth); `Correctness`, `RetrievalSufficiency`, `ToolCallCorrectness` (require ground truth); `ToolCallEfficiency`. Multi-turn: `ConversationCompleteness`, `UserFrustration`, etc. ([predefined judges](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/concepts/judges/))
- **`make_judge`** (MLflow ≥3.4.0): define a custom judge in **natural-language `instructions`** with template variables (`{{ inputs }}`, `{{ outputs }}`, and the `{{ trace }}` variable for **Agent-as-a-Judge** that inspects the whole execution trace), plus a `feedback_value_type`. **Judge alignment** trains a custom judge against human labels. ([building custom LLM judges](https://www.databricks.com/blog/building-custom-llm-judges-ai-agent-accuracy); [concepts/scorers](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/concepts/scorers))
- **Judge LLM is configurable:** `Safety(model="databricks:/databricks-gpt-oss-20b")`, format `<provider>:/<model-name>` or `databricks:/<serving-endpoint>` — **route judges to a cheap small model** to control eval cost.

### 4.3 What to evaluate for THIS agent
The watchdog is a **read-only investigator producing findings**, so the highest-value scorers are:
1. **Custom `make_judge` "finding correctness/grounding"** — does each finding cite the metric/table it's based on and follow from the data? (Agent-as-a-Judge over the trace catches hallucinated findings — the top risk for an autonomous auditor.)
2. **`ToolCallCorrectness` / `ToolCallEfficiency`** — did it query the right read-only tools without redundant/expensive calls?
3. **`Safety`** — low value here but cheap; run at high sample to be safe.
4. A **code-based `@scorer`** asserting **read-only invariant** (no write/DDL verbs ever appear in tool calls) — deterministic, free, and aligned with the absolute read-only requirement.

---

## 5. Production monitoring (only if you deploy an endpoint)

If/when the agent graduates to pattern (a), **Production Monitoring (Beta)** runs the same MLflow 3 scorers on a sample of live production traces.
- **"Production monitoring lets you automatically run MLflow 3 scorers on traces … to continuously assess quality."** Two-step lifecycle: `Safety().register(name=...).start(sampling_config=ScorerSamplingConfig(sample_rate=0.7))`. **≤20 scorers per experiment.** `filter_string` (same syntax as `mlflow.search_traces()`) restricts which traces are scored. Allow **15–20 min** for initial processing; results attach as feedback on the **Traces tab**; durable storage syncs to Delta ~every 15 min. ([production-monitoring](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/production-monitoring))
- **Constraints on production scorers:** only `@scorer`-decorated scorers (no class-based subclasses), defined/registered **from a Databricks notebook**, **self-contained** with inline imports.
- **Gap to flag:** the production-monitoring docs describe dashboards/quality-trend tracking but **no built-in threshold/alerting on quality regressions** — you'd build alerting yourself via a **SQL Alert on the monitoring Delta tables** (§3.1).

> For pattern (b) you don't need Production Monitoring at all: run scorers **offline inside the same job** (`mlflow.genai.evaluate()`), at a sample rate, and write the eval results to your findings/quality Delta table. Same judges, no standing infra.

---

## 6. Cost & ops — keeping the watchdog cheap

### 6.1 Where the money goes
1. **Reasoner LLM tokens** (in-tenant Claude). Pay-per-token is **per 1M input/output tokens** (DBU-rated), on demand, no commitment; **provisioned throughput** is hourly compute for guaranteed capacity (e.g., scaling-capacity bands in the tens-to-hundreds of DBU/hour). For a **bursty, low-QPS watchdog, pay-per-token wins decisively** — provisioned throughput only pays off at sustained high volume. ([foundation-model-serving pricing](https://www.databricks.com/product/pricing/foundation-model-serving); [pay-per-token vs provisioned analysis](https://medium.com/@lararachidi/optimizing-your-ai-deployment-an-analysis-of-databricks-pay-per-token-and-provisioned-throughput-2316a8a57386))
2. **Job compute** — serverless, scale-to-zero between runs, **standard mode** (fewer DBUs). Only billed during the sweep. ([run-serverless-jobs](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs))
3. **Serving endpoint** (only pattern a) — **"Models loaded (even when idle) still incur charges."** Custom CPU endpoint sizes: **Small (0–4 concurrent), Medium (8–16), Large (16–64)**; concurrency must be multiples of 4. **Scale-to-zero is opt-in** (`scale_to_zero_enabled=True`) and "not recommended for production endpoints, as capacity is not guaranteed when scaled to zero," and adds **cold-start latency**. Avoiding the endpoint avoids this whole line item. ([create-manage-serving-endpoints](https://docs.databricks.com/aws/en/machine-learning/model-serving/create-manage-serving-endpoints); [AI agent cost guide](https://community.databricks.com/t5/technical-blog/demystifying-databricks-pricing-for-ai-agents/ba-p/122281))
4. **Evaluation / judges** — judges are LLM calls and "significant computational power" intensive. Databricks' own guidance: **evaluate only 5–10% of responses** via sampling; offline eval ≈ "1,000 DBUs per 50,000 evals." ([AI agent cost guide](https://community.databricks.com/t5/technical-blog/demystifying-databricks-pricing-for-ai-agents/ba-p/122281); [production-monitoring](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/production-monitoring))
5. **Inference tables / payload logging** — extra storage; CPU endpoints support a **sampling fraction** (default 100%, settable lower) to cut volume. Max logged payload/trace size **1 MiB**. ([ai-gateway/inference-tables](https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/inference-tables))

### 6.2 The "always-evaluating agent" trap — and how to avoid it
An autonomous agent that re-evaluates everything every run burns tokens fast. Mitigations, all doc-backed:
- **Sample judges, don't run on everything.** Docs: safety/security at `sample_rate=1.0`; **expensive LLM judges at 0.05–0.2**; dev iteration at 0.3–0.5. ([production-monitoring](https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/production-monitoring))
- **Route judges to a small cheap model** (`model="databricks:/databricks-gpt-oss-20b"` etc.) — keep the powerful Claude reasoner for the agent, not the grader.
- **Prefer deterministic `@scorer` checks** (read-only invariant, finding-schema validation) over LLM judges where possible — they're free.
- **Cache / dedup work** so the agent doesn't re-investigate unchanged capacities (MERGE-based dedup, §2.2; cost guide also recommends caching FAQ responses and column pruning).
- **Right-size the sweep cadence** — hourly vs every-15-min is a direct multiplier on token+compute cost. Event-driven (table-update) triggers can run *only when new data lands*, avoiding empty sweeps.

### 6.3 Latency
Not a constraint for a watchdog. The job scheduler "is not intended for low-latency jobs," serverless standard mode adds 4–6 min startup, and scale-from-zero on an endpoint adds cold start — **all acceptable** for a background auditor. This is *why* the cheap pattern is also the right pattern here.

---

## 7. Governance — read-only, least-privilege, auditable

### 7.1 Enforce read-only at the Unity Catalog grant layer
- The complete read-only grant set is **`USE CATALOG` + `USE SCHEMA` + `SELECT`** (plus **`EXECUTE`** on any UC-function tools). "To read from a table, a user needs `SELECT` on the table, `USE CATALOG` on the parent catalog, and `USE SCHEMA` on the parent schema." `USE CATALOG`/`USE SCHEMA` grant *traversal only*, no data. ([privileges-reference](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/privileges-reference))
- **Never grant** `MODIFY`, `WRITE FILES`/`WRITE VOLUME`, any `CREATE *`, `MANAGE`, or **`ALL PRIVILEGES`** (which "implies `SELECT`, `MODIFY`, and `APPLY TAG`"). The read-only guarantee comes from *withholding* these, not from agent prompting. ([privileges-reference](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/privileges-reference))
- Use a **dedicated, least-privilege service principal** per the stated best practice. UC-function tools should be parameterized, `LIMIT`-bounded `SELECT`s; the agent only needs `EXECUTE`. ([create-custom-tool](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/create-custom-tool))
- Among managed MCP servers, **Genie is read-only** and the **functions/AI-search** servers require only `EXECUTE`/`SELECT`; **avoid the `…/api/2.0/mcp/sql` server (read *and write*)** for a strict read-only agent. ([managed-mcp](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/managed-mcp))

### 7.2 Auth model (if/when you deploy an endpoint)
- **Automatic authentication passthrough** — agent runs with a system-generated, least-privilege SP holding short-lived rotated M2M tokens for *declared* resources. Minimum creator permissions are all read/execute-level: SQL Warehouse `Use Endpoint`, Serving endpoint `Can Query`, UC Function `EXECUTE`, Genie `Can Run`, AI Search `Can Use`, UC Table `SELECT`. ([agent-authentication-model-serving](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-authentication-model-serving))
- **On-behalf-of-user (OBO)** (Public Preview, admin-gated) — agent acts as the querying user, so UC row/column controls and **per-user audit attribution** apply; tokens are downscoped to declared API scopes. Less relevant for an *unattended* watchdog (no end user), where a dedicated read-only SP is the right model — but note OBO if the agent ever gets an interactive UI. ([agent-authentication-model-serving](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-authentication-model-serving))
- **Endpoint invocation ACLs:** grant **`CAN_QUERY`** only to intended callers; reserve `CAN_MANAGE` for admins. ([serving-endpoint-acl](https://docs.databricks.com/security/auth-authz/access-control/serving-endpoint-acl.html))
- **Secrets** for any outbound call (e.g., a Teams webhook used directly by a tool): store as a Databricks secret, reference as `{{secrets/<scope>/<key>}}` in env vars; the endpoint creator needs READ on the secret. ([store-env-variable-model-serving](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/store-env-variable-model-serving))

### 7.3 Audit
- **`system.access.audit`** (Public Preview, 365-day retention) — `getTable` and other actions with `user_identity`, `action_name`, `request_params`; **`system.access.table_lineage`/`column_lineage`** attribute which identity read which table; **`system.query.history`** captures SQL text; **`system.serving.endpoint_usage`** captures per-request token counts (enable usage tracking). System tables are themselves read-only and UC-governed. ([system-tables/audit-logs](https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/audit-logs); [system-tables index](https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/))

---

## 8. Recommended architecture for the Phase-2 watchdog

```
                 ┌─────────────────────────── Unity Catalog (read-only SP grants: USE CATALOG/SCHEMA, SELECT, EXECUTE) ──────────────────────────┐
                 │                                                                                                                               │
 [Scheduled (cron)         ┌───────────────────────────────────────────────────────────────────┐         ┌──────────────────────────────┐      │
  or Table-update] ──────► │  Lakeflow Job  ·  SERVERLESS (standard mode, scale-to-zero)         │ ──read─►│  Fabric/PBI capacity metrics    │      │
  trigger                  │  Run-as: least-privilege service principal (M2M OAuth)             │         │  (Delta tables / Genie / tools) │      │
                           │                                                                   │         └──────────────────────────────┘      │
                           │  Python task = AGENT LOOP DIRECTLY (no serving endpoint)           │                                                │
                           │   • in-tenant Claude reasoner (pay-per-token)                      │ ──read─►  STATE/BASELINE Delta table ◄──MERGE──┤
                           │   • mlflow.<lib>.autolog()  → traces (tokens, cost, tool calls)    │                                                │
                           │   • read-only tools (UC funcs / Genie / AI Search)                 │ ─append─► FINDINGS Delta table (time travel)   │
                           │   • mlflow.genai.evaluate() on a 5–10% sample (cheap judge model)  │                                                │
                           └───────────────────────────────────────────────────────────────────┘                                                │
                 └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                            │                                   │
                       Job failure/duration  ───────────────┘                                   └──► SQL ALERT on FINDINGS table
                       → Lakeflow Jobs notification (TASK-level) → MS Teams + email                   (OK/TRIGGERED/ERROR) → MS Teams + email
                                                                                                 [Fabric-native signals → Fabric Activator → Teams]
   Audit: system.access.audit · table_lineage · query.history        Graduate to: Model Serving endpoint + Production Monitoring only if interactive/multi-consumer
```

**Concrete choices:**
1. **Autonomy:** Lakeflow Job, **Scheduled (cron)** trigger (hourly/daily), **serverless standard mode**, **pattern (b)** agent-loop-in-task. Add a **Table-update** trigger only if you want event-driven sweeps that skip empty runs.
2. **Memory/findings:** two **Delta tables in UC** (state via `MERGE`, findings append-only with time travel). Skip Lakebase for now (Azure preview; batch cadence doesn't need ms latency).
3. **Alerting:** **SQL Alert on the findings table → MS Teams destination + email**; **task-level** job-failure notifications; **Fabric Activator** for Fabric-native real-time conditions.
4. **Eval:** **MLflow Tracing** (autolog) + **offline `mlflow.genai.evaluate()` in the same job** on a **5–10% sample** with a **small cheap judge model**; a custom `make_judge` for finding-grounding + a deterministic `@scorer` enforcing the read-only invariant. Use the experiment's **token/cost trend charts** to watch spend.
5. **Cost:** **pay-per-token** reasoner; **no standing endpoint**; sample judges; cache/dedup; right-size cadence. Latency is a non-issue.
6. **Governance:** dedicated **read-only service principal** (`USE CATALOG`/`USE SCHEMA`/`SELECT`/`EXECUTE` only — never `MODIFY`/`WRITE`/`CREATE`/`MANAGE`/`ALL PRIVILEGES`); read-only tools only (Genie/UC functions/AI Search; **avoid the read-write SQL MCP server**); audit via `system.access.audit` + lineage + query history.

**When to flip to a serving endpoint (pattern a):** the audit agent becomes an **interactive chat** experience, is **consumed by multiple apps/users**, or needs **always-on low-latency** answers. Then `agents.deploy()` brings tracing + inference tables + Production Monitoring out of the box; enable `scale_to_zero_enabled=True` to limit idle cost (accepting cold starts), gate with `CAN_QUERY`, and consider **OBO** so per-user UC controls and audit attribution apply.

---

## 9. Caveats / preview-status flags (verify live before committing)
- **Production Monitoring** is **Beta**; **OBO / User authorization** is **Public Preview** and admin-gated; **managed MCP servers** and several **system tables** (`system.access.audit`, `system.serving.*`, `system.query.history`) are **Public Preview**.
- **Lakebase** is **GA on AWS (Feb 2026)** but **Public Preview on Azure** — default to Delta for state on an Azure/Fabric stack.
- **Production-monitoring has no built-in quality-regression alerting** — build it via a SQL Alert on the monitoring/findings Delta tables.
- The **MLflow 2 agent `payload_request_logs`/`payload_assessment_logs`** path is **deprecated (no new data after Dec 4, 2025)** — design on **MLflow 3** (tracing auto-enabled on deploy; ≥3.1.3 for `deploy()`).
- Confirm **serverless DBU pricing** on the Lakeflow Jobs pricing page and that the workspace has **Unity Catalog enabled** (required for serverless, file-arrival, and table-update triggers).
- On serverless, **GenAI autolog is not auto-enabled** — you must call `mlflow.<lib>.autolog()` in the job.

---

## Sources (docs-first; #docs ≈ 45 unique pages)

**Autonomy / Jobs / triggers / compute (docs.databricks.com):**
- jobs/triggers · jobs/scheduled · jobs/trigger-table-update · jobs/file-arrival-triggers · jobs/run-serverless-jobs · jobs/compute · jobs/ · release-notes/product/2025/june
- generative-ai/agent-framework/query-agent · author-agent · machine-learning/model-serving/ · score-custom-model-endpoints · dev-tools/auth/oauth-m2m

**Memory / findings / Lakebase (learn.microsoft.com/azure/databricks unless noted):**
- delta/merge · data-governance/.../anomaly-detection/ · oltp/projects/ · oltp/projects/state-management · generative-ai/agent-framework/stateful-agents · databricks.com/product/lakebase · databricks.com/blog/azure-databricks-lakebase-generally-available · databricks.com/product/pricing/lakebase

**Alerting (learn.microsoft.com):**
- admin/workspace-settings/notification-destinations · sql/user/alerts/ · jobs/notifications · jobs/tasks/alert
- Fabric: fabric/real-time-intelligence/data-activator/activator-introduction · fabric/real-time-hub/set-alerts-data-streams

**Evaluation / Tracing / Monitoring (docs.databricks.com + learn.microsoft.com + mlflow.org):**
- mlflow3/genai/eval-monitor/ · .../concepts/scorers · .../concepts/judges/ · .../concepts/production-quality-monitoring · .../production-monitoring
- mlflow3/genai/tracing/ · .../tracing/prod-tracing · .../tracing/app-instrumentation/automatic
- mlflow.org/docs/latest/genai/tracing/ · .../concepts/trace/ · .../tracing/token-usage-cost/
- ai-gateway/inference-tables · machine-learning/model-serving/inference-tables · generative-ai/agent-framework/request-assessment-logs
- databricks.com/blog/building-custom-llm-judges-ai-agent-accuracy · api-docs.databricks.com/.../databricks_agent_monitoring.html

**Cost / Serving (databricks.com + docs.databricks.com):**
- databricks.com/product/pricing/foundation-model-serving · product/pricing/model-serving · community.databricks.com/.../demystifying-databricks-pricing-for-ai-agents · machine-learning/model-serving/create-manage-serving-endpoints · model-serving-limits

**Governance / auth / audit (learn.microsoft.com/azure/databricks + docs.databricks.com):**
- generative-ai/agent-framework/agent-authentication-model-serving · agent-authentication · create-custom-tool · generative-ai/mcp/managed-mcp
- data-governance/unity-catalog/access-control/privileges-reference · machine-learning/model-serving/manage-serving-endpoints · security/auth-authz/access-control/serving-endpoint-acl · admin/system-tables/audit-logs · admin/system-tables/ · machine-learning/model-serving/store-env-variable-model-serving

# Research 01 — Databricks SQL (for bi-fabrics-audit-agent)

> Focus area: **Databricks SQL** — SQL Warehouse types/sizing/auto-stop; SQL Statement Execution API; `databricks-sql-connector`; SQL AI functions; alerts/queries/AI-BI dashboards; reading/writing Unity Catalog (UC) tables via SQL; warehouse permissions.
>
> Audit-agent context (READ-ONLY against Fabric/PBI; runs in Databricks): the agent / Databricks App needs to (a) run SQL against UC **without Spark** (App containers have no Spark), (b) persist its own telemetry/results to a `run_history` UC table, (c) query collected capacity data, and (d) optionally enrich with LLM functions. Every item below ends with **How it helps the audit agent**.
>
> Sources verified June 2026 against learn.microsoft.com/azure/databricks and docs.databricks.com. Dates on pages range Feb–Jun 2026.

---

## 0. TL;DR decision summary for the agent

- **Compute:** Use a **Serverless SQL warehouse**, X-Small, **Auto Stop = 5–10 min**. Fast cold start (2–6 s) is critical for an interactive App/agent; serverless adds Predictive IO + Intelligent Workload Management. `ai_query`/`ai_classify`/`ai_forecast` require Pro/Serverless (NOT Classic).
- **Run SQL from the App (no Spark):** Two interchangeable paths —
  1. **SQL Statement Execution API** (`POST /api/2.0/sql/statements`) — pure REST, no driver, ideal for a stateless container/MCP tool. Best for large reads via `EXTERNAL_LINKS`.
  2. **`databricks-sql-connector`** (PEP-249 lib) — ergonomic Python `cursor.execute(...)`, native parameter binding, OAuth M2M (service principal). Best for `INSERT`/`run_history` writes and transactional-style loops.
- **Writes:** `CREATE TABLE IF NOT EXISTS run_history ...` then `executemany("INSERT INTO run_history VALUES (?, ...)", rows)` (connector) or a parameterized `INSERT` statement via the Execution API. Both honor UC table ACLs.
- **Identity:** Service principal with OAuth M2M; needs **CAN USE** on the warehouse + UC `USE CATALOG`/`USE SCHEMA`/`SELECT`/`MODIFY` on the run_history schema.
- **LLM enrichment in SQL:** `ai_query('databricks-claude-...', prompt)` lets the agent batch-summarize/classify capacity findings inside SQL; `ai_forecast()` projects capacity usage; `ai_classify()` buckets findings by severity.
- **Surfacing results:** Databricks SQL **alerts** (schedule + condition → Teams/email/webhook) and **AI/BI dashboards** (Lakeview API, programmatic) are pull/push surfaces over the same UC tables.

---

## 1. SQL Warehouse types: serverless / pro / classic

**TITLE:** SQL warehouse types
**URL:** https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/warehouse-types
(AWS mirror: https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-types)

**Summary / feature matrix (exact):**

| Warehouse type | Photon Engine | Predictive IO | Intelligent Workload Management (IWM) | Compute location | Typical startup |
| --- | --- | --- | --- | --- | --- |
| Serverless | ✓ | ✓ | ✓ | Databricks account | **2–6 seconds** |
| Pro | ✓ | ✓ | — | Your Azure subscription | ~4 minutes |
| Classic | ✓ | — | — | Your Azure subscription | ~4 minutes |

- **Serverless**: best startup/IO, AI-driven IWM autoscaling, rapid up/down. Recommended for ETL, BI, exploratory analysis. SQL warehouses do **not** support credential passthrough — use Unity Catalog for governance.
- **Pro**: compute in your subscription; no IWM; use when serverless unavailable in a region, or you need custom networking / federation to on-prem or in-cloud DBs.
- **Classic**: Photon only; entry-level performance; interactive data exploration.

**Warehouse type defaults (important for IaC / Asset Bundles):**
- UI default (serverless-eligible workspace) = **serverless**; otherwise **pro**.
- **SQL Warehouses API default = `classic`**. To get serverless via API set `enable_serverless_compute = true` **and** `warehouse_type = "pro"`. Databricks recommends always setting these fields explicitly.
- Legacy external Hive metastore ⇒ serverless not supported.

**How it helps the audit agent:** The App/MCP server is interactive and bursty (a user asks a question → run SQL → answer). Serverless' 2–6 s cold start vs ~4 min for pro/classic is the deciding factor, and `ai_query` requires Pro/Serverless. Pin `enable_serverless_compute=true`, `warehouse_type="pro"` in the bundle so deployments are deterministic.

---

## 2. SQL Warehouse sizing, scaling, queuing, auto-stop

**TITLE:** SQL warehouse sizing, scaling, and queuing behavior
**URL:** https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/warehouse-behavior

**Cluster size → instances (classic/pro; serverless price/perf is similar):**

| Cluster size | Driver | Worker count (all workers Standard_E8ds_v4) |
| --- | --- | --- |
| 2X-Small | Standard_E8ds_v4 | 1 |
| X-Small | Standard_E8ds_v4 | 2 |
| Small | Standard_E16ds_v4 | 4 |
| Medium | Standard_E32ds_v4 | 8 |
| Large | Standard_E32ds_v4 | 16 |
| X-Large | Standard_E64ds_v4 | 32 |
| 2X-Large | Standard_E64ds_v4 | 64 |
| 3X-Large | Standard_E64ds_v4 | 128 |
| 4X-Large | Standard_E64ds_v4 | 256 |
| 5X-Large (Public Preview, pro+serverless) | Standard_E64ds_v4 | 512 |

- **Size** = compute for a single cluster; the autoscaler adds/removes whole clusters of that size.
- **Scaling (min/max clusters):** classic/pro fixed limit **one cluster per 10 concurrent queries**. Classic/pro autoscale rules: 2–6 min of load → +1 cluster; 6–12 → +2; 12–22 → +3; >22 → +3 plus 1 per extra 15 min. A query waiting **5 min** in queue triggers scale-up; **15 min** of low load triggers scale-down.
- **Queue cap:** max **1,000** queued queries (all types). Watch **Peak Queued Queries** on the monitoring tab.
- **Serverless autoscaling = IWM** (ML-driven): predicts each query's resource need, queues if no capacity, provisions more clusters when wait times rise, scales down keeping recent-peak headroom.
- **Sizing guidance:** start with one larger warehouse; size down if needed. Increase size if queries spill to disk (check **Bytes spilled to disk** in query profile).

**Auto Stop (idle shutdown):**
- **Serverless:** default **10 min**, minimum **5 min** (via UI). (Source: warehouse-types/behavior + create docs.)
- **Pro/Classic:** default **45 min**, minimum **10 min**.
- Idle warehouses keep accruing DBU + cloud charges until stopped.

**How it helps the audit agent:** For a cost-conscious audit tool, X-Small serverless with Auto Stop 5 min keeps idle cost near-zero between runs while preserving instant restart. The agent itself can *audit warehouse cost behavior* by reading `system.compute.warehouses` and warehouse-events tables (see §8/§9).

---

## 3. SQL Statement Execution API — `POST /api/2.0/sql/statements` (CRITICAL)

**TITLE:** Statement Execution API: Run SQL on warehouses (tutorial) + REST reference
**URLs:**
- Tutorial: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/sql-execution-tutorial
- REST reference: https://docs.databricks.com/api/workspace/statementexecution
- Get statement: https://docs.databricks.com/api/workspace/statementexecution/getstatement
- Get result chunk by index: https://docs.databricks.com/api/workspace/statementexecution/getstatementresultchunkn

**Why it matters:** Pure HTTPS, **no Spark, no driver/ODBC** — the App/MCP container just needs a token + `requests`. This is the recommended path for stateless serverless containers.

### 3.1 Endpoints
| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/2.0/sql/statements/` | Submit a statement; may return result inline or a statement_id to poll |
| GET | `/api/2.0/sql/statements/{statement_id}` | Get status + (if SUCCEEDED) first chunk |
| GET | `/api/2.0/sql/statements/{statement_id}/result/chunks/{chunk_index}` | Fetch a specific result chunk |
| POST | `/api/2.0/sql/statements/{statement_id}/cancel` | Cancel a running statement |

### 3.2 Request body fields (exact)
- `warehouse_id` (required)
- `statement` (the SQL; use named params `:name`)
- `catalog`, `schema` (set UC context for the statement)
- `parameters`: array of `{ "name": ..., "value": ..., "type": ... }` — `type` optional, default `STRING`. Types include `DECIMAL(18,2)`, `DATE`, `INT`, etc.
- `wait_timeout`: `"0s"` (return immediately) or `"5s"`–`"50s"` inclusive. Default behavior: returns after 10 s with just ID + status if not done.
- `on_wait_timeout`: `"CONTINUE"` (default) or `"CANCEL"`.
- `disposition`: `"INLINE"` (default) or `"EXTERNAL_LINKS"`.
- `format`: `"JSON_ARRAY"` (default, inline), `"ARROW_STREAM"`, or `"CSV"` (Arrow/CSV require EXTERNAL_LINKS).
- `byte_limit`: cap returned bytes (e.g. `1000`).
- `row_limit`: cap rows (instead of a SQL `LIMIT`).
- `query_tags`: `[{"key":"team","value":"finance"}]` (Public Preview) → surfaces in `system.query.history`.
- If result exceeds `byte_limit`/`row_limit`, response sets `"truncated": true`.

### 3.3 Example request (parameterized, INLINE JSON)
```bash
POST https://${DATABRICKS_HOST}/api/2.0/sql/statements/
Authorization: Bearer ${DATABRICKS_TOKEN}
Content-Type: application/json

{
  "warehouse_id": "<id>",
  "catalog": "samples",
  "schema": "tpch",
  "statement": "SELECT l_orderkey, l_extendedprice, l_shipdate FROM lineitem WHERE l_extendedprice > :extended_price AND l_shipdate > :ship_date LIMIT :row_limit",
  "parameters": [
    { "name": "extended_price", "value": "60000", "type": "DECIMAL(18,2)" },
    { "name": "ship_date", "value": "1995-01-01", "type": "DATE" },
    { "name": "row_limit", "value": "2", "type": "INT" }
  ]
}
```
> Parameterized queries (colon-prefixed `:name`) are the recommended defense against SQL injection — args are bound as literal values, never string-concatenated.

### 3.4 Statement states
`PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELED`, `CLOSED`. (`CLOSED` = result fully fetched / expired; cannot fetch further.)

### 3.5 Success response shape (INLINE / JSON_ARRAY)
```json
{
  "statement_id": "00000000-0000-0000-0000-000000000000",
  "status": { "state": "SUCCEEDED" },
  "manifest": {
    "format": "JSON_ARRAY",
    "schema": { "column_count": 3, "columns": [ { "name": "l_orderkey", "position": 0, "type_name": "LONG", "type_text": "BIGINT" }, ... ] },
    "chunks": [ { "chunk_index": 0, "row_count": 2, "row_offset": 0 } ],
    "total_chunk_count": 1, "total_row_count": 2, "truncated": false
  },
  "result": {
    "chunk_index": 0,
    "row_count": 2, "row_offset": 0,
    "data_array": [ ["2","71433.16","1997-01-28"], ["7","86152.02","1996-01-15"] ]
  }
}
```
Timeout-before-ready response: `{ "statement_id": "...", "status": { "state": "PENDING" } }`.

### 3.6 Async polling + chunk pagination
1. Submit with `wait_timeout` (e.g. `"30s"`). If state is `PENDING`/`RUNNING`, **poll** `GET /api/2.0/sql/statements/{statement_id}` until `SUCCEEDED`/`FAILED`.
2. The first chunk arrives in `result`. For more chunks, follow `result.next_chunk_internal_link` (a ready-to-call path like `/api/2.0/sql/statements/{id}/result/chunks/1?row_offset=188416`) or call the chunk endpoint by index. `next_chunk_internal_link` = `null` when done.
3. **As soon as the last chunk is fetched, the statement is CLOSED** — you cannot re-fetch.

### 3.7 Large results — `EXTERNAL_LINKS` + `ARROW_STREAM`
- **INLINE results are capped at 25 MiB** — exceeding it fails and cancels the statement.
- Set `"disposition":"EXTERNAL_LINKS"` (+ `"format":"ARROW_STREAM"` or `CSV`/`JSON`). Response `result.external_links[]` contains a pre-signed (SAS) `external_link` URL + `expiration`, `byte_count`, `chunk_index`, and `next_chunk_internal_link`.
- **Download the SAS URL with plain HTTP and DO NOT send the Databricks `Authorization` header** (the SAS token is embedded; adding the header risks leaking credentials). Protect SAS URLs/tokens.
- EXTERNAL_LINKS can be disabled per-account via a support case.

### 3.8 Security model
TLS 1.2+ only. Caller must authenticate (PAT / OAuth / Entra ID token) AND have **CAN USE** on the warehouse AND UC/table-ACL permission on every object in the statement. Only the executing user/SP can fetch that statement's results. Can be restricted via IP access lists.

**How it helps the audit agent:**
- The **MCP "run_sql" tool** and the App's data layer can be a thin `requests` wrapper over this API — no Spark dependency, works in any serverless container.
- **Writes:** submit `INSERT INTO run_history ...` (or `MERGE`) as a parameterized statement to log each audit run.
- **Reads:** capacity queries return inline (<25 MiB) for the common case; switch to EXTERNAL_LINKS/Arrow when exporting big telemetry slices.
- `query_tags` lets the agent tag every statement (e.g. `{"app":"bi-fabrics-audit"}`) so its own warehouse usage is auditable in `system.query.history`.

---

## 4. `databricks-sql-connector` (Python library)

**TITLE:** Databricks SQL Connector for Python
**URLs:**
- https://learn.microsoft.com/en-us/azure/databricks/dev-tools/python-sql-connector
- PyPI: https://pypi.org/project/databricks-sql-connector/
- GitHub: https://github.com/databricks/databricks-sql-python
- Native params doc: https://github.com/databricks/databricks-sql-python/blob/main/docs/parameters.md

**Install:** `pip install databricks-sql-connector` (lean) or `pip install databricks-sql-connector[pyarrow]` (adds PyArrow → CloudFetch + Arrow). PyArrow is **not** bundled in connector ≥4.0.0. Python ≥3.8. PEP-249 compliant.

**`sql.connect(...)` key parameters:**
- `server_hostname` (req) — e.g. `adb-1234567890123456.7.azuredatabricks.net`
- `http_path` (req) — warehouse path `/sql/1.0/warehouses/<id>` (or all-purpose compute path; **jobs compute not supported**)
- `access_token` / `auth_type` / `credentials_provider` / `username` / `password` — auth
- `catalog` (initial UC catalog; default `hive_metastore`) and `schema` (default `default`)
- `session_configuration` — dict of Spark session conf (`SET key=val`)
- `http_headers`, `use_cloud_fetch` (default `True`), `user_agent_entry`, `enable_telemetry` (set `0` to disable), `query_tags` (Public Preview; v4.1.3 session-level / v4.2.6 statement-level)

**Auth types supported:** PAT, Microsoft Entra ID token, OAuth M2M (SP, ≥2.7.0 + databricks-sdk ≥0.18.0), OAuth U2M (≥2.7.0 + sdk ≥0.19.0). **Not yet supported:** Azure managed identities, Entra service principals (via `auth/azure-sp`), Azure CLI auth.

**OAuth M2M (service principal) — recommended for the agent:**
```python
from databricks.sdk.core import Config, oauth_service_principal
from databricks import sql
import os
server_hostname = os.getenv("DATABRICKS_SERVER_HOSTNAME")

def credential_provider():
    config = Config(
        host          = f"https://{server_hostname}",
        client_id     = os.getenv("DATABRICKS_CLIENT_ID"),
        client_secret = os.getenv("DATABRICKS_CLIENT_SECRET"))
    return oauth_service_principal(config)

with sql.connect(server_hostname      = server_hostname,
                 http_path            = os.getenv("DATABRICKS_HTTP_PATH"),
                 credentials_provider = credential_provider) as connection:
    ...
```

**Cursor methods:** `execute(operation, parameters=None)`, `executemany(operation, seq_of_parameters)`, `fetchone()`, `fetchmany(size)`, `fetchall()`, `fetchmany_arrow(size)`, `fetchall_arrow()` (PyArrow Table), `cancel()`, `close()`. Metadata: `catalogs()`, `schemas()`, `tables()`, `columns()`. `arraysize` default `10000`.

**Native parameter binding (connector ≥3.0.0; safe from injection):** positional `?` markers —
```python
cursor.execute("SELECT * FROM samples.nyctaxi.trips WHERE pickup_zip = ? LIMIT ?", ['10019', 2])
```

**Write pattern (directly applicable to `run_history`):**
```python
with sql.connect(...) as connection:
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE IF NOT EXISTS squares (x int, x_squared int)")
        squares = [(i, i*i) for i in range(100)]
        cursor.executemany("INSERT INTO squares VALUES (?, ?)", squares)
        cursor.execute("SELECT * FROM squares LIMIT ?", [10])
        result = cursor.fetchall()
```
> For *large* loads, stage to cloud storage then `COPY INTO` rather than row-by-row INSERT.

**UC volumes file ops:** set `staging_allowed_local_path="/tmp/"` then `PUT '/tmp/x.csv' INTO '/Volumes/main/default/vol/x.csv' OVERWRITE` / `GET ... TO ...` / `REMOVE ...`.

**Row access:** `Row` is tuple-like — `row.col_name`, `row[0]`, or `row.asDict()`.

**Spark→Python type map (selected):** `bigint→int`, `decimal→decimal.Decimal`, `double→float`, `date→datetime.date`, `timestamp→datetime.datetime`, `array→numpy.ndarray`, `map/struct→str`, `null→NoneType`.

**How it helps the audit agent:** Cleanest path for the **`run_history` writer** and any transactional loop — `executemany` with native `?` params handles batched audit-result inserts safely. OAuth M2M with the audit SP keeps identity consistent with the rest of the agent (token managed by the SDK). `fetchall_arrow()` is handy if results feed pandas/analysis. Use the Statement Execution API instead when you want zero driver deps in a minimal container; use the connector when you want PEP-249 ergonomics and pooling within a longer-lived process.

---

## 5. SQL AI Functions

**TITLE:** Enrich data using AI Functions (umbrella)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-functions
**Status:** Public Preview. Built-in SQL functions; runnable from Databricks SQL, notebooks, Lakeflow pipelines, Workflows. Two classes: **task-specific** (managed models) and **`ai_query`** (general-purpose, any endpoint).

### 5.1 Full task-specific function list
| Function | Purpose |
| --- | --- |
| `ai_parse_document` | Parse text/tables/figures + layout from unstructured docs |
| `ai_extract` | Extract structured fields per a schema you define |
| `ai_classify` | Classify text against your labels |
| `ai_prep_search` (Beta) | Turn parsed docs into search-ready chunks for RAG |
| `ai_fix_grammar` | Correct grammar |
| `ai_translate` | Translate to a target language |
| `ai_summarize` | Summarize text |
| `ai_mask` | Mask specified entities |
| `ai_analyze_sentiment` | Sentiment: `positive` / `negative` / `neutral` / `mixed` |
| `ai_similarity` | Semantic similarity score between two strings |
| `ai_gen` | Answer an arbitrary prompt |
| `ai_forecast` | Table-valued time-series forecast |
| `vector_search` | Query an AI Search / vector index |

**Cost attribution:** Most batch AI usage bills under `system.billing.usage` `billing_origin_product = "MODEL_SERVING"`, `offering_type = "BATCH_INFERENCE"`. `ai_parse_document` / `ai_extract` / `ai_classify` bill under `billing_origin_product = "AI_FUNCTIONS"` (e.g. `product_features.ai_functions.ai_function = "AI_PARSE_DOCUMENT"`).
**Production best practice:** submit the full dataset in one query (auto parallelize/retry/scale); prefer Databricks-hosted `databricks-` models; set `failOnError => false` on large jobs.

### 5.2 `ai_query` (general-purpose) — KEY for the agent
**URLs:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_query and how-to https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-query

**Requirements:** NOT available on SQL **Classic**; on **pro** warehouses requires Azure Private Link; DBR 15.4 LTS+ recommended; workspace in a supported Model Serving region; definer needs **`CAN QUERY`** on the serving endpoint.

**Syntax:**
```
-- foundation / external model (request is a STRING):
ai_query(endpoint, request)
-- custom model with schema:
ai_query(endpoint, request)
-- custom model without schema:
ai_query(endpoint, request, returnType, failOnError)
```
**Arguments:** `endpoint` (STRING literal name), `request` (STRING for FM/external; single column or STRUCT for custom — STRUCT field names must match model inputs), `returnType` (optional ≥15.2; inferred from model schema if omitted), `failOnError` (bool, default `true`; `false` returns `STRUCT{response, errorMessage}`), `modelParameters` (`named_struct('max_tokens',…, 'temperature',…)`; temperature default `0.0`), `responseFormat` (`text`/`json_object`/`json_schema`, or DDL-style — structured output, ≥15.4 LTS), `files => content` (multimodal JPEG/PNG).

**Batch-optimized Databricks-hosted models include** (prefixed `databricks-`): `databricks-claude-opus-4-8`, `databricks-claude-opus-4-7`, `databricks-claude-opus-4-6`, `databricks-claude-sonnet-4-6`, `databricks-claude-sonnet-4`, `databricks-gpt-oss-120b`, `databricks-gpt-oss-20b`, `databricks-meta-llama-3-3-70b-instruct`, `databricks-meta-llama-3-1-8b-instruct`, `databricks-llama-4-maverick`, `databricks-qwen35-122b-a10b`, `databricks-gte-large-en` (embeddings), etc.

**Examples:**
```sql
-- Foundation model over a column:
SELECT *, ai_query(
  'databricks-meta-llama-3-3-70b-instruct',
  "Name the US state for ZIP: " || pickup_zip
) FROM samples.nyctaxi.trips LIMIT 10;

-- Tuned params:
SELECT text, ai_query("databricks-meta-llama-3-3-70b-instruct",
  "Summarize: " || text,
  modelParameters => named_struct('max_tokens',100,'temperature',0.7)) AS summary
FROM uc_catalog.schema.table;

-- Resilient batch:
SELECT text, ai_query("databricks-meta-llama-3-3-70b-instruct", "Summarize: "||text,
  failOnError => false) AS summary FROM uc_catalog.schema.table;

-- Structured output (DDL form):
SELECT ai_query("databricks-gpt-oss-20b", "Extract: "||abstract,
  responseFormat => 'STRUCT<title:STRING, authors:ARRAY<STRING>, keywords:ARRAY<STRING>>')
FROM research_papers;
```
> Routing note: if Unity AI Gateway (Beta) is enabled, `ai_query` to Databricks endpoints is auto-routed for usage tracking.

**How it helps the audit agent:** The agent can run LLM reasoning **inside SQL, in-warehouse**, over collected capacity rows — e.g. summarize the top throttled items, draft a remediation note per workspace, or classify findings — without round-tripping each row to an external LLM. Using `databricks-claude-*` keeps the reasoning model consistent with the agent's Mosaic AI model serving. Wrap as a UC UDF (`CREATE FUNCTION ... RETURN ai_query(...)`) to make a reusable "explain_finding(text)" callable from any audit query.

### 5.3 `ai_classify` (exact)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_classify
**Syntax:** `ai_classify(content, labels [, options])`. **Use version 2.0** (`options => map('version','2.0')`).
- `content`: STRING or VARIANT (e.g. from `ai_parse_document`/`ai_extract`).
- `labels`: JSON string — array `["urgent","not_urgent"]` or object `{ "label": "description", ... }`. v2: 2–500 labels, names 1–100 chars, descriptions 0–1000 chars. v1: ARRAY<STRING>, 2–20 labels.
- `options`: `version`, `instructions` (<20,000 chars), `multilabel` (`"true"`).
- **Returns (v2):** VARIANT `{ "response": ["label"], "error_message": null }`. v1 returns a plain STRING. Max context 128,000 tokens. **Not on SQL Classic; cannot be used in Views.**
```sql
SELECT ai_classify('My password is leaked.', '["urgent","not_urgent"]');  -- {"response":["urgent"], ...}
```
**How it helps the audit agent:** Bucket each detector finding into severity (`["critical","high","medium","low"]`) or category (`["throttling","cost","security","unused"]`) directly in the result query, with optional `instructions` to encode audit policy.

### 5.4 `ai_forecast` (exact)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_forecast
**Requirement:** Pro or Serverless SQL warehouse. Public Preview, HIPAA-compliant. Table-valued function.
**Syntax:**
```
ai_forecast(observed, horizon => horizon, time_col => time_col, value_col => value_col
  [, group_col => ...] [, prediction_interval_width => 0..1] [, frequency => ...]
  [, seed => ...] [, parameters => '{"global_floor":0, "weekly_order":10, ...}'])
```
- `observed`: TVF input with one time col + ≥1 value col (+ optional group/params). `time_col` must be DATE/TIMESTAMP; `value_col` (string or `ARRAY(...)`) castable to DOUBLE; up to 100 metrics/group.
- **Returns** per value col: `{v}_forecast`, `{v}_upper`, `{v}_lower` (all DOUBLE), future rows only up to (exclusive) `horizon`. Default model = prophet-like piecewise-linear + seasonality.
```sql
WITH agg AS (SELECT DATE(tpep_pickup_datetime) ds, SUM(fare_amount) revenue
             FROM samples.nyctaxi.trips GROUP BY 1)
SELECT * FROM AI_FORECAST(TABLE(agg), horizon=>'2016-03-31', time_col=>'ds', value_col=>'revenue');
```
**How it helps the audit agent:** Forecast Fabric/PBI **capacity usage (CU %) into the future** to flag where a capacity is trending toward exhaustion before throttling occurs — turning the audit from reactive to predictive. Group by `capacity_id`/`workspace_id`, set `parameters => '{"global_floor":0}'`.

### 5.5 `ai_analyze_sentiment` / `ai_gen` (signatures)
- `ai_analyze_sentiment(content)` → STRING ∈ {`positive`,`negative`,`neutral`,`mixed`}. URL: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_analyze_sentiment
- `ai_gen(prompt)` → generated STRING (general content). Listed in the AI Functions umbrella.
**How it helps the audit agent:** `ai_gen` can draft human-readable remediation guidance per finding inside a query; sentiment is lower-value here but available for user-feedback analysis.

---

## 6. Databricks SQL Alerts

**TITLE:** Databricks SQL alerts (new) / legacy alerts
**URLs:**
- https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/
- Legacy: https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/legacy
- SQL alert task for Jobs: https://learn.microsoft.com/en-us/azure/databricks/jobs/alert
- Query patterns: https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/query-patterns

**Summary:** An alert runs a query on a **schedule**, evaluates a **condition** against the result, and **notifies** when met; keeps an evaluation history.
- **New vs legacy:** each new alert **owns its query** (no reusing a saved query); states simplified to **`OK` / `TRIGGERED` / `ERROR`** (no legacy `UNKNOWN`). New + legacy can coexist.
- **Schedule + destinations:** frequency/period/start/timezone; notification destinations include email and configured destinations (Slack, webhook, Microsoft Teams, etc.).
- **Jobs integration:** add a **SQL alert task** in a Lakeflow Job so condition checks run on a pipeline trigger and downstream tasks branch on the result.
- **Alert ACLs:** `NO PERMISSIONS` / `CAN RUN` (see, view, manually trigger, subscribe) / `CAN MANAGE` (edit, modify permissions, delete).
- Documented audit-relevant patterns: alert on **system tables for serverless billing/ingestion cost**, **warehouse events / query history** (slow queries, failed sessions, capacity), **audit-log queries**, **UC data-quality / anomaly monitors**, **metric views**.

**How it helps the audit agent:** A native, no-code **push channel**. The agent writes findings to a UC table; an alert query like "rows in `audit_findings` where severity='critical' in last hour > 0" fires a **Teams** notification — complementary to the agent's own Teams delivery, and a fallback that runs even when the App is idle. The Jobs SQL alert task lets a scheduled audit branch (e.g., only run deep analysis when an anomaly is present).

---

## 7. AI/BI Dashboards (Lakeview)

**TITLE:** AI/BI dashboards — concepts, parameters, and APIs
**URLs:**
- Concepts: https://learn.microsoft.com/en-us/azure/databricks/dashboards/  •  https://docs.databricks.com/aws/en/dashboards/concepts
- Parameters: https://learn.microsoft.com/en-us/azure/databricks/dashboards/manage/filters/parameters
- Create (tutorial): https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/create-dashboard
- Dashboard CRUD via API: https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/dashboard-crud-api
- Lakeview REST API: https://docs.databricks.com/api/workspace/lakeview  (create: https://docs.databricks.com/api/workspace/lakeview/create)
- Manage AI/BI assets via APIs: https://docs.databricks.com/aws/en/ai-bi/admin/use-apis

**Summary:**
- Two areas: **Data tab** (datasets — each based on a table, view, metric view, or custom SQL + calculations) and **Pages** (visualization widgets + filter widgets).
- **Parameters** substitute values into dataset queries at runtime (filter *before* aggregation → efficient). Static (set by author in viz widgets) vs interactive (filter widgets at runtime). Multi-select queries use `ARRAY_CONTAINS(:param, col)` with a NULL check. Parameter/filter state is encoded in the URL (bookmark/share).
- **Lakeview API (still named "Lakeview"; AI/BI = renamed Lakeview dashboards):** create/get/update/list/trash. Key fields: `dashboard_id`, `display_name`, `warehouse_id`, `serialized_dashboard` (JSON string of layout + datasets; excluded from List responses — fetch via Get), `etag` (optimistic concurrency). You can also manage them as generic objects via the Workspace API.
- **Dashboard ACLs:** `CAN VIEW`(API `CAN READ`) / `CAN RUN` / `CAN EDIT` / `CAN MANAGE` (view+interact+refresh+clone at lower tiers; edit/publish at EDIT; permissions/delete at MANAGE).

**How it helps the audit agent:** A **pull surface** over the same UC audit tables. The agent (or bundle) can programmatically publish a "Fabric Capacity Audit" dashboard via the Lakeview create API (pass `serialized_dashboard` + `warehouse_id`), parameterized by `capacity_id`/date so analysts self-serve. Genie/AI-BI is a separate research area; here the relevant point is dashboards read the agent's UC outputs with no extra ETL.

---

## 8. Reading / writing Unity Catalog tables via SQL (the `run_history` pattern)

**Mechanics (combining §3/§4):** Both the Statement Execution API and the connector run ordinary Spark SQL against UC. Set context with `catalog`/`schema` (API body or `sql.connect(catalog=, schema=)`) or fully-qualify (`catalog.schema.table`).

**Create + write `run_history` (connector):**
```python
cursor.execute("""
  CREATE TABLE IF NOT EXISTS audit.bi_fabrics.run_history (
    run_id STRING, started_at TIMESTAMP, finished_at TIMESTAMP,
    capacity_id STRING, status STRING, findings_count INT, details STRING
  ) USING DELTA
""")
cursor.execute(
  "INSERT INTO audit.bi_fabrics.run_history VALUES (?, ?, ?, ?, ?, ?, ?)",
  [run_id, started, finished, cap_id, "SUCCEEDED", n, json_blob])
```
**Same via Execution API (parameterized):**
```json
{ "warehouse_id":"<id>", "catalog":"audit", "schema":"bi_fabrics",
  "statement":"INSERT INTO run_history VALUES (:rid,:s,:f,:cap,:st,:n,:d)",
  "parameters":[
    {"name":"rid","value":"..."},
    {"name":"s","value":"2026-06-22T10:00:00","type":"TIMESTAMP"},
    {"name":"n","value":"3","type":"INT"}, ...] }
```
**Idempotent upserts:** use `MERGE INTO run_history t USING (...) s ON t.run_id=s.run_id WHEN NOT MATCHED THEN INSERT ...`.
**Read for the App:** `SELECT ... FROM audit.bi_fabrics.capacity_usage WHERE capacity_id = :cap AND ts >= :since` — inline if <25 MiB, else EXTERNAL_LINKS.
**Bulk loads:** stage to a UC **volume** then `COPY INTO audit.bi_fabrics.capacity_usage FROM '/Volumes/...'`.

**UC privileges needed on the run_history schema (for the audit SP):** `USE CATALOG` on the catalog, `USE SCHEMA` on the schema, `SELECT` (reads), `MODIFY` (INSERT/UPDATE/DELETE/MERGE), and `CREATE TABLE` if the agent creates the table itself. (Granted via `GRANT ... ON SCHEMA audit.bi_fabrics TO \`audit-sp\``.)

**How it helps the audit agent:** This is the persistence backbone — `run_history` + collected `capacity_usage`/`audit_findings` tables live in UC, written via either SQL path, read by the App, alerts, and dashboards. Delta gives time-travel/audit trail for free.

---

## 9. SQL Warehouse permissions (ACLs)

**TITLE:** Access control lists — SQL warehouse ACLs
**URL:** https://learn.microsoft.com/en-us/azure/databricks/security/auth/access-control/
(Warehouse-specific ACL legacy page: https://docs.databricks.com/security/access-control/sql-endpoint-acl.html)

**Exact SQL warehouse ability matrix:**

| Ability | NO PERMISSIONS | CAN VIEW | CAN MONITOR | CAN USE | IS OWNER | CAN MANAGE |
| --- | --- | --- | --- | --- | --- | --- |
| Start the warehouse | | | ✓ | ✓ | ✓ | ✓ |
| View warehouse details | | ✓ | ✓ | ✓ | ✓ | ✓ |
| View warehouse queries | | ✓ | ✓ | | ✓ | ✓ |
| Run queries | | | ✓ | ✓ | ✓ | ✓ |
| View warehouse monitoring tab | | ✓ | ✓ | | ✓ | ✓ |
| Stop the warehouse | | | | | ✓ | ✓ |
| Delete the warehouse | | | | | ✓ | ✓ |
| Edit the warehouse | | | | | ✓ | ✓ |
| Modify permissions | | | | | ✓ | ✓ |

- **CAN USE** = run queries + start (the minimum the audit SP needs to execute SQL). **CAN MONITOR** = start/run + see queries/monitoring (needed to manually restart and to observe). **CAN MANAGE** / **IS OWNER** = full lifecycle + permissions. (Permissions API name mapping note: UI "CAN VIEW" ⇄ API "CAN READ" — applies to several object types.)
- Warehouse creator + workspace admins get CAN MANAGE automatically. Setting requires **CAN MONITOR** to manually restart.
- **Permissions APIs:** Set — https://docs.databricks.com/api/workspace/warehouses/setpermissions ; Get — https://docs.databricks.com/api/workspace/warehouses/getpermissions. Bundles: https://docs.databricks.com/aws/en/dev-tools/bundles/permissions.

**Related ACLs the agent touches:** **Serving endpoint** needs **CAN QUERY** (required for `ai_query`); **Query**, **Dashboard**, **Alert** ACLs as above.

**How it helps the audit agent:** Grant the audit service principal exactly **CAN USE** on the warehouse (least privilege to run SQL) + **CAN QUERY** on the model-serving endpoint (for `ai_query`) + UC `SELECT`/`MODIFY` on its schema. No need for CAN MANAGE. This keeps the READ-ONLY-against-Fabric agent appropriately minimal on the Databricks side while still able to write its own `run_history`.

---

## 10. Cross-cutting recommendations for the audit agent

1. **Warehouse:** Serverless, X-Small, Auto Stop 5 min; pin `enable_serverless_compute=true`, `warehouse_type="pro"` in the Asset Bundle.
2. **SQL access layer:** Wrap the Statement Execution API as the MCP `run_sql` tool (stateless, no Spark); use `databricks-sql-connector` (OAuth M2M) inside the App process for `run_history` writes / pooled reads. Both with parameterized queries only.
3. **Large reads:** EXTERNAL_LINKS + ARROW_STREAM; strip the Authorization header when downloading SAS URLs.
4. **In-warehouse intelligence:** `ai_query('databricks-claude-...')` for finding summaries/remediation; `ai_classify` for severity/category; `ai_forecast` for capacity-trend prediction. Wrap as UC UDFs for reuse.
5. **Surfacing:** SQL Alerts → Teams/webhook push (fallback + scheduled); Lakeview AI/BI dashboard (programmatic create) → analyst pull. Both read the agent's UC tables.
6. **Permissions:** SP gets warehouse **CAN USE**, endpoint **CAN QUERY**, UC `USE CATALOG`+`USE SCHEMA`+`SELECT`+`MODIFY` (+`CREATE TABLE` if self-provisioning).

---

## Flat URL list (sources)

1. https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/warehouse-types
2. https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-types
3. https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/warehouse-behavior
4. https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-behavior
5. https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/create
6. https://docs.databricks.com/en/compute/sql-warehouse/create-sql-warehouse.html
7. https://learn.microsoft.com/en-us/azure/databricks/dev-tools/sql-execution-tutorial
8. https://docs.databricks.com/aws/en/dev-tools/sql-execution-tutorial
9. https://docs.databricks.com/api/workspace/statementexecution
10. https://docs.databricks.com/api/workspace/statementexecution/getstatement
11. https://docs.databricks.com/api/workspace/statementexecution/getstatementresultchunkn
12. https://learn.microsoft.com/en-us/azure/databricks/dev-tools/python-sql-connector
13. https://docs.databricks.com/aws/en/dev-tools/python-sql-connector
14. https://pypi.org/project/databricks-sql-connector/
15. https://github.com/databricks/databricks-sql-python
16. https://github.com/databricks/databricks-sql-python/blob/main/docs/parameters.md
17. https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-functions
18. https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-query
19. https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_query
20. https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_classify
21. https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_forecast
22. https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_analyze_sentiment
23. https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-functions-example
24. https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/
25. https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/legacy
26. https://learn.microsoft.com/en-us/azure/databricks/sql/user/alerts/query-patterns
27. https://learn.microsoft.com/en-us/azure/databricks/jobs/alert
28. https://learn.microsoft.com/en-us/azure/databricks/dashboards/
29. https://docs.databricks.com/aws/en/dashboards/concepts
30. https://learn.microsoft.com/en-us/azure/databricks/dashboards/manage/filters/parameters
31. https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/create-dashboard
32. https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/dashboard-crud-api
33. https://docs.databricks.com/api/workspace/lakeview
34. https://docs.databricks.com/api/workspace/lakeview/create
35. https://docs.databricks.com/aws/en/ai-bi/admin/use-apis
36. https://learn.microsoft.com/en-us/azure/databricks/security/auth/access-control/
37. https://docs.databricks.com/security/access-control/sql-endpoint-acl.html
38. https://docs.databricks.com/api/workspace/warehouses/setpermissions
39. https://docs.databricks.com/api/workspace/warehouses/getpermissions
40. https://docs.databricks.com/aws/en/dev-tools/bundles/permissions
41. https://learn.microsoft.com/en-us/azure/databricks/integrations/compute-details
42. https://docs.databricks.com/aws/en/large-language-models/batch-inference-pipelines

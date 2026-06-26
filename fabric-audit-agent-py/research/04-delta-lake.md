# 04 — Delta Lake Tables (run_history + capacity_reporting in Unity Catalog)

Research for **bi-fabrics-audit-agent** — a READ-ONLY Fabric/Power BI capacity audit agent
running in Databricks that persists **audit run history** (`run_history`, one row per audit run,
append-mostly) and **curated capacity metrics** (`capacity_reporting`, upsert/merge) as queryable
**managed Delta tables in Unity Catalog (UC)**, consumed by BI and by the agent.

Scope of THIS file: Delta Lake **tables** only. (UC *volumes*, capacity telemetry, OAuth/scopes,
Fabric/PBI REST, Apps/MCP/Mosaic AI/Claude, Asset Bundles, secrets, Kusto, databricks-sdk basics
are covered elsewhere and are not repeated here, except where a Delta write path requires them.)

Sources are overwhelmingly `docs.databricks.com/aws/en/...` and `learn.microsoft.com/azure/databricks`
(identical content; AWS and Azure mirrors both cited). Delta OSS API + delta-rs cited where the
non-Spark write path requires it.

---

## Design summary (TL;DR for implementers)

- **Create** both tables as UC managed Delta tables by three-level name (`catalog.schema.table`),
  no `USING`, no `LOCATION`. Use `CREATE TABLE IF NOT EXISTS` for idempotent setup.
- **run_history**: `run_id BIGINT GENERATED ALWAYS AS IDENTITY` surrogate key, `run_ts TIMESTAMP
  DEFAULT current_timestamp()`, append per run via `INSERT INTO ... BY NAME` or
  `.write.mode("append").saveAsTable(...)`.
- **capacity_reporting**: upsert with **`MERGE WITH SCHEMA EVOLUTION`** keyed on
  `(capacity_id, metric_date)` so new metric columns flow in automatically.
- **Layout**: use **liquid clustering** (`CLUSTER BY`), NOT partitioning (both tables < 1 TB).
  Enable **predictive optimization** so `OPTIMIZE`/`VACUUM`/`ANALYZE` run automatically.
- **Audit-as-of**: `DESCRIBE HISTORY` maps run → Delta `version`; feed into
  `VERSION AS OF` / `TIMESTAMP AS OF` for "what did capacity look like as of run N / last week."
  **Set `delta.deletedFileRetentionDuration` deliberately** — default 7 days, after which VACUUM
  makes old versions un-queryable (and DBR 18.0+ hard-blocks time travel beyond it).
- **Non-Spark client** writes a `run_history` row via the **SQL Statement Execution API**
  (`POST /api/2.0/sql/statements`, by table name, governed, GA) — NOT delta-rs against a managed table.
- **"What changed between runs"**: enable **Change Data Feed** (`delta.enableChangeDataFeed=true`),
  read with `table_changes('tbl', startVersion[, endVersion])`.
- **Idempotent re-runs**: `INSERT ... REPLACE WHERE run_date = current_date()`, or streaming
  `txnAppId`/`txnVersion`, or a keyed MERGE.

---

# 1. CREATE TABLE (managed UC Delta table) + schema

### 1.1 CREATE TABLE [USING] — core grammar, three-level name, IF NOT EXISTS / OR REPLACE
**TITLE:** CREATE TABLE [USING] (SQL language reference)
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-using
(Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-create-table-using)

**Summary:** Canonical CREATE TABLE reference. Covers `CREATE TABLE [IF NOT EXISTS]`,
`CREATE OR REPLACE TABLE`, the three-level `catalog.schema.table` name, per-column properties
(NOT NULL, GENERATED, DEFAULT, COMMENT), table-level COMMENT and TBLPROPERTIES. Managed tables
default to Delta when no `USING` is given.

```
{ { [CREATE OR] REPLACE TABLE | CREATE [EXTERNAL] TABLE [ IF NOT EXISTS ] }
  table_name
  [ table_specification ]
  [ USING data_source ]
  [ table_clauses ]
  [ AS query ] }

table_specification
  ( { column_identifier column_type [ column_properties ] } [, ...]
    [ , table_constraint ] [...] )

column_properties
  { NOT NULL |
    GENERATED ALWAYS AS ( expr ) |
    GENERATED { ALWAYS | BY DEFAULT } AS IDENTITY [ ( [ START WITH start | INCREMENT BY step ] [...] ) ] |
    DEFAULT default_expression |
    COMMENT column_comment }

table_clauses
  { COMMENT table_comment | TBLPROPERTIES clause | ... }
```

**How it helps:** Base statement for both tables. Use `CREATE TABLE IF NOT EXISTS
catalog.schema.run_history (...)` for idempotent first-run setup; reserve `CREATE OR REPLACE TABLE`
for intentional rebuilds of a curated table.

### 1.2 Managed table creation in Unity Catalog (Delta by default)
**TITLE:** What is a managed table? (Unity Catalog managed tables)
**URL:** https://docs.databricks.com/aws/en/tables/managed
(Azure: https://learn.microsoft.com/en-us/azure/databricks/tables/managed)

**Summary:** A managed UC table uses the three-level name and **no** storage `LOCATION`; Databricks
creates a Delta table by default unless you specify `USING iceberg`. **All reads and writes to
managed tables must use table names** — "Path-based access to Unity Catalog managed tables is not
supported." UC governs storage and lifecycle.

```sql
CREATE TABLE <catalog-name>.<schema-name>.<table-name>(
  <column-specification>
);
```

**How it helps:** A plain `CREATE TABLE main.audit.run_history (...)` (no `USING`, no `LOCATION`)
yields a UC-governed managed Delta table — exactly what you want for queryable, governed audit
history. The "must write by name" rule decides the non-Spark write path (see §7).

### 1.3 IDENTITY columns (auto-increment run_id) + generated columns
**TITLE:** Use identity columns / Generated columns in Delta Lake
**URL:** https://docs.databricks.com/aws/en/delta/generated-columns
(Azure: https://learn.microsoft.com/en-us/azure/databricks/delta/generated-columns)

**Summary:** IDENTITY columns auto-assign unique `BIGINT` surrogate keys. `GENERATED ALWAYS AS
IDENTITY` forbids manual insertion; `GENERATED BY DEFAULT AS IDENTITY` allows it. Generated columns
`GENERATED ALWAYS AS (expr)` derive values from other columns. **Caveats:** identity columns
**disable concurrent transactions** on the table, support only `BIGINT`, can't be partitioned on,
and can't be added/altered via `ALTER TABLE`.

```sql
CREATE TABLE table_name (
  id_col1 BIGINT GENERATED ALWAYS AS IDENTITY,
  id_col2 BIGINT GENERATED ALWAYS AS IDENTITY (START WITH -1 INCREMENT BY 1),
  id_col3 BIGINT GENERATED BY DEFAULT AS IDENTITY
)
-- generated column:
CREATE TABLE t (
  birthDate TIMESTAMP,
  dateOfBirth DATE GENERATED ALWAYS AS (CAST(birthDate AS DATE))
)
```

**How it helps:** `run_id BIGINT GENERATED ALWAYS AS IDENTITY` is the surrogate key for
`run_history`; `run_date DATE GENERATED ALWAYS AS (CAST(run_ts AS DATE))` gives a filter/cluster
-friendly date. Caveat: identity serializes parallel writers — fine for a single sequential audit job.

### 1.4 DEFAULT column values
**TITLE:** ALTER TABLE … COLUMN clause (SET DEFAULT) / column defaults
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-alter-table-manage-column
(Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-alter-table-manage-column)

**Summary:** `DEFAULT default_expression` supplies a value on `INSERT` / `MERGE ... INSERT` when the
column is omitted. Defaults allow literals + built-in functions, not aggregates/window/subqueries.
Set inline at CREATE, or later via `ALTER COLUMN ... SET DEFAULT`. You **cannot** define a DEFAULT
in the same statement that *adds* a column to an existing Delta table; using DEFAULT may require the
`delta.feature.allowColumnDefaults` table feature.

```sql
ALTER TABLE table_name ALTER COLUMN column_name SET DEFAULT default_expression
-- inline at create: column_name TYPE DEFAULT default_expression
```

**How it helps:** `run_ts TIMESTAMP DEFAULT current_timestamp()` and `status STRING DEFAULT
'RUNNING'` give every `run_history` insert sensible audit metadata automatically.

---

# 2. INSERT / UPDATE / DELETE / MERGE (SQL DML)

### 2.1 INSERT INTO (append), INSERT OVERWRITE, BY NAME, REPLACE WHERE
**TITLE:** INSERT INTO (SQL DML)
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-dml-insert-into
(Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-dml-insert-into)

**Summary:** `INSERT INTO` appends; `INSERT OVERWRITE` truncates (or, with `PARTITION`, replaces
matching partitions) before inserting. `BY NAME` matches source→target columns by name not position.
`REPLACE WHERE predicate` does a selective conditional overwrite; `REPLACE USING (cols)` does a
dynamic key-based replace. Optional `WITH SCHEMA EVOLUTION` (DBR 18.1+) auto-adds new source columns.

```
INSERT [ WITH SCHEMA EVOLUTION ] { OVERWRITE | INTO } [ TABLE ] table_name
    [ PARTITION clause ]
    [ ( column_name [, ...] ) | BY NAME ]
    [ REPLACE WHERE predicate | REPLACE USING ( column_name [, ...] ) ]
    query
```
```sql
INSERT INTO students VALUES ('Amy Smith', '123 Park Ave', 111111);        -- append
INSERT INTO target BY NAME SELECT 0 AS n, 'data' AS text;                  -- by name (order-independent)
INSERT OVERWRITE students VALUES ('Ashua Hill', '456 Erica Ct', 111111);  -- full overwrite
INSERT INTO sales REPLACE WHERE tx_date BETWEEN '2022-10-01' AND '2022-10-31'
   VALUES (DATE'2022-10-01', 1237);                                        -- selective overwrite
```

**How it helps:** `INSERT INTO run_history BY NAME SELECT ...` is the primary append path per run
(robust to column reordering); `INSERT INTO ... REPLACE WHERE run_date = current_date()` makes a
re-run of today's audit idempotent without touching prior history.

### 2.2 UPDATE
**TITLE:** UPDATE (Delta Lake on Databricks)
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/delta-update
(Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-update)

**Summary:** Updates rows matching an optional `WHERE`; each `SET` is `column = expr | DEFAULT`.
Subqueries allowed in the predicate, but **`UPDATE ... FROM ... JOIN` is NOT supported** — use MERGE.

```
UPDATE table_name [table_alias]
  SET { { column_name | field_name } = { expr | DEFAULT } } [, ...]
  [WHERE clause]
```
```sql
UPDATE run_history SET status = 'SUCCEEDED', finished_at = current_timestamp()
WHERE run_id = 42;
```

**How it helps:** Flip a `run_history` row's terminal state when an audit run completes/fails.

### 2.3 DELETE
**TITLE:** DELETE FROM (Delta Lake on Databricks)
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/delta-delete-from
(Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-delete-from)

**Summary:** Deletes rows matching `WHERE` (Delta only). Predicate supports `IN`, `NOT IN`,
`EXISTS`, scalar subqueries.

```
DELETE FROM table_name [table_alias] [WHERE predicate]
```
```sql
DELETE FROM run_history WHERE run_date < current_date() - INTERVAL 90 DAYS;
```

**How it helps:** Retention pruning on the append-mostly `run_history`.

### 2.4 MERGE INTO — the upsert core for capacity_reporting
**TITLE:** MERGE INTO (Delta Lake) + Upsert into a Delta table using merge
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/delta-merge-into
(concept: https://docs.databricks.com/aws/en/delta/merge ;
Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-merge-into)

**Summary:** `MERGE INTO` upserts: `WHEN MATCHED` updates/deletes, `WHEN NOT MATCHED [BY TARGET]`
inserts, `WHEN NOT MATCHED BY SOURCE` updates/deletes target rows absent from source. Each clause
takes an optional `AND` condition. `MERGE WITH SCHEMA EVOLUTION` auto-adds new source columns to the
target. Source columns not in target are ignored; safe casts applied.

```sql
MERGE [ WITH SCHEMA EVOLUTION ] INTO target_table [target_alias]
    USING source_table_reference [source_alias]
    ON merge_condition
    { WHEN MATCHED [ AND cond ] THEN matched_action |
      WHEN NOT MATCHED [BY TARGET] [ AND cond ] THEN not_matched_action |
      WHEN NOT MATCHED BY SOURCE [ AND cond ] THEN not_matched_by_source_action } [...]
-- matched_action: DELETE | UPDATE SET * [EXCEPT (col,...)] | UPDATE SET col = expr|DEFAULT [, ...]
-- not_matched_action: INSERT * [EXCEPT (col,...)] | INSERT (cols) VALUES (expr|DEFAULT [, ...])
-- not_matched_by_source_action: DELETE | UPDATE SET col = expr|DEFAULT [, ...]
```
```sql
MERGE WITH SCHEMA EVOLUTION INTO capacity_reporting tgt
USING updates src
ON tgt.capacity_id = src.capacity_id AND tgt.metric_date = src.metric_date
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE THEN UPDATE SET status = 'STALE';
```

**How it helps:** Core write path for `capacity_reporting` — key on `(capacity_id, metric_date)`,
refresh curated metrics, insert new capacities, let `WITH SCHEMA EVOLUTION` absorb new metric
columns, and optionally flag capacities that vanished from a run via `WHEN NOT MATCHED BY SOURCE`.

---

# 3. Append vs Overwrite

### 3.1 DataFrame write modes + overwriteSchema
**TITLE:** Update Delta Lake table schema — "Replace table schema"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/update-schema

**Summary:** `mode("append")` adds rows; `mode("overwrite")` replaces all data but **not** the schema
by default. Add `.option("overwriteSchema","true")` to also replace schema/partitioning. Cannot
combine `overwriteSchema=true` with dynamic partition overwrite.

```python
df.write.mode("append").saveAsTable("target_table")                          # append
df.write.mode("overwrite").saveAsTable("target_table")                        # data overwrite
df.write.option("overwriteSchema","true").mode("overwrite").saveAsTable("target_table")  # schema too
```

**How it helps:** Append accumulates run history; overwriteSchema rebuilds a curated table when its
shape changes fundamentally.

### 3.2 Selective overwrite — replaceWhere / REPLACE WHERE (idempotent re-run)
**TITLE:** Selectively overwrite data with Delta Lake — "REPLACE WHERE"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/delta/selective-overwrite

**Summary:** `replaceWhere` atomically overwrites only rows matching a predicate (e.g. one date
partition), leaving the rest untouched (SQL DBR 12.2 LTS+, Python/Scala 9.1 LTS+). Delta validates
all written rows match the predicate (controlled by
`spark.databricks.delta.replaceWhere.constraintCheck.enabled`). Cannot combine with
`partitionOverwriteMode`.

```python
(replace_data.write.mode("overwrite")
   .option("replaceWhere", "metric_date >= '2026-06-01' AND metric_date <= '2026-06-30'")
   .saveAsTable("capacity_reporting"))
```
```sql
INSERT INTO TABLE events REPLACE WHERE start_date >= '2017-01-01' AND end_date <= '2017-01-31'
SELECT * FROM replace_data;
```

**How it helps:** Idempotent re-runs — re-overwrite only today's run/date partition without touching
prior history, so re-running "today's capacity snapshot" replaces only today's rows.

### 3.3 Dynamic data/partition overwrite — REPLACE USING (recommended), partitionOverwriteMode (legacy)
**TITLE:** Selectively overwrite data with Delta Lake — "REPLACE USING" / "partitionOverwriteMode (legacy)"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/delta/selective-overwrite

**Summary:** `REPLACE USING (cols)` is the modern compute-independent dynamic overwrite — replaces
rows whose key columns compare equal, leaving others unchanged (SQL DBR 16.3+, Python/Scala 18.2+;
serverless + SQL warehouses, no Spark config). Legacy `partitionOverwriteMode=dynamic`
(DBR 11.3 LTS+, classic compute only) overwrites whole partitions; Databricks now recommends
`REPLACE USING` over it.

```sql
INSERT INTO TABLE events REPLACE USING (event_id, start_date) SELECT * FROM source_data;
```
```python
(df.write.mode("overwrite").option("replaceUsing","capacity_id, metric_date")
   .saveAsTable("capacity_reporting"))
```

**How it helps:** Key-based replacement of curated metric rows without enumerating predicates.

---

# 4. Schema evolution

### 4.1 Enabling evolution — mergeSchema, autoMerge, WITH SCHEMA EVOLUTION
**TITLE:** Update Delta Lake table schema — "Enable schema evolution"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/update-schema
(AWS concept page: https://docs.databricks.com/aws/en/data-engineering/schema-evolution)

**Summary:** Four ways, in recommended order: (1) `INSERT WITH SCHEMA EVOLUTION`;
(2) `MERGE WITH SCHEMA EVOLUTION`; (3) per-write `.option("mergeSchema","true")` (batch/streaming);
(4) legacy session config `spark.databricks.delta.schema.autoMerge.enabled=true` (NOT recommended —
silently evolves every write in the session). Per-operation options take precedence over the config.
**Auto-allowed:** adding new columns, reordering, renaming (with column mapping), type *widening*.
**NOT automatic:** narrowing/changing type, renaming without column mapping, dropping a column
(needs `overwriteSchema` rewrite or column mapping). INSERT only adds new columns as the *last* columns.

```python
(spark.read.table("source_table").write
   .option("mergeSchema","true").mode("append").saveAsTable("target_table"))
```
```sql
SET spark.databricks.delta.schema.autoMerge.enabled=true;   -- legacy, avoid in prod
INSERT WITH SCHEMA EVOLUTION INTO target_table SELECT * FROM source_table;
```

**How it helps:** Core "audit schema grows as new metrics are added" mechanism — a new metric column
in a later run auto-adds to the table and back-fills older rows as NULL, so BI never breaks on a
widened schema.

### 4.2 INSERT WITH SCHEMA EVOLUTION (exact behavior)
**TITLE:** INSERT INTO — `WITH SCHEMA EVOLUTION` parameter (DBR 18.1+)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-dml-insert-into

```sql
INSERT WITH SCHEMA EVOLUTION INTO TABLE students SELECT * FROM new_students;
-- a new column present in source is auto-added; pre-existing rows get NULL
```

**How it helps:** SQL-only schema growth from the warehouse path (no PySpark), with NULL back-fill —
exactly the semantics an append-only audit table needs.

### 4.3 How MERGE handles schema evolution
**TITLE:** Automatic schema evolution for merge
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/update-schema
(also https://learn.microsoft.com/en-us/azure/databricks/delta/merge)

**Summary:** With evolution enabled, MERGE handles: (1) source-only column → added to target when
assigned by name or via `UPDATE SET *` / `INSERT *`; (2) target-only column → left as-is for
`UPDATE SET *`, set NULL for `INSERT *`. Without evolution, `*` actions error on mismatch. Enable
per-statement: `MERGE WITH SCHEMA EVOLUTION` (SQL DBR 15.4 LTS+) or `.withSchemaEvolution()`
(DataFrame API). `EXCEPT` can exclude columns.

```python
(targetTable.merge(sourceDF, "source.key = target.key")
   .withSchemaEvolution()
   .whenMatchedUpdateAll().whenNotMatchedInsertAll()
   .whenNotMatchedBySourceDelete().execute())
```

**How it helps:** Upserts the latest curated capacity metrics by key while new metric columns flow in
automatically — a single statement keeps the "current state" table and its schema in sync.

### 4.4 ALTER TABLE — ADD / ALTER / RENAME / DROP COLUMN, SET TBLPROPERTIES
**TITLE:** ALTER TABLE … COLUMN clause
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-alter-table-manage-column

**Summary:** All column DDL. Requires UC `MODIFY`. `ADD COLUMNS` appends nullable columns (no
DEFAULT at add-time; existing rows NULL). `ALTER/CHANGE COLUMN` changes comment/position/nullability/
type/default (`TYPE` widening is Delta-only, DBR 15.2+). DBR 16.3+ allows altering multiple columns in
one statement.

```sql
ALTER TABLE students ADD COLUMN email STRING COMMENT 'student email';
ALTER TABLE students ALTER COLUMN email COMMENT 'primary email address';
ALTER TABLE students DROP COLUMN email;
ALTER TABLE students RENAME COLUMN rollno TO student_id;
ALTER TABLE table_name ALTER COLUMN column_name SET DEFAULT default_expression;
ALTER TABLE table_name SET TBLPROPERTIES ('key' = 'value');
```

**How it helps:** Explicit, controlled evolution — add a documented new metric column, deprecate an
old one, set retention/CDF/clustering via `SET TBLPROPERTIES`.

### 4.5 Column mapping mode (required for RENAME / DROP)
**TITLE:** Rename and drop columns with Delta Lake column mapping
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/features/column-mapping

**Summary:** Metadata-only renames/drops require column mapping. Enable `name` mode on an existing
table via `SET TBLPROPERTIES`, or `id` mode at CREATE (id can't be set later). Needs Delta reader
v2+/writer v5+, DBR 10.4 LTS+ (rename), 11.3 LTS+ (drop). Dropped-column data stays in files until
`REORG TABLE` + `VACUUM`. Removing column mapping rewrites all files.

```sql
ALTER TABLE <table> SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name');
ALTER TABLE <table> RENAME COLUMN old_name TO new_name;
ALTER TABLE <table> DROP COLUMN col_name;
```

**How it helps:** Refactor the audit schema (rename/drop a metric) cheaply without rewriting large
history tables.

---

# 5. Time travel & history (the core BI audit capability)

### 5.1 Time travel query syntax (VERSION AS OF / TIMESTAMP AS OF / @ syntax)
**TITLE:** Work with Delta Lake table history — "Time travel syntax"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/history

**Summary:** Query any prior version by number or timestamp. SQL: `VERSION AS OF n` /
`TIMESTAMP AS OF 'ts'`; PySpark: `.option("versionAsOf", n)` / `.option("timestampAsOf","ts")`;
shorthand `@v<version>` or `@<yyyyMMddHHmmssSSS>`. **Gotcha:** DBR 18.0+ blocks time-travel for
versions older than `deletedFileRetentionDuration` (default 7 days); for UC managed tables this
applies from DBR 12.2+.

```sql
SELECT * FROM capacity_reporting TIMESTAMP AS OF '2026-06-18T22:15:12.013Z';
SELECT * FROM capacity_reporting VERSION AS OF 123;
SELECT * FROM capacity_reporting@v123;                 -- version form
SELECT * FROM capacity_reporting@20260618000000000;    -- timestamp form
```
```python
spark.read.option("versionAsOf", 123).table("capacity_reporting")
spark.read.option("timestampAsOf", "2026-06-18").table("capacity_reporting")
```
"What changed last week" diff:
```sql
SELECT (SELECT count(*) FROM capacity_reporting)
     - (SELECT count(*) FROM capacity_reporting TIMESTAMP AS OF date_sub(current_date(), 7)) AS delta_rows;
```

**How it helps:** Exactly "what did capacity look like as of run N / last week" — read
`capacity_reporting VERSION AS OF <run_version>` (or `TIMESTAMP AS OF date_sub(current_date(),7)`)
to reconstruct any past snapshot and diff against current.

### 5.2 DESCRIBE HISTORY + history schema
**TITLE:** Work with Delta Lake table history — "Retrieve table history" / "History schema"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/history
(grammar: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-describe-history)

**Summary:** `DESCRIBE HISTORY table` returns one row per version, newest first. Columns include
`version`, `timestamp`, `userId`, `userName`, `operation` (WRITE/MERGE/DELETE/RESTORE/OPTIMIZE…),
`operationParameters`, `readVersion`, `isolationLevel`, `isBlindAppend`, `operationMetrics`
(`numOutputRows`, `numTargetRowsInserted/Updated/Deleted`, …), and `userMetadata` (user-defined
commit metadata). History retention governed by `delta.logRetentionDuration` (default 30 days).

```sql
DESCRIBE HISTORY capacity_reporting;
DESCRIBE HISTORY capacity_reporting LIMIT 1;
```

**How it helps:** This *is* the audit log — each run's write is a queryable history row. The agent
maps "run N" → Delta `version`, shows who/when/operation/row-counts per collection, and feeds
`version`+`timestamp` straight into time travel. Tag writes with `userMetadata` (e.g. the audit
run_id) to make runs self-describing in DESCRIBE HISTORY.

### 5.3 Data + log retention (how long versions stay queryable) — CRITICAL
**TITLE:** Work with Delta Lake table history — "Configure data retention for time travel queries"
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/history

**Summary:** To query a past version you must retain BOTH its **log** files AND its **data** files.
`delta.logRetentionDuration` (default `interval 30 days`) controls log/history; 
`delta.deletedFileRetentionDuration` (default `interval 7 days`) is the VACUUM threshold for deleting
unreferenced data files. Because tables are VACUUMed regularly, practical point-in-time queries are
limited to ~7 days unless you raise the deleted-file duration. DBR 18.0+: `logRetentionDuration` must
be ≥ `deletedFileRetentionDuration`. Databricks: don't use history as long-term backup.

```sql
ALTER TABLE capacity_reporting SET TBLPROPERTIES (
  'delta.logRetentionDuration'         = 'interval 90 days',
  'delta.deletedFileRetentionDuration' = 'interval 90 days'
);
```

**How it helps:** Directly sizes the audit window. To let BI time-travel "capacity as of last
quarter," set BOTH retentions to e.g. 90 days — otherwise VACUUM silently makes old runs
un-queryable after 7 days. For true long-term retention, persist dated append rows rather than
relying on time travel.

### 5.4 RESTORE TABLE (roll back to a run/version)
**TITLE:** RESTORE (Delta Lake on Databricks)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-restore

**Summary:** `RESTORE` rolls a table back to a version/timestamp (requires `MODIFY`). Cannot restore
to a version whose data files were already VACUUMed. RESTORE is data-changing
(`dataChange=true`), so streaming readers may reprocess.

```sql
RESTORE TABLE capacity_reporting TO VERSION AS OF 1;
RESTORE TABLE capacity_reporting TO TIMESTAMP AS OF '2026-06-02 00:00:00';
```

**How it helps:** Recovery — if a run writes bad curated metrics, roll back to the last good run
version. Admin-recovery tool, not a routine audit path.

---

# 6. Layout: Liquid clustering / partitioning / OPTIMIZE / VACUUM / auto-optimize

### 6.1 Liquid clustering (recommended over partitioning + ZORDER)
**TITLE:** Use liquid clustering for tables
**URL:** https://docs.databricks.com/aws/en/tables/clustering
(grammar: https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-cluster-by ;
Azure: https://learn.microsoft.com/en-us/azure/databricks/tables/clustering)

**Summary:** Liquid clustering replaces partitioning AND `ZORDER` with one evolvable layout.
"Databricks recommends liquid clustering for all new tables." You can **redefine clustering keys
without rewriting existing data**. Works incrementally with predictive optimization. `CLUSTER BY
AUTO` (DBR 15.4+) lets Databricks pick/adapt keys from query history. **Not compatible with
partitioning or ZORDER.** Updated/merged rows recluster only on `OPTIMIZE`.

```sql
CREATE TABLE run_history (run_id BIGINT, run_ts TIMESTAMP) CLUSTER BY (run_ts);
CREATE OR REPLACE TABLE t (c1 INT, c2 STRING) CLUSTER BY AUTO;
ALTER TABLE table_name CLUSTER BY (new_col1, new_col2);   -- no data rewrite
ALTER TABLE table_name CLUSTER BY NONE;
OPTIMIZE table_name;        -- apply clustering incrementally
OPTIMIZE table_name FULL;   -- force full reclustering (DBR 16.x+)
```
```python
(spark.readStream.table("source_table")
   .writeStream.clusterBy("capacity_id")
   .option("checkpointLocation", checkpointPath)
   .toTable("target_table"))
```

**How it helps:** Cluster `run_history` by `run_ts`/`run_id` and `capacity_reporting` by
`metric_date`/`capacity_id` so BI filters hit far fewer files; keys can be re-tuned (or `AUTO`) later
without rewriting the growing history.

### 6.2 Partitioning — when NOT to
**TITLE:** When to partition tables on Databricks
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/partitions
(AWS: https://docs.databricks.com/aws/en/tables/partitions)

**Summary:** "Databricks recommends liquid clustering for all new Delta Lake tables." "Most tables
with less than 1 TB of data do not require partitions." Partition only on low/known-cardinality
fields; "Databricks recommends you do not partition tables that contain less than a terabyte of
data" and "all partitions contain at least a gigabyte." Unpartitioned tables get automatic ingestion
time clustering (DBR 11.3 LTS+).

```sql
CREATE TABLE table_name (col1 INT, event_date DATE) PARTITIONED BY (event_date);  -- avoid for these tables
```

**How it helps:** Audit tables are well under 1 TB — do NOT partition (you'd create the small-file
problem). Rely on ingestion-time + liquid clustering.

### 6.3 OPTIMIZE (bin-compaction; ZORDER is legacy)
**TITLE:** OPTIMIZE (Delta Lake) / Optimize data file layout
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/delta-optimize
(concept: https://docs.databricks.com/aws/en/delta/optimize)

**Summary:** `OPTIMIZE` coalesces small files via bin-packing (idempotent). `ZORDER BY` is legacy,
incremental, and "you can't use this clause on tables that use liquid clustering." Databricks
recommends liquid clustering over Z-ordering. `FULL` (DBR 16.0+) rewrites all files.

```sql
OPTIMIZE events;
OPTIMIZE events WHERE date >= '2017-01-01';
OPTIMIZE events WHERE date >= current_timestamp() - INTERVAL 1 day ZORDER BY (eventType);  -- legacy
OPTIMIZE events FULL;
```

**How it helps:** Compacts the many small files from frequent appends/upserts into few large files,
so BI scans of `capacity_reporting` touch fewer files. On liquid-clustered tables use plain
`OPTIMIZE` (it both compacts and clusters).

### 6.4 VACUUM (bounds storage AND time travel)
**TITLE:** VACUUM / Remove unused data files with vacuum
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-vacuum
(concept: https://docs.databricks.com/aws/en/delta/vacuum)

**Summary:** `VACUUM` deletes data files no longer referenced and older than the retention threshold
(default 7 days via `delta.deletedFileRetentionDuration`). "If you run VACUUM on a Delta table, you
lose the ability to time travel back to a version older than the specified data retention period." 
`DRY RUN` lists up to 1000 files without deleting. Databricks strongly recommends a retention
interval of ≥ 7 days. DBR 16.1+ adds `FULL`/`LITE`.

```sql
VACUUM eventsTable RETAIN 100 HOURS;
VACUUM eventsTable DRY RUN;
ALTER TABLE table_name SET TBLPROPERTIES ('delta.deletedFileRetentionDuration' = '30 days');
```

**How it helps:** Keeps storage bounded by removing obsolete files from constant upserts; set
`delta.deletedFileRetentionDuration` to match your needed audit time-travel window, and `DRY RUN`
before any aggressive `RETAIN`.

### 6.5 Auto-optimize (optimized writes / auto compaction) + Predictive optimization
**TITLE:** Configure Delta Lake to control data file size + Predictive optimization for UC managed tables
**URL:** https://docs.databricks.com/aws/en/delta/tune-file-size
and https://docs.databricks.com/aws/en/optimizations/predictive-optimization

**Summary:** Optimized writes pre-compact during the write; auto compaction combines small files
after a successful write — together but "not a full replacement for OPTIMIZE" (schedule OPTIMIZE for
> 1 TB). **Predictive optimization** "automatically runs OPTIMIZE, VACUUM, and ANALYZE on Unity
Catalog managed tables" (and incremental clustering for liquid-clustered tables); it does NOT run
ZORDER, supports only UC managed tables, and is enabled by default for accounts created on/after
2024-11-11.

```sql
ALTER TABLE <table> SET TBLPROPERTIES (delta.autoOptimize.optimizeWrite = true);
ALTER TABLE <table> SET TBLPROPERTIES (delta.autoOptimize.autoCompact = true);
ALTER CATALOG [catalog_name] { ENABLE | DISABLE | INHERIT } PREDICTIVE OPTIMIZATION;
DESCRIBE TABLE EXTENDED <table>;   -- verify
```

**How it helps:** For UC managed `run_history`/`capacity_reporting`, predictive optimization runs
OPTIMIZE/VACUUM/ANALYZE/clustering automatically — no maintenance jobs, files stay compact, BI stays
fast. Optimized writes + auto compaction keep files large at write time, ideal for high append/upsert
frequency.

---

# 7. Writing Delta from Python and SQL (incl. non-Spark client)

### 7.1 PySpark — saveAsTable vs save vs insertInto, mergeSchema
**TITLE:** Tutorial: Delta Lake (PySpark write basics)
**URL:** https://docs.databricks.com/aws/en/delta/tutorial
(Azure: https://learn.microsoft.com/en-us/azure/databricks/delta/tutorial ;
schema evolution: https://docs.databricks.com/aws/en/data-engineering/schema-evolution)

**Summary:** Delta is the default format. Write a UC managed table by three-part name with
`saveAsTable()` (resolves columns **by name**, can create/replace) or `writeTo(...).createOrReplace()`.
**`save(path)` is only for external tables/volumes — NOT UC managed tables.** `insertInto()` requires
the table to exist and matches columns **by position**. `.option("mergeSchema","true")` evolves the
schema on append. `.mode(...)`: `append`, `overwrite`, `error`/`errorifexists` (default), `ignore`.

```python
df.write.saveAsTable("workspace.default.people_10k")                 # create (error if exists)
df.write.mode("append").saveAsTable("audit.meta.run_history")        # append one run
df.write.mode("overwrite").saveAsTable("audit.meta.run_history")     # overwrite
df.writeTo("audit.meta.run_history").createOrReplace()               # DataFrameWriterV2
df.write.mode("append").option("mergeSchema","true").saveAsTable("audit.meta.capacity_reporting")
```

**How it helps:** `spark.createDataFrame([row]).write.mode("append").saveAsTable("audit.meta.run_history")`
inserts one run_history row per run from a Spark cluster; `mergeSchema` absorbs new metric columns.

### 7.2 DeltaTable API (Python delta-spark / io.delta.tables) — MERGE example
**TITLE:** Delta Lake Python API (delta-spark) + Upsert into a Delta table using merge
**URL:** https://docs.delta.io/api/latest/python/spark/
and https://docs.databricks.com/aws/en/delta/merge

**Summary:** `delta.tables` exposes `DeltaTable` (handle), `DeltaTableBuilder`
(`createIfNotExists`), and the fluent `DeltaMergeBuilder`. `DeltaTable.forName(spark, "cat.sch.tbl")`
gets a handle; `.merge(source, condition)` + `whenMatched*/whenNotMatched*/whenNotMatchedBySource*`
+ `.execute()` upserts; `.update()`, `.delete()`, `.history()`, `.toDF()` operate on the handle.
`.withSchemaEvolution()` enables MERGE schema evolution.

```python
from delta.tables import DeltaTable

capacityTable = DeltaTable.forName(spark, "audit.meta.capacity_reporting")
dfUpdates = spark.createDataFrame(curated_capacity_rows)

(capacityTable.alias("tgt")
  .merge(dfUpdates.alias("src"),
         "tgt.capacity_id = src.capacity_id AND tgt.metric_date = src.metric_date")
  .whenMatchedUpdate(set={
      "cu_used": "src.cu_used", "cu_limit": "src.cu_limit",
      "utilization_pct": "src.utilization_pct", "updated_at": "src.updated_at"})
  .whenNotMatchedInsert(values={
      "capacity_id": "src.capacity_id", "metric_date": "src.metric_date",
      "cu_used": "src.cu_used", "cu_limit": "src.cu_limit",
      "utilization_pct": "src.utilization_pct", "updated_at": "src.updated_at"})
  .execute())

# Concise: auto-map all + remove stale
(capacityTable.alias("tgt")
  .merge(dfUpdates.alias("src"), "tgt.capacity_id = src.capacity_id")
  .whenMatchedUpdateAll().whenNotMatchedInsertAll().whenNotMatchedBySourceDelete()
  .execute())
```

**How it helps:** Exact pattern for idempotently merging curated `capacity_reporting` rows (update
existing keys, insert new, optionally delete capacities no longer present).

### 7.3 Standalone deltalake (delta-rs) — write WITHOUT Spark + UC caveat
**TITLE:** deltalake (delta-rs) Python usage / API + "Delta Lake without Spark"
**URL:** https://delta-io.github.io/delta-rs/python/usage.html
and https://delta-io.github.io/delta-rs/python/api_reference.html
and https://delta.io/blog/delta-lake-without-spark/

**Summary:** The `deltalake` package (delta-rs Rust core, no Spark/JVM) reads/writes/manages Delta
from plain Python via Arrow (pandas/Polars/DuckDB). `write_deltalake(table_or_uri, data, mode=...)`
writes; `DeltaTable.merge(...)`/`update`/`delete`/`history`/`to_pandas` operate on a table.
**UC caveat (load-bearing):** `write_deltalake` writes to a **path/URI** and does not register in a
catalog; it historically lacked UC credential vending. UC **managed** tables disallow path-based
access (§1.2), and governed external-client writes to managed tables require the Unity REST API +
catalog commits (Beta). **Use delta-rs only for external Delta tables — not UC managed tables.**

```python
mode='error' | 'append' | 'overwrite' | 'ignore'   # write_deltalake mode
```
```python
import pandas as pd
from deltalake import write_deltalake
write_deltalake("path/to/external_table", pd.DataFrame({"x":[1,2,3]}), mode="append")
```

**How it helps:** Confirms the non-Spark client should write `run_history` via the SQL Statement
Execution API (§7.4), NOT by pointing `write_deltalake` at a UC managed table.

### 7.4 SQL Statement Execution API (REST) — non-Spark write by name
**TITLE:** Execute a SQL statement (REST) + Statement Execution API tutorial
**URL:** https://docs.databricks.com/api/workspace/statementexecution/executestatement
and https://docs.databricks.com/aws/en/dev-tools/sql-execution-tutorial
(GA blog: https://www.databricks.com/blog/announcing-general-availability-databricks-sql-statement-execution-api)

**Summary:** `POST /api/2.0/sql/statements` runs any SQL (INSERT/MERGE/CREATE TABLE) on a SQL
warehouse from any HTTP client — no Spark cluster. Required body: `statement`, `warehouse_id`.
Optional: `catalog`, `schema`, `parameters` (named `:param` placeholders, type defaults STRING),
`wait_timeout` (5–50s, default 10s, `0s` returns immediately), `on_wait_timeout`
(`CONTINUE`/`CANCEL`), `disposition` (INLINE/EXTERNAL_LINKS), `format`. Response carries
`statement_id` and `status.state` (PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/CLOSED). Poll with
`GET .../{id}`, cancel with `POST .../{id}/cancel`. INLINE results cap 25 MiB.

```json
{
  "warehouse_id": "abc123def456",
  "catalog": "audit", "schema": "meta",
  "statement": "INSERT INTO run_history VALUES (:run_id, :started_at, :status, :rows_written)",
  "parameters": [
    { "name": "run_id",       "value": "2026-06-22T10:00:00Z-001", "type": "STRING" },
    { "name": "started_at",   "value": "2026-06-22T10:00:00",      "type": "TIMESTAMP" },
    { "name": "status",       "value": "SUCCEEDED",                "type": "STRING" },
    { "name": "rows_written", "value": "1532",                     "type": "INT" }
  ],
  "wait_timeout": "30s"
}
```

**How it helps:** Recommended way for the lightweight non-Spark client to write one `run_history`
row per run — a single parameterized `INSERT`/`MERGE` POST to a SQL warehouse, governed by UC, no
cluster, writes **by name** (required for managed tables).

### 7.5 databricks-sdk wrapper for Statement Execution
**TITLE:** StatementExecutionAPI — Databricks SDK for Python
**URL:** https://databricks-sdk-py.readthedocs.io/en/latest/workspace/sql/statement_execution.html

**Summary:** `WorkspaceClient().statement_execution.execute_statement(...)` is the Pythonic wrapper
over the REST endpoint (same body fields); `get_statement(id)` polls, `cancel_execution(id)` cancels,
`get_statement_result_chunk_n(id, n)` fetches more chunks. Returns a `StatementResponse` with
`statement_id` and `status.state`.

```python
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

w = WorkspaceClient()
resp = w.statement_execution.execute_statement(
    warehouse_id="abc123def456", catalog="audit", schema="meta",
    statement="INSERT INTO run_history VALUES (:run_id, :started_at, :status, :rows_written)",
    parameters=[
        StatementParameterListItem(name="run_id", value="2026-06-22T10:00:00Z-001"),
        StatementParameterListItem(name="started_at", value="2026-06-22T10:00:00", type="TIMESTAMP"),
        StatementParameterListItem(name="status", value="SUCCEEDED"),
        StatementParameterListItem(name="rows_written", value="1532", type="INT"),
    ],
    wait_timeout="30s",
)
print(resp.statement_id, resp.status.state)   # SUCCEEDED
```

**How it helps:** Cleanest non-Spark path to insert a `run_history` row or run a parameterized MERGE
into `capacity_reporting` — handles auth, retries, result polling via `WorkspaceClient`, no cluster.

### 7.6 UC managed tables + external Delta-client write constraints (decisive)
**TITLE:** What is a managed table? + Access Databricks tables from Apache Spark and other clients (Unity REST)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/tables/managed
and https://learn.microsoft.com/en-us/azure/databricks/external-access/unity-rest

**Summary:** "All reads and writes to managed tables must use table names … Path-based access to
Unity Catalog managed tables is not supported." External Delta-client create/write to managed Delta
tables is **Beta** and requires **catalog commits**
(`TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')`), plus: External data access enabled
on the metastore, `EXTERNAL USE SCHEMA`, `SELECT`/`MODIFY`/`CREATE` grants, PAT or OAuth M2M.
External clients **cannot** run `ALTER TABLE`/`OPTIMIZE`/`VACUUM`/`ANALYZE` or create
generated/default/constraint columns on managed tables.

**How it helps:** Decisive guidance — for governed audit writes to UC **managed** tables from a
non-Spark process, prefer the **SQL Statement Execution API** (writes by name, GA). Reserve
delta-rs for **external** Delta tables, or managed only with catalog commits (Beta).

---

# 8. Streaming writes/reads & Change Data Feed (incremental ingestion + change tracking)

### 8.1 Delta streaming WRITES (append/complete, toTable, checkpoint)
**TITLE:** Delta table streaming reads and writes (writes)
**URL:** https://docs.databricks.com/aws/en/structured-streaming/delta-lake
(Azure: https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/delta-lake)

**Summary:** Write into a Delta table with `format("delta")` (implicit via `.toTable()`), a
`checkpointLocation`, and an output mode. `.toTable("cat.sch.tbl")` writes a named UC table.
`outputMode("append")` adds rows; `outputMode("complete")` rewrites the whole table each batch
(aggregations). Recommended checkpoint path: `<table>/_checkpoints`.

```python
(events.writeStream.outputMode("append")
   .option("checkpointLocation", "/tmp/delta/events/_checkpoints/")
   .toTable("events"))
```

**How it helps:** Append-mode writes land each audit run incrementally into history; complete-mode
keeps a curated "current metrics" rollup always in sync.

### 8.2 Trigger intervals — AvailableNow for periodic incremental runs
**TITLE:** Configure Structured Streaming trigger intervals
**URL:** https://docs.databricks.com/aws/en/structured-streaming/triggers

**Summary:** `trigger(availableNow=True)` consumes all currently-available data as an incremental
batch (possibly several micro-batches), then stops — ideal for scheduled runs. `Trigger.Once` is
**deprecated** (DBR 11.3 LTS+) — migrate to `AvailableNow`. `processingTime` runs at a fixed
interval. `AvailableNow` respects `maxFilesPerTrigger`/`maxBytesPerTrigger`.

```python
.trigger(availableNow=True)            # incremental batch then stop (recommended)
.trigger(processingTime='10 seconds')  # fixed interval
.trigger(once=True)                    # deprecated → use availableNow
```

**How it helps:** Run the audit as a periodic Databricks Job with `trigger(availableNow=True)` — it
processes only new data since the last checkpoint and exits, behaving like an incremental batch.

### 8.3 foreachBatch — MERGE/upsert inside a stream
**TITLE:** Use foreachBatch to write to arbitrary data sinks
**URL:** https://docs.databricks.com/aws/en/structured-streaming/foreach

**Summary:** `foreachBatch(fn)` runs an arbitrary batch function per micro-batch, receiving
`(micro_batch_df, batch_id)` — the standard place to run a Delta `MERGE INTO` so changed rows update
in place. `foreachBatch` is **at-least-once**; use `batch_id` (with txnAppId/txnVersion or a keyed
MERGE) for exactly-once.

```python
def process_batch(output_df, batch_id):
    if not output_df.isEmpty():
        (DeltaTable.forName(spark, "audit.meta.capacity_reporting").alias("t")
           .merge(output_df.alias("s"), "t.capacity_id = s.capacity_id")
           .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())

streamingDF.writeStream.foreachBatch(process_batch).start()
```

**How it helps:** Each incremental audit micro-batch upserts curated metrics by key, so the latest
value replaces the old one instead of duplicating rows.

### 8.4 Delta streaming READS (skipChangeCommits, rate limits, starting point)
**TITLE:** Delta table streaming reads and writes (reads)
**URL:** https://docs.databricks.com/aws/en/structured-streaming/delta-lake
(UC streaming: https://docs.databricks.com/aws/en/structured-streaming/unity-catalog)

**Summary:** `spark.readStream.table("…")` streams newly appended files by default. Control
update/delete rewrites with `skipChangeCommits` (DBR 12.2 LTS+, recommended — ignores files rewritten
by UPDATE/MERGE/DELETE/OVERWRITE), legacy `ignoreChanges`, `ignoreDeletes`. Rate-limit with
`maxFilesPerTrigger` (default 1000) / `maxBytesPerTrigger`. Start with `startingVersion` /
`startingTimestamp`. **UC constraint:** Structured Streaming checkpoints must use a path in a UC
external location.

```python
(spark.readStream.option("skipChangeCommits", "true").table("source_table"))
spark.readStream.option("startingVersion", "5").table("user_events")
spark.readStream.option("startingTimestamp", "2018-10-18").table("user_events")
```

**How it helps:** Lets downstream agent/BI logic stream the append-only history table without choking
on compaction/maintenance rewrites, with bounded per-batch size.

### 8.5 Change Data Feed (CDF) — enable + read
**TITLE:** Use Delta Lake change data feed on Databricks
**URL:** https://docs.databricks.com/aws/en/delta/delta-change-data-feed
(feature/retention: https://docs.databricks.com/aws/en/tables/features/change-data-feed)

**Summary:** CDF records row-level inserts/updates/deletes between versions. Enable via
`delta.enableChangeDataFeed = true` at CREATE or `ALTER TABLE`. **Only changes after enablement are
recorded.** Read batch/stream with `.option("readChangeFeed","true")` + `startingVersion`/
`startingTimestamp` (and optional `endingVersion`/`endingTimestamp`). Output adds `_change_type`
(`insert`/`update_preimage`/`update_postimage`/`delete`), `_commit_version`, `_commit_timestamp`.
**Retention:** CDF data follows table retention — change files are deleted by VACUUM; when a version
is removed you can no longer read its CDF. Non-additive schema changes block CDF reads spanning them.

```sql
CREATE TABLE capacity_reporting (...) TBLPROPERTIES (delta.enableChangeDataFeed = true);
ALTER TABLE capacity_reporting SET TBLPROPERTIES (delta.enableChangeDataFeed = true);
```
```python
spark.read.option("readChangeFeed","true").option("startingVersion", 0)
  .option("endingVersion", 10).table("capacity_reporting")
```

**How it helps:** The agent/BI layer reads exactly which capacity rows changed (old vs new via
pre/post-image) between two audit runs, instead of diffing full snapshots.

### 8.6 table_changes() SQL function (pure-SQL CDF for the Fabric/PBI side)
**TITLE:** table_changes table-valued function
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/functions/table_changes

**Summary:** `table_changes(table_str, start [, end])` where start/end are a BIGINT version or a
TIMESTAMP literal. Returns all table columns plus `_change_type` (NOT NULL), `_commit_version`
(NOT NULL), `_commit_timestamp` (NOT NULL). Requires CDF enabled + SELECT/ownership.

```sql
SELECT * FROM table_changes('capacity_reporting', 0, 10);
SELECT * FROM table_changes('capacity_reporting', '2026-06-01T00:00:00.000+0000')
  ORDER BY _commit_version;
```

**How it helps:** Gives the Fabric/Power BI side a pure-SQL way to pull "what capacity rows changed
since version/run N" with commit version + timestamp, no Spark code.

### 8.7 Idempotent writes (txnAppId / txnVersion) + checkpoint exactly-once
**TITLE:** Idempotent writes via txnAppId/txnVersion + foreachBatch idempotency
**URL:** https://docs.databricks.com/aws/en/structured-streaming/delta-lake
(checkpoints: https://docs.databricks.com/aws/en/structured-streaming/checkpoints ;
foreach: https://docs.databricks.com/aws/en/structured-streaming/foreach)

**Summary:** Delta `DataFrameWriter` options `txnAppId` (unique app string) + `txnVersion`
(monotonic, use `batch_id`) dedupe writes — re-running a batch with the same pair makes Delta **ignore
the duplicate**. **Critical:** if you delete the checkpoint and restart, use a **different
`txnAppId`** or restarts may reapply batches. The Delta log + checkpoint give exactly-once stream
processing; checkpoints must live in a UC external location.

```python
def writeIdempotent(batch_df, batch_id):
    (batch_df.write.format("delta").mode("append")
       .option("txnVersion", batch_id).option("txnAppId", app_id)
       .saveAsTable("audit.meta.run_history"))

streamingDF.writeStream.foreachBatch(writeIdempotent).start()
```

**How it helps:** Guarantees re-running/retrying an audit batch does not double-insert history rows —
Delta drops the replayed transaction; combined with the checkpoint, each periodic `availableNow` run
picks up exactly the new data.

---

# 9. Constraints

### 9.1 Constraints overview (enforced vs informational)
**TITLE:** Constraints on Databricks
**URL:** https://docs.databricks.com/aws/en/tables/constraints
(Azure: https://learn.microsoft.com/en-us/azure/databricks/tables/constraints)

**Summary:** `NOT NULL` and `CHECK` are **enforced** — a violating transaction fails.
`PRIMARY KEY`, `FOREIGN KEY`, `UNIQUE` are **informational only** (not enforced) but enable optimizer
rewrites. PK/FK available for UC + Delta in DBR 13.3 LTS+, GA in 15.2+. CHECK constraints are added
via `ALTER TABLE ... ADD CONSTRAINT` after creation.

```sql
CREATE TABLE people10m (id INT NOT NULL, middleName STRING NOT NULL, ...);
ALTER TABLE people10m ALTER COLUMN ssn SET NOT NULL;
ALTER TABLE people10m ADD CONSTRAINT dateWithinRange CHECK (birthDate > '1900-01-01');
ALTER TABLE people10m DROP CONSTRAINT dateWithinRange;
CREATE TABLE T(pk1 INT NOT NULL, pk2 INT NOT NULL, CONSTRAINT t_pk PRIMARY KEY(pk1, pk2));
CREATE TABLE S(pk INT NOT NULL PRIMARY KEY, fk1 INT,
               CONSTRAINT s_t_fk FOREIGN KEY(fk1) REFERENCES T);
```

### 9.2 CONSTRAINT clause reference (PK/FK grammar + RELY)
**TITLE:** CONSTRAINT clause (CREATE TABLE)
**URL:** https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-constraint
(Azure: https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-create-table-constraint)

**Summary:** PK columns are implicitly `NOT NULL`; at most one PK per table; FK parent must have a PK
or UNIQUE. `RELY` (default `NORELY`) tells the optimizer it may exploit the constraint — user ensures
it holds. CHECK constraints are NOT definable inline here; add via `ALTER TABLE`.

```
PRIMARY KEY ( key_column [, ...] ) [ constraint_option ] [...]
FOREIGN KEY ( fk_column [, ...] ) REFERENCES parent_table [ ( parent_column [, ...] ) ]
{ RELY | NORELY }
```

**How it helps:** Declare `run_id` as the informational PK of `run_history` and
`(capacity_id, metric_date)` as PK of `capacity_reporting`, with `FOREIGN KEY (run_id) REFERENCES
run_history` to document lineage and speed BI joins. Use enforced `NOT NULL` on key columns and
`CHECK (cpu_pct BETWEEN 0 AND 100)` for data quality. **Because PK/FK are unenforced, dedupe lives in
your MERGE key logic;** add `RELY` only once uniqueness is guaranteed.

---

# 10. Recommended schema sketch (synthesis)

```sql
-- Append-mostly audit history (one row per run)
CREATE TABLE IF NOT EXISTS main.audit.run_history (
  run_id         BIGINT GENERATED ALWAYS AS IDENTITY COMMENT 'Surrogate key',
  run_ts         TIMESTAMP DEFAULT current_timestamp() COMMENT 'Run start',
  run_date       DATE GENERATED ALWAYS AS (CAST(run_ts AS DATE)),
  status         STRING DEFAULT 'RUNNING' NOT NULL,
  capacity_count INT,
  CONSTRAINT run_history_pk PRIMARY KEY (run_id)
) CLUSTER BY (run_ts)
  COMMENT 'One row per Fabric/PBI capacity audit run'
  TBLPROPERTIES (
    'delta.feature.allowColumnDefaults' = 'supported',
    'delta.enableChangeDataFeed'        = 'true',
    'delta.deletedFileRetentionDuration'= 'interval 90 days',
    'delta.logRetentionDuration'        = 'interval 90 days'
  );

-- Curated, upserted metrics
CREATE TABLE IF NOT EXISTS main.audit.capacity_reporting (
  capacity_id STRING NOT NULL,
  metric_date DATE   NOT NULL,
  run_id      BIGINT,
  cpu_pct     DOUBLE,
  cu_used     DOUBLE,
  cu_limit    DOUBLE,
  updated_at  TIMESTAMP DEFAULT current_timestamp(),
  CONSTRAINT capacity_reporting_pk PRIMARY KEY (capacity_id, metric_date),
  CONSTRAINT capacity_reporting_fk FOREIGN KEY (run_id) REFERENCES main.audit.run_history
) CLUSTER BY (metric_date, capacity_id)
  COMMENT 'Curated capacity metrics, upserted via MERGE'
  TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

-- CHECK constraint added after creation (cannot be inline in CONSTRAINT clause):
ALTER TABLE main.audit.capacity_reporting
  ADD CONSTRAINT cpu_range CHECK (cpu_pct BETWEEN 0 AND 100);
```

**Load-bearing caveats:**
1. An IDENTITY column **disables concurrent transactions** — fine for a single sequential audit job,
   not for parallel writers.
2. PK/FK/UNIQUE are **informational and unenforced** — dedupe must live in MERGE key logic.
3. CHECK constraints are added via `ALTER TABLE ... ADD CONSTRAINT` **after** creation, not inline.
4. `UPDATE ... FROM ... JOIN` is unsupported — use `MERGE INTO`.
5. Set **both** `delta.logRetentionDuration` and `delta.deletedFileRetentionDuration` to your
   look-back window, or VACUUM makes old versions un-queryable after ~7 days (DBR 18.0+ hard-blocks).
6. For UC **managed** tables, non-Spark clients must write **by name** via the SQL Statement
   Execution API — not delta-rs by path.

---

# 11. Flat URL list (all sources)

- https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-using
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-create-table-using
- https://docs.databricks.com/aws/en/tables/managed
- https://learn.microsoft.com/en-us/azure/databricks/tables/managed
- https://docs.databricks.com/aws/en/delta/generated-columns
- https://learn.microsoft.com/en-us/azure/databricks/delta/generated-columns
- https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-alter-table-manage-column
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-alter-table-manage-column
- https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-dml-insert-into
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-dml-insert-into
- https://docs.databricks.com/aws/en/sql/language-manual/delta-update
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-update
- https://docs.databricks.com/aws/en/sql/language-manual/delta-delete-from
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-delete-from
- https://docs.databricks.com/aws/en/sql/language-manual/delta-merge-into
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-merge-into
- https://docs.databricks.com/aws/en/delta/merge
- https://learn.microsoft.com/en-us/azure/databricks/delta/merge
- https://learn.microsoft.com/en-us/azure/databricks/delta/selective-overwrite
- https://learn.microsoft.com/en-us/azure/databricks/tables/update-schema
- https://docs.databricks.com/aws/en/data-engineering/schema-evolution
- https://learn.microsoft.com/en-us/azure/databricks/tables/features/column-mapping
- https://learn.microsoft.com/en-us/azure/databricks/tables/history
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-describe-history
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-restore
- https://docs.databricks.com/aws/en/tables/clustering
- https://learn.microsoft.com/en-us/azure/databricks/tables/clustering
- https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-cluster-by
- https://docs.databricks.com/aws/en/delta/clustering
- https://learn.microsoft.com/en-us/azure/databricks/tables/partitions
- https://docs.databricks.com/aws/en/tables/partitions
- https://docs.databricks.com/aws/en/sql/language-manual/delta-optimize
- https://docs.databricks.com/aws/en/delta/optimize
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/delta-vacuum
- https://docs.databricks.com/aws/en/sql/language-manual/delta-vacuum
- https://docs.databricks.com/aws/en/delta/vacuum
- https://docs.databricks.com/aws/en/delta/tune-file-size
- https://docs.databricks.com/aws/en/optimizations/predictive-optimization
- https://docs.databricks.com/aws/en/delta/table-properties
- https://docs.databricks.com/aws/en/delta/tutorial
- https://learn.microsoft.com/en-us/azure/databricks/delta/tutorial
- https://docs.delta.io/api/latest/python/spark/
- https://delta-io.github.io/delta-rs/python/usage.html
- https://delta-io.github.io/delta-rs/python/api_reference.html
- https://delta.io/blog/delta-lake-without-spark/
- https://github.com/delta-io/delta-rs/discussions/2066
- https://docs.databricks.com/api/workspace/statementexecution/executestatement
- https://docs.databricks.com/api/workspace/statementexecution
- https://docs.databricks.com/aws/en/dev-tools/sql-execution-tutorial
- https://learn.microsoft.com/en-us/azure/databricks/dev-tools/sql-execution-tutorial
- https://learn.microsoft.com/en-us/azure/databricks/integrations/msft-power-platform-usage
- https://learn.microsoft.com/en-us/azure/databricks/partners/bi/fabric
- https://learn.microsoft.com/en-us/azure/databricks/external-access/unity-rest
- https://databricks-sdk-py.readthedocs.io/en/latest/workspace/sql/statement_execution.html
- https://databricks-sdk-py.readthedocs.io/en/latest/clients/workspace.html
- https://databricks-sdk-py.readthedocs.io/en/latest/workspace/sql/warehouses.html
- https://www.databricks.com/blog/announcing-general-availability-databricks-sql-statement-execution-api
- https://docs.databricks.com/aws/en/structured-streaming/delta-lake
- https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/delta-lake
- https://docs.databricks.com/aws/en/structured-streaming/triggers
- https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/triggers
- https://docs.databricks.com/aws/en/pyspark/reference/classes/datastreamwriter/trigger
- https://docs.databricks.com/aws/en/structured-streaming/foreach
- https://learn.microsoft.com/en-us/azure/databricks/structured-streaming/foreach
- https://docs.databricks.com/aws/en/structured-streaming/unity-catalog
- https://docs.databricks.com/aws/en/delta/delta-change-data-feed
- https://learn.microsoft.com/en-us/azure/databricks/delta/delta-change-data-feed
- https://docs.databricks.com/aws/en/tables/features/change-data-feed
- https://learn.microsoft.com/en-us/azure/databricks/tables/features/change-data-feed
- https://docs.databricks.com/aws/en/sql/language-manual/functions/table_changes
- https://docs.databricks.com/aws/en/structured-streaming/checkpoints
- https://docs.databricks.com/aws/en/structured-streaming/production
- https://docs.databricks.com/aws/en/tables/constraints
- https://learn.microsoft.com/en-us/azure/databricks/tables/constraints
- https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-create-table-constraint
- https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-syntax-ddl-create-table-constraint

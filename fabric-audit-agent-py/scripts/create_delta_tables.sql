-- Phase 5 Delta tables for the Fabric Audit Agent.
-- Run once in a Databricks notebook or SQL warehouse against Unity Catalog.
-- Replace ${catalog} and ${schema} with actual values (default: main.bi_fabrics_agent).
--
-- All four tables: liquid clustering (no partition columns), 90-day retention.

-- 1. run_history — one row per sweep/audit run (replaces the local JSON store)
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.run_history (
  run_at          STRING       COMMENT 'ISO-8601 UTC timestamp of the run',
  tenant          STRING       COMMENT 'Tenant identifier',
  peak_cu_pct     DOUBLE       COMMENT 'Peak capacity CU% observed in the run',
  verdict_decision STRING      COMMENT 'Capacity verdict: healthy/optimize/size-up',
  sla_breached_count INT       COMMENT 'Number of SLA breaches at time of run',
  duration_ms     DOUBLE       COMMENT 'Wall-clock duration of the run in milliseconds',
  errored         BOOLEAN      COMMENT 'Whether the run encountered an error',
  token_usage     STRING       COMMENT 'JSON-encoded LLM token usage (nullable)',
  findings_json   STRING       COMMENT 'JSON array of {key, level, where, what, suppressed}'
)
USING DELTA
COMMENT 'Audit sweep run history — one row per run_audit() invocation'
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 90 days',
  'delta.logRetentionDuration' = 'interval 90 days'
);

-- 2. audit_findings — one row per finding per run (for Phase 6 context injection)
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.audit_findings (
  run_at          STRING       COMMENT 'ISO-8601 UTC timestamp of the parent run',
  tenant          STRING       COMMENT 'Tenant identifier',
  finding_key     STRING       COMMENT 'Stable finding key (e.g. capacity.concentration.user)',
  level           STRING       COMMENT 'Severity: Info/Warning/Critical',
  finding_type    STRING       COMMENT 'Finding type (e.g. capacity.concentration)',
  resource        STRING       COMMENT 'Resource the finding applies to',
  what_text       STRING       COMMENT 'Human-readable finding description',
  confidence      STRING       COMMENT 'Confidence level: high/medium/low',
  suppressed      BOOLEAN      COMMENT 'Whether the finding was suppressed/snoozed'
)
USING DELTA
COMMENT 'Individual audit findings — one row per finding per run, queryable for Phase 6 context'
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 90 days',
  'delta.logRetentionDuration' = 'interval 90 days'
);

-- 3. capacity_reporting — capacity CU% time-series snapshots
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.capacity_reporting (
  run_at          STRING       COMMENT 'ISO-8601 UTC timestamp of the parent run',
  tenant          STRING       COMMENT 'Tenant identifier',
  window_ts       STRING       COMMENT 'Timestamp of the 30-second capacity window',
  cu_pct          DOUBLE       COMMENT 'Capacity utilization percentage',
  base_cu         INT          COMMENT 'Base capacity units (from SKU)',
  overage_total_ms DOUBLE      COMMENT 'Cumulative overage in milliseconds',
  interactive_pct DOUBLE       COMMENT 'Interactive workload percentage (nullable)',
  background_pct  DOUBLE       COMMENT 'Background workload percentage (nullable)'
)
USING DELTA
COMMENT 'Capacity CU% time-series snapshots — one row per 30-second window per run'
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 90 days',
  'delta.logRetentionDuration' = 'interval 90 days'
);

-- 4. concentration_alerts — concentration alert events
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.concentration_alerts (
  alert_at        STRING       COMMENT 'ISO-8601 UTC timestamp of the alert',
  tenant          STRING       COMMENT 'Tenant identifier',
  alert_type      STRING       COMMENT 'Alert trigger: concentration/throttle/spike',
  resource        STRING       COMMENT 'Resource that triggered the alert (user/item)',
  share_pct       DOUBLE       COMMENT 'Share percentage that triggered the alert',
  reason          STRING       COMMENT 'Human-readable alert reason',
  delivered       BOOLEAN      COMMENT 'Whether the alert was actually delivered',
  delivery_channel STRING     COMMENT 'Channel used: teams/email/none'
)
USING DELTA
COMMENT 'Concentration and threshold alerts — one row per alert event'
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 90 days',
  'delta.logRetentionDuration' = 'interval 90 days'
);

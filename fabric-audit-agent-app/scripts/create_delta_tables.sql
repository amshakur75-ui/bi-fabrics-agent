-- Phase 5 Delta tables for the Fabric Audit Agent.
-- Run once in a Databricks notebook or SQL warehouse against Unity Catalog.
-- Replace ${catalog} and ${schema} with actual values (default: shakur-main.bi-fabrics-audit).
--
-- All tables: liquid clustering via CLUSTER BY (NO partition columns), 90-day retention.
-- NOTE: CREATE TABLE IF NOT EXISTS will NOT add CLUSTER BY to a table that already exists.
-- For tables created before this clustering was added, run the one-time ALTER statements at the
-- bottom of this file to enable liquid clustering on the existing table.

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
CLUSTER BY (tenant, run_at)
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
CLUSTER BY (tenant, run_at)
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
CLUSTER BY (tenant, window_ts)
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
  delivery_channel STRING     COMMENT 'Channel used: none until Phase 10 (Entra bot identity)'
)
USING DELTA
CLUSTER BY (tenant, alert_at)
COMMENT 'Concentration and threshold alerts — one row per alert event'
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 90 days',
  'delta.logRetentionDuration' = 'interval 90 days'
);

-- 5. audit_alerts — Tier-2 alert state machine (dedup + 48h reminders + escalation + resolution)
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.audit_alerts (
  incident_key          STRING   COMMENT 'Stable incident id, e.g. concentration::WS/Item or throttle::capacity',
  status                STRING   COMMENT 'active | resolved',
  severity              STRING   COMMENT 'Derived severity: info | warn',
  check_type            STRING   COMMENT 'concentration/throttle/pressure/overage',
  resource              STRING   COMMENT 'Item/workspace (concentration) or capacity',
  chat_id               STRING   COMMENT 'Pre-created Lakebase ai_chatbot conversation id (deep-link target)',
  metric                DOUBLE   COMMENT 'Primary metric value, for escalation comparison',
  first_alerted_at      STRING   COMMENT 'ISO-8601 UTC of first alert',
  last_alerted_at       STRING   COMMENT 'ISO-8601 UTC of last alert/re-alert',
  last_reminded_at      STRING   COMMENT 'ISO-8601 UTC of last 48h reminder (nullable)',
  resolved_at           STRING   COMMENT 'ISO-8601 UTC when marked resolved (nullable)',
  escalation_count      INT      COMMENT 'Number of escalation re-alerts',
  materiality_reason    STRING   COMMENT 'Why it was reported (or suppressed)',
  investigation_summary STRING   COMMENT 'Trimmed investigation text, reused for 48h reminders',
  delivered             BOOLEAN  COMMENT 'Whether the last card was delivered',
  run_at                STRING   COMMENT 'ISO-8601 UTC of the run that last touched this row',
  currently_active      BOOLEAN  COMMENT 'Is the condition firing right now (attribution stays open when False)',
  presence_count        INT      COMMENT 'Hysteresis streak: consecutive checks a pending signal has persisted'
)
USING DELTA
CLUSTER BY (incident_key)
COMMENT 'Tier-2 alert state machine — one row per incident (dedup / 48h reminders / escalation / resolution)'
TBLPROPERTIES (
  'delta.deletedFileRetentionDuration' = 'interval 90 days',
  'delta.logRetentionDuration' = 'interval 90 days'
);

-- One-time ALTER statements for tables that were CREATEd before liquid clustering was added
-- above (CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so it will not add the
-- CLUSTER BY). Run these once per pre-existing table; they are safe no-ops if the table is
-- already clustered on the same columns. Retention is idempotent to (re)set the same way.
-- ALTER TABLE ${catalog}.${schema}.run_history          CLUSTER BY (tenant, run_at);
-- ALTER TABLE ${catalog}.${schema}.audit_findings       CLUSTER BY (tenant, run_at);
-- ALTER TABLE ${catalog}.${schema}.capacity_reporting   CLUSTER BY (tenant, window_ts);
-- ALTER TABLE ${catalog}.${schema}.concentration_alerts CLUSTER BY (tenant, alert_at);
-- ALTER TABLE ${catalog}.${schema}.audit_alerts         CLUSTER BY (incident_key);

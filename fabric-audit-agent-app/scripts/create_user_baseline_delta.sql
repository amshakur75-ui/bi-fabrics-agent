-- Design A' — Phase B, task B1 — user_baseline Delta table
--
-- Nightly-rebuilt baseline (per-user + one estate-wide row) used by B2's
-- ``detect_user_baseline_deviation_precomputed`` to spot a user's own anomaly signal
-- without recomputing 14 days of history on every 5-min sweep.
--
-- Schema mirrors the (camelCase) baseline dict that ``user_baseline_bootstrap.build_baselines``
-- produces, mapped to snake_case columns by ``context_user_baseline._to_row``.
--
-- Composite key: (scope, user_id). Estate rows carry user_id = NULL and are unique per scope
-- (one estate row total). MERGE upserts use NULL-safe eq (<=>) on user_id so the estate row
-- matches itself instead of inserting a duplicate.
--
-- Run once per catalog.schema at provision time; the bootstrap job can otherwise create the
-- table lazily on first upsert (Delta autoMerge is enabled), but this SQL is the canonical
-- schema for review / manual re-provision.

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.user_baseline (
    scope           STRING   COMMENT 'user | estate — controls which layer of the 3-layer fallback',
    user_id         STRING   COMMENT 'user email/UPN when scope=user; NULL for the estate row',
    p50             DOUBLE   COMMENT '50th percentile CPU-seconds across the trailing 14 days',
    p95             DOUBLE   COMMENT '95th percentile CPU-seconds — the anomaly threshold',
    sample_count    BIGINT   COMMENT 'number of operations aggregated into this baseline',
    min_cu_seconds  DOUBLE   COMMENT 'minimum observed CPU-seconds in the sample',
    max_cu_seconds  DOUBLE   COMMENT 'maximum observed CPU-seconds in the sample',
    as_of           STRING   COMMENT 'ISO-8601 timestamp the bootstrap job stamped on this row'
)
USING DELTA
TBLPROPERTIES (
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact'   = 'true'
);

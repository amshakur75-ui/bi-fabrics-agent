-- Design A' — Phase B, task B4 — tier2_capacity_reporting Delta table
--
-- NAME: deliberately NOT `capacity_reporting`. scripts/create_delta_tables.sql already
-- provisions a table by that name at a DIFFERENT GRAIN — one row per 30-second capacity
-- window (window_ts / cu_pct / base_cu). This one is one row per 5-minute SWEEP. Appending
-- sweep-grain rows into the window-grain table fails on schema mismatch, and forcing it
-- would leave a table nobody can query. Distinct grain, distinct table.
--
-- Long-tail analytical archive of what each 5-minute tier2 sweep saw: peak CU%, throttle
-- minutes, overage state, Fabric threshold pcts, item-attribution coverage, and which
-- tier2 checks fired that run. Separate from ``tier2_readings`` (small rolling window used
-- by the stateful gates) — this table is meant for weekly / monthly trend reports and
-- post-incident review ("what did capacity look like leading up to this?").
--
-- Append-only. Rows are never mutated after write. Retention (e.g. drop rows older than
-- 90 days) is a follow-up job, not enforced by the schema.
--
-- ``signal_types`` is a compact JSON string (STRING column, e.g. ``["throttle","pressure"]``)
-- so an analyst can filter / explode with ``from_json(signal_types, 'array<string>')``
-- without needing an array column. Empty list ``"[]"`` means the sweep ran but nothing
-- fired; NULL means the row predates B4 (older sweeps).

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.tier2_capacity_reporting (
    run_at                          STRING   COMMENT 'ISO-8601 sweep run timestamp',
    capacity_id                     STRING   COMMENT 'Fabric capacity id when the collector reported one',
    peak_cu_pct                     DOUBLE   COMMENT 'Peak CU% across the sweep window',
    peak_at                         STRING   COMMENT 'ISO-8601 timestamp of the peak-CU window',
    throttle_minutes                DOUBLE   COMMENT 'Minutes throttled in this window',
    overage_total_ms                DOUBLE   COMMENT 'Overage carry-forward at end of window (ms)',
    overage_cumulative_pct          DOUBLE   COMMENT 'Cumulative overage % of base capacity',
    minutes_to_burndown             DOUBLE   COMMENT 'Estimated minutes until overage burns down',
    max_interactive_delay_pct       DOUBLE   COMMENT 'Max InteractiveDelayThresholdPercentage seen',
    max_interactive_rejection_pct   DOUBLE   COMMENT 'Max InteractiveRejectionThresholdPercentage seen',
    max_background_rejection_pct    DOUBLE   COMMENT 'Max BackgroundRejectionThresholdPercentage seen',
    item_count                      INT      COMMENT 'Items in this sweep''s attribution (WHO coverage)',
    signal_types                    STRING   COMMENT 'JSON array of tier2 check names that fired',
    collector_ok                    BOOLEAN  COMMENT 'True when the collector returned usable data'
)
USING DELTA
TBLPROPERTIES (
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact'   = 'true'
);

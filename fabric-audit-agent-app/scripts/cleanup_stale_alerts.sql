-- One-time cleanup: mark the legacy inactive-but-open alert_alerts backlog as resolved.
--
-- Root cause (see f9581cf commit message + DEPLOY-STATUS-2026-08-07.md):
-- On 2026-08-07 the daily digest surfaced "Open tickets: 161 (160 warning)" because 160 legacy
-- per-user concentration findings — created BEFORE the healthy-capacity gate + _family() checkType
-- fix landed — had their finding stop firing (currently_active=False) but never became
-- status='resolved' (no human clicked Resolve). alerts_store["query_active"] returns everything
-- WHERE status='active', so they kept flooding every render.
--
-- The code fix in f9581cf splits actively-firing from stale in the daily digest + the notification
-- center Open tab, so future stale rows stop cluttering the count. This script does the one-time
-- cleanup of the existing 160-row backlog.
--
-- SAFE: only touches rows that are BOTH already-open AND no-longer-firing AND legacy-tagged as
-- "sweep" (the check_type all the pre-fix per-user-concentration tickets got). Does NOT touch any
-- currently-firing incident, and does NOT touch rows with a genuine actionable check_type
-- (concentration / throttle / pressure / etc.). Idempotent — re-running finds 0 rows to update.
--
-- Run in a Databricks SQL editor / notebook against the audit_alerts catalog+schema:
--   default catalog: shakur-main, schema: bi-fabrics-audit (per databricks.yml var defaults;
--   adjust if you override FABRIC_DELTA_CATALOG / FABRIC_DELTA_SCHEMA).

-- 0. Preview what would change (RUN THIS FIRST):
SELECT count(*) AS to_resolve
FROM `shakur-main`.`bi-fabrics-audit`.audit_alerts
WHERE status = 'active'
  AND currently_active = false
  AND check_type = 'sweep';

-- 1. If the count looks right, run the update:
UPDATE `shakur-main`.`bi-fabrics-audit`.audit_alerts
   SET status         = 'resolved',
       resolved_at    = date_format(current_timestamp(), "yyyy-MM-dd'T'HH:mm:ss'Z'"),
       materiality_reason = coalesce(materiality_reason, '') ||
                            ' [auto-resolved 2026-08-07: legacy sweep ticket, finding no longer firing]'
 WHERE status = 'active'
   AND currently_active = false
   AND check_type = 'sweep';

-- 2. Confirm the cleanup (should return 0):
SELECT count(*) AS still_stale
FROM `shakur-main`.`bi-fabrics-audit`.audit_alerts
WHERE status = 'active' AND currently_active = false AND check_type = 'sweep';

-- 3. Sanity check remaining open tickets (should be small, all currently_active=true):
SELECT check_type, count(*) AS n,
       sum(CASE WHEN currently_active THEN 1 ELSE 0 END) AS firing_now
FROM `shakur-main`.`bi-fabrics-audit`.audit_alerts
WHERE status = 'active'
GROUP BY check_type ORDER BY n DESC;

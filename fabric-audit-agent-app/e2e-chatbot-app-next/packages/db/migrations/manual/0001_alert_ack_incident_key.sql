-- Part-7 read-path completion (alerting-redesign-and-plugin-parity design, Sub-plan 6).
--
-- ai_chatbot.alert_ticket is now keyed by incident_key with a nullable chat_id (see
-- ../../../../scripts/create_lakebase_alert_ticket.sql in the Python repo), so a finding's ticket
-- is written even when chat creation failed. The chat app's ack/resolve/reopen actions
-- (getAlertAckMap / setAlertAck / resolveAlert / reopenAlert in packages/db/src/queries.ts) still
-- key ai_chatbot.alert_ack by chat_id only, so those chat-less tickets could never be acked or
-- resolved. This adds a parallel, nullable incident_key column + unique constraint so
-- setAlertAckByIncident / resolveAlertByIncident / reopenAlertByIncident (queries.ts) can key off
-- it instead, without touching the existing chat_id-keyed path.
--
-- NOTE ai_chatbot.alert_ack is NOT a Drizzle-managed table (it has no entry in
-- packages/db/src/schema.ts — it's written directly by fabric_audit_agent's Tier-2 job / this
-- app's queries.ts via raw SQL, same as alert_ticket). `npm run db:generate` will therefore never
-- produce a migration for it; this file must be applied by hand, the same way
-- create_lakebase_alert_ticket.sql is, e.g.:
--   PGPASSWORD="$TOKEN" psql "host=$HOST user=<owner> dbname=databricks_postgres sslmode=require" \
--     -f packages/db/migrations/manual/0001_alert_ack_incident_key.sql
--
-- Idempotent / safe to re-run.

-- Add the new nullable identity column.
ALTER TABLE ai_chatbot.alert_ack ADD COLUMN IF NOT EXISTS incident_key text;

-- chat_id was originally the sole (implicit or explicit) unique key for ON CONFLICT (chat_id)
-- upserts. Chat-less acks need to insert a row with chat_id = NULL, so chat_id must allow NULLs.
-- A plain UNIQUE constraint (as opposed to a PRIMARY KEY) permits any number of NULLs while still
-- enforcing uniqueness among non-null chat_ids, and remains a valid ON CONFLICT (chat_id) arbiter.
DO $mig$
BEGIN
  -- Drop a legacy PRIMARY KEY on chat_id, if present, so chat_id can become nullable.
  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema = 'ai_chatbot' AND table_name = 'alert_ack'
      AND constraint_type = 'PRIMARY KEY' AND constraint_name = 'alert_ack_pkey'
  ) THEN
    ALTER TABLE ai_chatbot.alert_ack DROP CONSTRAINT alert_ack_pkey;
  END IF;

  ALTER TABLE ai_chatbot.alert_ack ALTER COLUMN chat_id DROP NOT NULL;

  -- Ensure a UNIQUE constraint exists on chat_id (arbiter for the existing ON CONFLICT (chat_id)
  -- upserts in queries.ts: setAlertAck / resolveAlert).
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alert_ack_chat_id_key'
  ) THEN
    ALTER TABLE ai_chatbot.alert_ack ADD CONSTRAINT alert_ack_chat_id_key UNIQUE (chat_id);
  END IF;

  -- Same for incident_key (arbiter for the new ON CONFLICT (incident_key) upserts in queries.ts:
  -- setAlertAckByIncident / resolveAlertByIncident).
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alert_ack_incident_key_key'
  ) THEN
    ALTER TABLE ai_chatbot.alert_ack ADD CONSTRAINT alert_ack_incident_key_key UNIQUE (incident_key);
  END IF;
END
$mig$;

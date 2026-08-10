-- Step 9 — Tier-2 alert ticket detail (Lakebase Postgres, ai_chatbot schema).
--
-- The Tier-2 job WRITES this table (fabric_audit_agent.adapters.chat_store_lakebase.create_ticket_writer)
-- and the chat app READS it (packages/db getAlertTicketMap) to show ticket detail — what / where /
-- since-when / currently-active — in the Alerts sidebar. This is the reverse of the alert_ack
-- boundary (app writes ack, job reads it). Lifecycle (open/investigating/resolved) is DERIVED in the
-- app from the ack map, so it is NOT stored here.
--
-- Part 7 fix (tightening.md): keyed by incident_key, NOT chat_id. When chat creation fails upstream
-- (chat_writer raises), the finding still needs a row here so it doesn't vanish from the
-- notification center — but a table keyed by chat_id can't hold a row with no chat_id. incident_key
-- (the finding/incident's own stable key, e.g. "model.bidirectional::Sales") is always present, so
-- it is the identity; chat_id is now a nullable attribute.
--
-- Run once at deploy as the Lakebase project owner (the user the tier2 job connects as), e.g.:
--   EP=projects/fabrics-audit-agent-memory/branches/production/endpoints/primary
--   HOST=$(databricks postgres get-endpoint $EP --profile fabric-test -o json | jq -r .status.hosts.host)
--   TOKEN=$(databricks postgres generate-database-credential $EP --profile fabric-test -o json | jq -r .token)
--   PGPASSWORD="$TOKEN" psql "host=$HOST user=<owner> dbname=databricks_postgres sslmode=require" \
--     -f scripts/create_lakebase_alert_ticket.sql
-- then grant the app service principal read access (replace <APP_SP_CLIENT_ID> with the app's
-- service_principal_client_id from `databricks apps get fabric-audit-agent`):
--   GRANT SELECT ON ai_chatbot.alert_ticket TO "<APP_SP_CLIENT_ID>";

CREATE TABLE IF NOT EXISTS ai_chatbot.alert_ticket (
  incident_key     text PRIMARY KEY,
  chat_id          text,
  check_type       text,
  severity         text,
  resource         text,
  workspace        text,
  detail           text,
  first_detected   text,          -- ISO-8601 UTC string, displayed verbatim by the app
  currently_active boolean,
  updated_at       timestamptz DEFAULT now()
);

-- Idempotent migration for a table created BEFORE the Part-7 fix (PK was chat_id, incident_key was
-- a plain nullable column). Safe to re-run: only acts if the legacy chat_id PK is still present.
DO $mig$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.key_column_usage
    WHERE table_schema = 'ai_chatbot' AND table_name = 'alert_ticket'
      AND constraint_name = 'alert_ticket_pkey' AND column_name = 'chat_id'
  ) THEN
    -- backfill incident_key for any legacy row that somehow lacks one, so the new PK can be set
    UPDATE ai_chatbot.alert_ticket SET incident_key = 'legacy::' || chat_id
      WHERE incident_key IS NULL;
    -- DE-DUPE before the new PK. While chat_id was the PK, every tier2 run that minted a fresh
    -- chat for the SAME incident inserted ANOTHER row, so duplicate incident_keys accumulated —
    -- and `ADD CONSTRAINT ... PRIMARY KEY (incident_key)` then fails with
    -- "could not create unique index ... Key (incident_key)=(...) is duplicated", aborting the
    -- whole migration. Keep the most recently detected row per incident (ties broken by chat_id
    -- so the choice is deterministic) and drop the rest.
    DELETE FROM ai_chatbot.alert_ticket a
     USING ai_chatbot.alert_ticket b
     WHERE a.incident_key = b.incident_key
       AND (COALESCE(a.first_detected, '') , a.chat_id)
         < (COALESCE(b.first_detected, '') , b.chat_id);
    ALTER TABLE ai_chatbot.alert_ticket DROP CONSTRAINT alert_ticket_pkey;
    ALTER TABLE ai_chatbot.alert_ticket ALTER COLUMN incident_key SET NOT NULL;
    ALTER TABLE ai_chatbot.alert_ticket ADD CONSTRAINT alert_ticket_pkey PRIMARY KEY (incident_key);
    ALTER TABLE ai_chatbot.alert_ticket ALTER COLUMN chat_id DROP NOT NULL;
  END IF;
END
$mig$;

-- App SP needs read access (it does not own this table). Uncomment + fill in at deploy:
-- GRANT SELECT ON ai_chatbot.alert_ticket TO "<APP_SP_CLIENT_ID>";

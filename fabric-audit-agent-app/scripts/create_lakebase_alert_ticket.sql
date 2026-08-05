-- Step 9 — Tier-2 alert ticket detail (Lakebase Postgres, ai_chatbot schema).
--
-- The Tier-2 job WRITES this table (fabric_audit_agent.adapters.chat_store_lakebase.create_ticket_writer)
-- and the chat app READS it (packages/db getAlertTicketMap) to show ticket detail — what / where /
-- since-when / currently-active — in the Alerts sidebar. This is the reverse of the alert_ack
-- boundary (app writes ack, job reads it). Lifecycle (open/investigating/resolved) is DERIVED in the
-- app from the ack map, so it is NOT stored here.
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
  chat_id          text PRIMARY KEY,
  incident_key     text,
  check_type       text,
  severity         text,
  resource         text,
  workspace        text,
  detail           text,
  first_detected   text,          -- ISO-8601 UTC string, displayed verbatim by the app
  currently_active boolean,
  updated_at       timestamptz DEFAULT now()
);

-- App SP needs read access (it does not own this table). Uncomment + fill in at deploy:
-- GRANT SELECT ON ai_chatbot.alert_ticket TO "<APP_SP_CLIENT_ID>";

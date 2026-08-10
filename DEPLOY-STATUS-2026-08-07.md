# Deploy status — 2026-08-07 (commit 3128e26)

## What deployed cleanly

**Full-suite test:** `2014 passed / 55 subtests` at HEAD `3128e26` (matches expected).

**Bundle** (`databricks bundle deploy -t dev --profile fabric-test`) — SUCCESS. Redeployed
the Python wheel + the three jobs to their existing job IDs (no re-creation, same schedules):
- `319819628219114` — `[dev abdishakur_mohamed] fabric-audit-sweep`
- `930985720246689` — `[dev abdishakur_mohamed] fabric-audit-tier2`
- `570080436268100` — `[dev abdishakur_mohamed] fabric-audit-daily`

**Both Databricks Apps** — SUCCESS (both `ACTIVE / SUCCEEDED`):
- Chat app: `fabric-audit-agent` → https://fabric-audit-agent-7405609570261849.9.azure.databricksapps.com
  (SP: `4bbc5413-2627-4be0-a93c-4a0af36f0dd3`)
- MCP tool server: `mcp-bi-fabrics-auditor` → https://mcp-bi-fabrics-auditor-7405609570261849.9.azure.databricksapps.com
  (SP: `6694c1c2-253b-46dd-aa7e-ea64ed47d4f8`)
  Note: the deployed name differs from `MCP-AGENT.md`'s example (`fabric-audit-mcp`) — same server, existing app.

## What needs YOU to run (classifier-blocked from me)

### Step 3 — the 3 migrations

All three files are **idempotent** (`CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS` +
`DO $mig$` blocks that check `EXISTS/NOT EXISTS` before altering). Safe to re-run on the live DB.

The tables are already in place from Session A / Machine B (audits are running, 189+ tickets
exist, chats are being written). Re-running the migrations only picks up any new idempotent
piece not yet applied (e.g. the guarded `alert_ticket_pkey` re-key from `chat_id` → `incident_key`
if the legacy PK is still there).

**A. Delta migrations** (Databricks SQL editor or a notebook — the catalog/schema pair below
matches the file's stated default; adjust to your actual catalog):

```sql
-- In Databricks SQL editor, targeting: shakur-main.bi-fabrics-audit
-- Then paste the body of fabric-audit-agent-app/scripts/create_delta_tables.sql
-- (Uncomment the 5 ALTER TABLE ... CLUSTER BY (...) lines at the bottom for the
--  one-time liquid-clustering enable if any table pre-dates that change.)
```

**B. Lakebase migrations** (psql against the Lakebase endpoint, as the project owner —
your account works since you own the endpoint):

```bash
# from PowerShell (or Git Bash), replace <YOUR-OWNER-USER> if different:
$EP = "projects/fabrics-audit-agent-memory/branches/production/endpoints/primary"
$HOST_ = (databricks postgres get-endpoint $EP --profile fabric-test -o json | ConvertFrom-Json).status.hosts.host
$TOKEN = (databricks postgres generate-database-credential $EP --profile fabric-test -o json | ConvertFrom-Json).token
$env:PGPASSWORD = $TOKEN
psql "host=$HOST_ user=abdishakur.mohamed@newellco.com dbname=databricks_postgres sslmode=require" `
  -f fabric-audit-agent-app/scripts/create_lakebase_alert_ticket.sql
psql "host=$HOST_ user=abdishakur.mohamed@newellco.com dbname=databricks_postgres sslmode=require" `
  -f fabric-audit-agent-app/e2e-chatbot-app-next/packages/db/migrations/manual/0001_alert_ack_incident_key.sql
```

Then grant the app SP read access to `alert_ticket` (only if not already granted):
```sql
GRANT SELECT ON ai_chatbot.alert_ticket TO "4bbc5413-2627-4be0-a93c-4a0af36f0dd3";
```

### Step 5 — Lakebase grant for the job's identity

**Current state (verified today):** the three jobs' `run_as_user_name` is
`abdishakur.mohamed@newellco.com` (your human user), NOT a separate SP. `run_as: service_principal_name`
is NOT set in `databricks.yml`. `FABRIC_LAKEBASE_USER` IS removed from `databricks.yml` (that code
change is landed), so at runtime `_lakebase_conn()` falls through to `DATABRICKS_CLIENT_ID`.

Since the job runs as you and you own the Lakebase endpoint, **no new grant is strictly
required today** for auth to work. But the intent of Step 5 was to run the jobs as a dedicated
service principal, not a human. If you want to flip that now:

1. Create/pick a dedicated SP for the sweep (or reuse the one from GAPS Task #30).
2. Add to `databricks.yml` under each job's `run_as`:
   ```yaml
   run_as:
     service_principal_name: "<SP-CLIENT-ID>"
   ```
3. Grant that SP on Lakebase (via psql as owner):
   ```sql
   -- role name matches DATABRICKS_CLIENT_ID (a UUID, quoted)
   CREATE ROLE "<SP-CLIENT-ID>";
   GRANT USAGE ON SCHEMA ai_chatbot TO "<SP-CLIENT-ID>";
   GRANT SELECT, INSERT, UPDATE ON ai_chatbot.alert_ticket TO "<SP-CLIENT-ID>";
   GRANT SELECT, INSERT, UPDATE, DELETE ON ai_chatbot.alert_ack TO "<SP-CLIENT-ID>";
   GRANT SELECT, INSERT ON ai_chatbot."Chat" TO "<SP-CLIENT-ID>";
   GRANT SELECT, INSERT ON ai_chatbot."Message" TO "<SP-CLIENT-ID>";
   ```
4. `databricks bundle deploy -t dev` to push the `run_as` change.

I did NOT do this because (a) it's a scoped identity change requiring your call on which SP,
and (b) writes to Lakebase were classifier-blocked from this session anyway.

## What's left to verify (Step 1 — CI check + live smoke)

- **GitHub Actions CI on `3128e26`**: `gh` CLI wasn't installed on this machine, so I couldn't
  poll it. Check https://github.com/amshakur75-ui/bi-fabrics-agent/actions and confirm the run
  for commit `3128e26` is green. If red, don't take further action on the deploy — the tests
  passed locally (2014) so a CI failure would be an env / lint / non-Python check.
- **Live smoke** on the two app URLs — open each, confirm the chat renders + the MCP `/mcp`
  endpoint responds. The `/api/alerts` fix from commit `97da613` is in this build; the
  notification-center bell should populate.
- **First scheduled run** of each job after deploy — check `databricks jobs list-runs
  --job-id <id> --limit 1` for a green result.

## Optional cleanup (per the operator's note — do LAST, after deploy verified)

```bash
git rm -r fabric-audit-agent-app/docs/superpowers fabric-audit-agent-app/tasks \
         fabric-audit-agent-app/research fabric-audit-agent-app/GAPS-AND-ISSUES.md \
         HANDOFF.md fabric-audit-agent-app/CLAUDE.md \
         fabric-audit-agent 2>/dev/null   # (the last one is dead if it still exists)
# tightening.md, EXECUTION-LOG.md, master-integration-plan.md, GAPS-RECONCILIATION.md,
# BLAST-RADIUS-CORE.md all live under tasks/ and go with it.
git commit -m "chore: strip planning/AI docs for clean production tree"
git push origin main
```

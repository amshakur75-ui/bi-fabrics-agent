# Chat History via Lakebase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is an **infrastructure-enablement** plan — most tasks end in a live verification (CLI/HTTP/manual E2E), not a pytest cycle.

**Goal:** Turn on persistent chat history for the deployed `fabric-audit-agent` Databricks App by attaching a dedicated Lakebase Postgres instance, so conversations save, the sidebar lists them, and `/chat/:id` reloads full history.

**Architecture:** Provision a new dedicated Lakebase Autoscaling project (`fabrics-audit-agent-memory`, scale-to-zero), attach it to the existing app as a `postgres` resource (OAuth auth via the app SP), ensure the `ai_chatbot` schema is created **owned by the app SP**, and redeploy. The chat template already implements all persistence/UI — this only supplies + wires the database.

**Tech Stack:** Databricks Lakebase (Postgres Autoscaling, `databricks postgres` CLI), Databricks Apps, Drizzle ORM migrations (`packages/db`), the existing Vercel-AI-SDK chat template.

## Global Constraints

- Profile: **always `--profile fabric-test`** on every Databricks CLI call. Never auto-select another.
- App name: `fabric-audit-agent`. Deployed from workspace path `/Workspace/Users/abdishakur.mohamed@newellco.com/bi-fabrics-agent/fabric-audit-agent-app` (synced from git via `databricks sync`).
- Lakebase project id: `fabrics-audit-agent-memory` (RFC 1123: lowercase/digits/hyphen). Display name: `Fabrics Audit Agent Memory`.
- Postgres schema owned by the app: `ai_chatbot` (hardcoded in `packages/db/src/connection-core.ts`).
- `databricks apps create-update` with `update_mask=resources` REPLACES the resources array — always read current resources and merge.
- Windows shell note: prefix `/Workspace…` and `/Volumes…` CLI paths with `MSYS_NO_PATHCONV=1` (git-bash mangles leading-slash paths).
- **HARD GATE:** Do not run Task 2 (provisioning = first spend) until the user approves the cost estimate in Task 1.
- No changes to agent logic, MCP server, or Delta memory. Frontend code change is limited to (at most) the schema-ownership migration hook in Task 4.

---

### Task 1: Cost analysis + confirmation gate (NO SPEND YET)

**Files:** none (analysis only).

**Interfaces:**
- Produces: a go/no-go decision + the min/max CU + suspend settings to use in Task 2.

- [ ] **Step 1: Fetch current Lakebase pricing**

Get authoritative numbers (do not guess): fetch the Databricks Lakebase / "Database" pricing for Azure and the workspace region. Use WebFetch on `https://www.databricks.com/product/pricing/lakebase` (and the Azure Databricks pricing page if needed). Record: compute $/CU-hour, storage $/GB-month, and confirm scale-to-zero bills 0 compute when suspended.

- [ ] **Step 2: Compute the estimate for THIS workload**

Chat history for one low-traffic internal app: a few MB of rows; compute active only while someone is actively chatting, then scale-to-zero after 5 min idle. Present three lines:
- Idle/month (storage only, ~tens of MB): ~$0.x
- Light use (e.g. 1 CU active ~2 h/day, 20 workdays): CU_rate × 40 h
- Heaviest plausible (1 CU active 8 h/day × 30 d): CU_rate × 240 h
Note scale-to-zero means the realistic figure is near the idle line.

- [ ] **Step 3: Present to the user and WAIT for approval**

Post the numbers + the exact instance config to be created (1 CU min/max, scale-to-zero 5 min, single primary endpoint, PG 17). Explicitly ask: "Approve creating this? (this is the first new spend)." **Do not proceed to Task 2 without a yes.**

- [ ] **Step 4: Record the decision**

Once approved, note the chosen min/max CU (default 1/1) and suspend timeout for Task 2. If the user wants tighter cost control, set `autoscaling_limit_min_cu` lower / confirm suspend timeout.

---

### Task 2: Provision the Lakebase project

**Files:** none (creates cloud resources).

**Interfaces:**
- Consumes: user approval from Task 1.
- Produces: `PROJECT_ID=fabrics-audit-agent-memory`, plus resolved `BRANCH` (`projects/fabrics-audit-agent-memory/branches/production`), `ENDPOINT` path, `DATABASE` path, and endpoint `HOST` — used by Tasks 3–5.

- [ ] **Step 1: Create the project**

```bash
databricks postgres create-project fabrics-audit-agent-memory \
  --json '{"spec": {"display_name": "Fabrics Audit Agent Memory"}}' \
  --profile fabric-test
```
Auto-creates the `production` branch + `primary` read-write endpoint (scale-to-zero). CLI waits for completion.

- [ ] **Step 2: Capture the resource paths**

```bash
databricks postgres list-branches   projects/fabrics-audit-agent-memory --profile fabric-test
databricks postgres list-endpoints  projects/fabrics-audit-agent-memory/branches/production --profile fabric-test
databricks postgres list-databases  projects/fabrics-audit-agent-memory/branches/production --profile fabric-test
```
Record: `BRANCH = projects/fabrics-audit-agent-memory/branches/production`; `ENDPOINT = .../endpoints/<endpoint_id>`; `DATABASE = .../databases/<database_id>` (PG db name often `databricks-postgres` / `databricks_postgres` — note both the resource-path id and `status.postgres_database`); `HOST = status.hosts.host` from get-endpoint.

- [ ] **Step 3: Verify READY**

Confirm the branch state is `READY` and the endpoint exists. Expected: one read-write endpoint, database present.

---

### Task 3: Attach the Lakebase DB to the app (merge resources)

**Files:**
- Create (scratch): `<scratchdir>/app-update.json`

**Interfaces:**
- Consumes: `PROJECT_ID`, `BRANCH`, `DATABASE` from Task 2.
- Produces: the app carrying a `postgres` resource named `database`, injecting PG connection env into the container.

- [ ] **Step 1: Read the app's CURRENT resources (to merge, not clobber)**

```bash
databricks apps get fabric-audit-agent --profile fabric-test -o json > <scratchdir>/app-before.json
```
Extract the existing `resources` array (volume, volume-2, secrets, mcp-bi-fabrics-auditor, etc.). Also capture `service_principal_client_id` (needed if Task 4 uses grants).

- [ ] **Step 2: Build the merged update payload**

Write `<scratchdir>/app-update.json` = `{"update_mask":"resources","app":{"resources":[<ALL existing resources>, <new postgres resource>]}}` where the new entry is:
```json
{
  "name": "database",
  "postgres": {
    "branch": "projects/fabrics-audit-agent-memory/branches/production",
    "database": "projects/fabrics-audit-agent-memory/branches/production/databases/<DATABASE_ID>",
    "permission": "CAN_CONNECT_AND_CREATE"
  }
}
```

- [ ] **Step 3: Apply**

```bash
databricks apps create-update fabric-audit-agent --json @<scratchdir>/app-update.json --profile fabric-test
```

- [ ] **Step 4: Verify the binding + discover injected env var names**

```bash
databricks apps get fabric-audit-agent --profile fabric-test -o json | grep -iE "postgres|database|PGHOST|PGDATABASE|PGUSER|POSTGRES_URL"
```
Expected: the `database`/`postgres` resource present AND all prior resources still present. Note which PG env vars Databricks injects (the template's `connection.ts` accepts `POSTGRES_URL` OR `PGHOST`+`PGDATABASE`+`PGUSER`+`PGPORT`). If the binding does not inject a usable set, add the missing `PG*` mapping in the app `app.yaml` env (workspace-managed) referencing the resource.

---

### Task 4: Ensure `ai_chatbot` is created AND owned by the app SP

**Decision:** the app SP must own the schema (skill: "deploy first so the SP creates and owns the schema"), because the app connects as the SP. The template has **no boot-time migration**. Primary approach **4A (boot-time migrate)**; fallback **4B (owner-migrate + grant)** if boot-migrate is impractical.

**Files:**
- Inspect: `e2e-chatbot-app-next/packages/db/package.json`, `e2e-chatbot-app-next/packages/db/src/connection.ts`, `e2e-chatbot-app-next/scripts/` or `server/src/index.ts`, `scripts/start_app.py`
- Modify (4A): the app start sequence (`scripts/start_app.py` or the node server entry) to run migrations once on boot.

**Interfaces:**
- Consumes: the attached DB (Task 3), `service_principal_client_id` (Task 3 Step 1).
- Produces: `ai_chatbot.{Chat,Message,Vote}` existing and writable by the app SP.

- [ ] **Step 1: Discover the template's migration entry point**

Inspect `packages/db` for a migrate script (`drizzle-kit migrate`, a `migrate()` call using `drizzle-orm/postgres-js/migrator`, or a `db:migrate` npm script) and whether `start-app`/the node server calls it. Record the exact command the app could run at boot.

- [ ] **Step 2 (4A): Add an idempotent boot-time migration, guarded by DB availability**

In the app start path, before the node server begins serving, run the migration (Drizzle records applied migrations in its own tracking table, so this is idempotent across restarts). It must run only when a DB is configured (mirror `isDatabaseAvailable()`), so ephemeral deploys are unaffected. Keep the change minimal and follow the template's existing process-spawn style in `start_app.py`.

- [ ] **Step 3: (fallback 4B) If boot-migrate is impractical**

Apply the migration SQL as the project owner, then grant the SP. Get an endpoint credential (`databricks postgres generate-database-credential <ENDPOINT>`), apply `packages/db/migrations/*.sql` via `psql`/`databricks psql --project fabrics-audit-agent-memory`, then:
```sql
GRANT USAGE, CREATE ON SCHEMA ai_chatbot TO "<SP_CLIENT_ID>";
GRANT ALL ON ALL TABLES IN SCHEMA ai_chatbot TO "<SP_CLIENT_ID>";
ALTER DEFAULT PRIVILEGES IN SCHEMA ai_chatbot GRANT ALL ON TABLES TO "<SP_CLIENT_ID>";
```
(Trade-off: schema owned by a human role, not the SP — acceptable with explicit default-privilege grants; 4A is cleaner long-term.)

- [ ] **Step 4: Commit any code change**

If 4A added a boot-migrate step, commit it: `git add … && git commit -m "feat(app): run Lakebase migrations on boot so the SP owns ai_chatbot"`. (No commit for 4B.)

---

### Task 5: Deploy the app and create the schema

**Files:** none (deploy).

- [ ] **Step 1: Sync latest source to the workspace (if any code changed in Task 4)**

```bash
MSYS_NO_PATHCONV=1 databricks sync . /Workspace/Users/abdishakur.mohamed@newellco.com/bi-fabrics-agent --full --profile fabric-test
```

- [ ] **Step 2: Deploy**

```bash
MSYS_NO_PATHCONV=1 databricks apps deploy fabric-audit-agent \
  --source-code-path /Workspace/Users/abdishakur.mohamed@newellco.com/bi-fabrics-agent/fabric-audit-agent-app \
  --profile fabric-test
```
Expected: deployment `SUCCEEDED`, app `RUNNING`. On boot (4A) the SP runs migrations → `ai_chatbot` created and owned by the SP.

- [ ] **Step 3: Check logs for DB connect + migration success**

```bash
MSYS_NO_PATHCONV=1 databricks apps logs fabric-audit-agent --profile fabric-test 2>&1 | grep -iE "postgres|migrat|ai_chatbot|isDatabaseAvailable|error|permission denied"
```
Expected: DB connects; migrations applied or already-applied; no `permission denied`.

---

### Task 6: Verify end-to-end

**Files:** none (manual + SQL verification).

- [ ] **Step 1: Confirm schema + tables exist**

Using an endpoint credential (`generate-database-credential` on `ENDPOINT`) + `psql`, or `databricks psql --project fabrics-audit-agent-memory`:
```sql
\dt ai_chatbot.*
```
Expected: `Chat`, `Message`, `Vote` present.

- [ ] **Step 2: Live chat persistence**

Open the app URL. Confirm **no "Chat history is disabled" banner** (sidebar is active). Send "What's the current capacity health?" → get a reply.

- [ ] **Step 3: Refresh persists**

Reload the tab. Expected: the same conversation is still shown (not a blank new chat). The sidebar lists it under Today.

- [ ] **Step 4: Deep-link reload**

Copy the `/chat/<id>` URL, open in a new tab. Expected: the full conversation history rehydrates.

- [ ] **Step 5: Confirm rows landed**

```sql
SELECT count(*) FROM ai_chatbot."Chat"; SELECT count(*) FROM ai_chatbot."Message";
```
Expected: ≥1 chat and ≥2 messages (user + assistant).

- [ ] **Step 6: Report + close sub-project #1**

Summarize to the user: history live, cost as approved, verification results. Then move to designing sub-project #2 (Tier-2 → Teams alerts) with the captured requirements (alert-once + 48h reminders, escalate/reset, Delta alerts table, deep-link to a saved conversation).

---

## Self-Review

- **Spec coverage:** dedicated Lakebase (T2) ✓; bind to existing app (T3) ✓; migrations create `ai_chatbot` owned by SP (T4) ✓; redeploy + `isDatabaseAvailable()` true (T5) ✓; E2E persist + sidebar + `/chat/:id` (T6) ✓; cost/lifecycle confirmation (T1, added per user request) ✓; risks (SP grants, PG env format, cold-start) addressed in T3/T4/T6.
- **Placeholder scan:** the only deferred specifics are genuine discovery steps (injected PG env var names in T3.4; migration entry point in T4.1) that must be read from the live app/template — each has an explicit command + expected result. No vague "add error handling" steps.
- **Consistency:** `fabrics-audit-agent-memory`, `ai_chatbot`, `fabric-audit-agent`, `--profile fabric-test`, and the workspace source path are used identically throughout.

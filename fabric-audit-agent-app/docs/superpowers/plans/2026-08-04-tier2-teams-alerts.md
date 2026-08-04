# Tier-2 → Teams Alerts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Python logic tasks are TDD (pytest); infra/frontend tasks end in a live/manual verification.

**Goal:** When the 5-min Tier-2 job finds something worth reporting, investigate it, post a Teams Adaptive Card (facts + investigation + deep-link), and pre-create a saved public chat conversation the team can open and continue — deduped, with 48h reminders, escalation re-alerts, and a resolved card.

**Architecture:** Deterministic gate → deterministic dedup/materiality (no LLM) → investigate once (LLM, only when alerting) → deliver via `outbound.py`/`egress.py` webhook sink → write a `Chat`+`Message` into Lakebase `ai_chatbot` → embed `/chat/<id>` in the card. State lives in a new Delta `audit_alerts` table. A shared "Alerts" sidebar view lists the conversations.

**Tech Stack:** Python (`fabric_audit_agent`), Delta (Unity Catalog), Lakebase Postgres (psycopg + databricks-sdk `postgres`), Power Automate/Teams Adaptive Cards, the e2e chat template (Express/React/Drizzle).

## Global Constraints

- Profile: **always `--profile fabric-test`**. Windows CLI paths under `/Workspace`,`/Volumes`: prefix `MSYS_NO_PATHCONV=1`.
- Delta catalog/schema: `shakur-main.bi-fabrics-audit`. Lakebase project: `fabrics-audit-agent-memory`, schema `ai_chatbot`, tables `Chat`/`Message`/`Vote` (owned by app SP `4bbc5413-2627-4be0-a93c-4a0af36f0dd3`).
- Delivery **must** pass `apply_egress_controls(..., sink="alert")` (egress strips secrets; user-attribution intentionally preserved). Webhook URL only from secret `POWER_AUTOMATE_ALERT_URL` (scope `fabric-audit`); never logged/committed.
- Whole path flag-gated: `TIER2_WEBHOOK_ENABLED` (default off). Tier-2 only; the hourly sweep stays silent.
- Data-dict keys stay camelCase (project convention); Python identifiers snake_case; JSON `ensure_ascii=False`, `separators=(",",":")`.
- **Materiality thresholds (approved defaults, all env-overridable via `FABRIC_TIER2_*`):** REPORT if `severity=="warn"` OR `recurrence.isRecurring` OR concentration `sharePct>=40` OR `throttleMinutes>=5` OR pressure `peakCuPct>=120` OR overage `minutesToBurndown<60`. SUPPRESS if `severity=="info"` AND background-dominated AND not recurring AND `normalityHint` normal. ESCALATION if severity rose OR `peakCuPct`+20 OR `throttleMinutes` doubled(&>=5) OR `sharePct`+15. Reminder cadence 48h.
- **No LLM before the alert decision.** Investigation runs only for new/escalation/ambiguous; reminders reuse the stored investigation.

## File Structure

- Create `fabric_audit_agent/automation/incident.py` — `incident_key(trigger)`, `severity_of(trigger)` (pure).
- Create `fabric_audit_agent/automation/materiality.py` — `classify(trigger, prior)` → `"report"|"suppress"|"ambiguous"`, `is_escalation(trigger, prior)` (pure).
- Create `fabric_audit_agent/context_alerts.py` — Delta `audit_alerts` store `{query_active, upsert_alert, mark_resolved}` (mirror `context_findings.py`).
- Create `fabric_audit_agent/adapters/chat_store_lakebase.py` — `create_alert_chat(title, markdown) -> chat_id` (Lakebase insert).
- Create `fabric_audit_agent/adapters/delivery_webhook.py` — `build_card(kind, alert)`, `create_webhook_sink(url) -> {"deliver": fn}`.
- Modify `fabric_audit_agent/outbound.py` — add `tier2_alert` allowlist entry.
- Modify `fabric_audit_agent/automation/tier2_check.py` — orchestrate the flow.
- Modify `fabric_audit_agent/job.py` (`run_tier2_job`) — build reasoner + sinks + chat store, gate on `TIER2_WEBHOOK_ENABLED`.
- Modify `scripts/create_delta_tables.sql` — `audit_alerts` DDL.
- Modify `fabric-audit-agent-app/databricks.yml` — tier2 job `run_as` SP + deps (`databricks-sdk`, `psycopg[binary]`, `requests`).
- Create `e2e-chatbot-app-next/server/src/routes/alerts.ts` + register; create `client/src/components/sidebar-alerts.tsx` + wire into the sidebar.
- Tests under `tests/`: `test_incident_key.py`, `test_materiality.py`, `test_alerts_store.py`, `test_delivery_webhook.py`, `test_chat_store_lakebase.py`, `test_tier2_alert_flow.py`.

---

### Task 1: Dedicated service principal + Lakebase grant + job run_as

**Files:** Modify `fabric-audit-agent-app/databricks.yml` (job `run_as`).

**Interfaces:** Produces `TIER2_SP_APP_ID` (client id) used by the job run identity; the SP holds `USAGE`+`INSERT` on `ai_chatbot`.

- [ ] **Step 1: Discover or create the SP**
Run `databricks service-principals list --profile fabric-test -o json`. If a suitable existing SP (e.g. a prior `fabric-audit` job SP) exists, reuse it; else create:
```bash
databricks service-principals create --display-name "fabric-audit-tier2-job" --profile fabric-test -o json
```
Record `applicationId` as `TIER2_SP_APP_ID`.
- [ ] **Step 2: Grant the SP Lakebase access** (run as project owner; the SP's PG role is auto-created on first identity use, but grant explicitly). Using an endpoint credential + a Postgres client (psycopg, as in sub-project #1 verification):
```sql
GRANT USAGE ON SCHEMA ai_chatbot TO "<TIER2_SP_APP_ID>";
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA ai_chatbot TO "<TIER2_SP_APP_ID>";
ALTER DEFAULT PRIVILEGES IN SCHEMA ai_chatbot GRANT SELECT, INSERT ON TABLES TO "<TIER2_SP_APP_ID>";
```
(If the role doesn't exist yet: `CREATE ROLE "<TIER2_SP_APP_ID>" WITH LOGIN;` first — verify with the databricks-lakebase skill's role commands.)
- [ ] **Step 3: Set the tier-2 job to run as the SP** — in `databricks.yml`, add under the `fabric_audit_tier2` job (or the target): `run_as: {service_principal_name: <TIER2_SP_APP_ID>}`. Grant the SP `CAN_USE` on the serverless environment + access to the `fabric-audit` secret scope and the Delta schema (job needs both). 
- [ ] **Step 4: Verify** the SP can connect + insert: a one-off psycopg script authenticating as the SP (via its own credential) does `INSERT ... RETURNING` into a temp row then rolls back. Expected: success (no `permission denied`).

---

### Task 2: `audit_alerts` Delta table + store

**Files:** Modify `scripts/create_delta_tables.sql`; Create `fabric_audit_agent/context_alerts.py`; Test `tests/test_alerts_store.py`.

**Interfaces:** Produces `create_alerts_store_delta(catalog, schema) -> {"query_active": fn, "upsert": fn, "resolve": fn}` and a pure `from_row`/`to_row` mapping. `query_active() -> {incident_key: alert_dict}`; `upsert(alert_dict)`; `resolve(incident_key, at)`.

- [ ] **Step 1: DDL** — add to `scripts/create_delta_tables.sql`:
```sql
CREATE TABLE IF NOT EXISTS `shakur-main`.`bi-fabrics-audit`.audit_alerts (
  incident_key STRING, status STRING, severity STRING, check_type STRING, resource STRING,
  chat_id STRING, first_alerted_at TIMESTAMP, last_alerted_at TIMESTAMP, last_reminded_at TIMESTAMP,
  resolved_at TIMESTAMP, escalation_count INT, materiality_reason STRING, delivered BOOLEAN, run_at TIMESTAMP
) USING DELTA CLUSTER BY (incident_key) TBLPROPERTIES (delta.deletedFileRetentionDuration = '90 days');
```
- [ ] **Step 2: Write the failing test** (`test_alerts_store.py`) using an in-memory fake store (inject a dict-backed store like the other tests do), asserting `upsert` then `query_active` round-trips an alert and `resolve` flips status. (Mirror the fake-store pattern in `test_job_alerting.py`/`context_findings` tests.)
- [ ] **Step 3: Implement `context_alerts.py`** mirroring `context_findings.create_findings_store_delta` (camelCase↔snake row mapping via `_to_row`/`_from_row`; Spark upsert via MERGE on `incident_key`; `query_active` filters `status='active'`; `resolve` MERGE-sets status/resolved_at). Provide a pure `_to_row`/`_from_row` unit-tested without Spark.
- [ ] **Step 4: Run tests** `pytest tests/test_alerts_store.py -q` → PASS. **Step 5: Commit.**

---

### Task 3: `incident_key`, `severity`, materiality + escalation (pure, TDD)

**Files:** Create `automation/incident.py`, `automation/materiality.py`; Tests `test_incident_key.py`, `test_materiality.py`.

**Interfaces:** `incident_key(trigger:dict)->str`; `severity_of(trigger)->"info"|"warn"`; `classify(trigger, prior:dict|None, cfg)->("report"|"suppress"|"ambiguous", reason)`; `is_escalation(trigger, prior, cfg)->bool`.

- [ ] **Step 1: Failing tests for `incident_key`** — concentration→`concentration::{workspace}/{item}`, throttle/pressure/overage→`{check}::{capacityId}`; stable across dict key order.
```python
def test_incident_key_concentration():
    t={"check":"concentration","workspace":"WS","item":"Model A"}
    assert incident_key(t)=="concentration::WS/Model A"
def test_incident_key_capacity_scoped():
    t={"check":"throttle","capacityId":"cap-1"}
    assert incident_key(t)=="throttle::cap-1"
```
- [ ] **Step 2: Implement `incident.py`** (dict lookups; default `capacityId` to `"capacity"` if absent). Run → PASS.
- [ ] **Step 3: Failing tests for `classify`/`is_escalation`** covering each default rule (warn→report; recurring→report; sharePct 45→report, 32→ambiguous; info+background+normal+not-recurring→suppress; escalation on severity rise and on peakCuPct+20). Use the approved default cfg.
- [ ] **Step 4: Implement `materiality.py`** with the constraint thresholds read from `cfg` (defaults from env `FABRIC_TIER2_*`). Pure, no I/O. Run → PASS. **Step 5: Commit.**

---

### Task 4: Lakebase chat writer

**Files:** Create `adapters/chat_store_lakebase.py`; Test `tests/test_chat_store_lakebase.py` (unit-test the SQL/row builder with a fake cursor; live insert covered in Task 8).

**Interfaces:** `create_alert_chat(markdown:str, title:str, *, conn_factory=None, user_id="fabric-audit-agent") -> chat_id:str`. Builds `Chat` + assistant `Message` rows matching the Drizzle schema and inserts them in one transaction; returns the `Chat.id` uuid.

- [ ] **Step 1: Failing test** — inject a fake connection/cursor; assert two INSERTs (Chat then Message), that `Chat` gets `visibility='public'`, `userId='fabric-audit-agent'`, and `Message.parts` = `json([{ "type":"text","text":markdown }])`, `role='assistant'`, and the returned id matches the Chat id.
- [ ] **Step 2: Implement** — `conn_factory` default generates a Lakebase credential via databricks-sdk (`WorkspaceClient().postgres...` or `generate-database-credential`) and connects with psycopg to host `ep-shy-bird-e1gcy0mq...eastus2`, db `databricks_postgres`, sslmode require. Use `uuid.uuid4()` ids, `now()` timestamps, `json.dumps(parts, ensure_ascii=False)`. Column names/casing exactly per `packages/db/src/schema.ts` (`"Chat"`,`"Message"`, quoted identifiers). Run → PASS. **Step 3: Commit.**

---

### Task 5: Webhook delivery adapter + outbound entry

**Files:** Create `adapters/delivery_webhook.py`; Modify `outbound.py`; Test `test_delivery_webhook.py`.

**Interfaces:** `build_card(kind:"new"|"reminder"|"resolved", alert:dict, chat_url:str)->dict` (Adaptive Card); `create_webhook_sink(url:str, poster=None)->{"deliver": fn}` where `deliver(safe_payload)->{"delivered":bool,"status":int}` POSTs `{"attachments":[card]}`.

- [ ] **Step 1: Failing tests** — `build_card("new", alert, url)` returns `contentType application/vnd.microsoft.card.adaptive`, a FactSet with the expected facts, and an `Action.OpenUrl` targeting `chat_url`; `build_card("resolved", ...)` is the one-liner; `create_webhook_sink` with a fake poster returns `delivered=True` on 202 and posts UTF-8 `{"attachments":[...]}`.
- [ ] **Step 2: Implement** — card builder (v1.4, severity emoji title, FactSet, investigation summary TextBlock, OpenUrl); `create_webhook_sink` posts via `poster` (default: `requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type":"application/json; charset=utf-8"})`). Never log the URL.
- [ ] **Step 3: outbound.py** — add `"tier2_alert": {"enabled": True, "sink": "webhook"}` to `_ALLOWLIST` (dispatch still gates on the `sinks` dict being present, which `job.py` only provides when the flag is on). Run tests → PASS. **Step 4: Commit.**

---

### Task 6: Tier-2 orchestration + job wiring (the flow)

**Files:** Modify `automation/tier2_check.py`, `job.py`; Test `test_tier2_alert_flow.py`.

**Interfaces:** `run_tier2_check(..., alerts_store, reasoner, chat_writer, delivery_sinks, cfg)` implements the ordered flow; returns `{"triggered","triggers","delivered","checkedAt"}` with `delivered` now populated.

- [ ] **Step 1: Failing integration test** (injected fakes: fake alerts_store dict, fake reasoner counting calls, fake chat_writer returning `"chat-1"`, fake webhook sink recording payloads). Assert, across simulated runs:
  - new reportable trigger → **1** reasoner call, **1** chat write, **1** "new" card, alert row `active`.
  - same trigger next run (<48h, not escalated) → **0** reasoner, **0** cards (silent).
  - +49h later, still active → **0** reasoner, **1** "reminder" card (reuses stored investigation).
  - escalation (severity rose) → **1** reasoner, **1** card, `escalation_count=1`.
  - trigger gone → **1** "resolved" card, row `resolved`.
  - clear-suppress trigger (info+normal) → **0** reasoner, **0** cards.
- [ ] **Step 2: Implement the flow** in `tier2_check.run_tier2_check` exactly per the spec ordering (dedup → backstop → investigate-once → deliver; reminders/resolve deterministic). Store the investigation markdown on the alert for reminder reuse (or re-read the chat's first message). Use `dispatch_outbound("tier2_alert", payload, sinks=delivery_sinks)` for every send (egress applied inside).
- [ ] **Step 3: Wire `job.run_tier2_job`** — when `TIER2_WEBHOOK_ENABLED` and `POWER_AUTOMATE_ALERT_URL` present: build `reasoner=_default_reasoner(env,...)`, `chat_writer=create_alert_chat`, `alerts_store=create_alerts_store_delta(...)`, `delivery_sinks={"webhook": create_webhook_sink(url)}`; else pass empties (current no-op behavior preserved). Pass `APP_URL` for the deep-link base.
- [ ] **Step 4: Run** `pytest tests/test_tier2_alert_flow.py -q` → PASS, and the full suite `pytest tests/ -q` stays green. **Step 5: Commit.**

---

### Task 7: Shared "Alerts" sidebar view (frontend)

**Files:** Create `e2e-chatbot-app-next/server/src/routes/alerts.ts` (+ register in `server/src/index.ts`); Create `client/src/components/sidebar-alerts.tsx` (+ mount in the sidebar).

- [ ] **Step 1: Backend route** — `GET /api/alerts` → `getChatsByUserId('fabric-audit-agent')` (reuse the existing query; public visibility means no per-user auth filter), newest first; returns `[]` when DB unavailable. Register with the existing auth middleware.
- [ ] **Step 2: Frontend** — a collapsible "Alerts" section above personal history in the sidebar, SWR-fetching `/api/alerts`, each row links to `/chat/<id>` (reuse the history-item component). Update `tests/smoke.spec.ts` selectors if needed.
- [ ] **Step 3: Build check** — `cd e2e-chatbot-app-next && npm run build` succeeds (typecheck/lint pass). **Step 4: Commit.**

---

### Task 8: Deploy + secret + flag + E2E verify

- [ ] **Step 1: Secret** — `databricks secrets put-secret fabric-audit POWER_AUTOMATE_ALERT_URL --profile fabric-test` (paste the Power Automate URL; the user supplies it — do not commit it).
- [ ] **Step 2: Create the Delta table** — run the `audit_alerts` DDL (via `databricks sql`/a notebook) in `shakur-main.bi-fabrics-audit`.
- [ ] **Step 3: Set env** on the tier-2 job (databricks.yml `named_parameters`/env): `TIER2_WEBHOOK_ENABLED=true`, `APP_URL=https://fabric-audit-agent-7405609570261849.9.azure.databricksapps.com`, the Claude endpoint, and the Lakebase host/db for the writer. Add deps (`databricks-sdk>=0.81`, `psycopg[binary]`, `requests`) to the tier2 job environment.
- [ ] **Step 4: Deploy** — build wheel, `databricks bundle deploy -t dev --profile fabric-test`; confirm the job updated with the SP run_as.
- [ ] **Step 5: Controlled live test** — trigger a run (`databricks bundle run fabric_audit_tier2 -t dev` or a forced test trigger). Verify: a card lands in Teams; clicking opens `/chat/<id>` with the investigation; the conversation appears in the app's Alerts view; `audit_alerts` has one `active` row. Run again immediately → no duplicate card (silent). 
- [ ] **Step 6: Report + hardening note** — summarize; log the "job runs as dedicated SP" as done, and note any deferred tuning of thresholds after observing volume.

---

## Self-Review

- **Spec coverage:** two-stage dedup-before-LLM flow (T6) ✓; materiality backstop + escalation (T3) ✓; `audit_alerts` state machine + 48h/resolve (T2,T6) ✓; pre-created public chat writer (T4) ✓; webhook Adaptive Card via outbound/egress + flag/secret (T5,T8) ✓; dedicated SP + grant + run_as (T1) ✓; Alerts sidebar (T7) ✓; thresholds = approved defaults, env-tunable (Global Constraints) ✓; testing (per-task pytest + T8 E2E) ✓.
- **Placeholder scan:** thresholds are concrete (approved defaults); the only discover-then-use steps (T1 SP id, T8 secret value) are genuine external inputs with explicit commands.
- **Consistency:** `incident_key`, `audit_alerts`, `fabric-audit-agent` system user, `TIER2_WEBHOOK_ENABLED`, `POWER_AUTOMATE_ALERT_URL`, `ai_chatbot` used identically across tasks; `create_alert_chat`/`create_webhook_sink`/`create_alerts_store_delta`/`classify`/`incident_key` signatures match their consumers in T6.

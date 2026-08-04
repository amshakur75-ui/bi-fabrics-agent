# Design — Tier-2 → Teams Alerts with Investigation + Deep-link

- **Date:** 2026-08-04
- **Status:** Approved (design) — pending spec review
- **Sub-project:** #2 of 3 (history ✅ → **Tier-2 Teams alerts** → chart fixes)
- **Depends on:** sub-project #1 (chat history / Lakebase `ai_chatbot`) — now live. The alert deep-link opens a **saved** conversation, which requires that DB.

---

## Context / Problem

The deployed `fabric-audit-tier2` Databricks Job (`automation/tier2_check.py`, every 5 min) runs deterministic gates (concentration / throttle / pressure / overage) with recurrence enrichment, but **delivery is a hardcoded no-op** (`delivered = {}`). The Power Automate → Teams webhook path is proven working (Adaptive Card via `{"attachments":[<card>]}`). This sub-project turns Tier-2 into a real alerter: when something is genuinely worth reporting, it investigates, posts a Teams card with the facts + investigation, and links to a **saved chat conversation** the team can open and continue.

## Goals / Success Criteria

1. When a gate fires and the condition is **worth reporting**, a Teams Adaptive Card is delivered with: severity, the trigger facts (throttle mins / peak CU% / User→Item→Owner / recurrence), a short investigation summary, and an **"Investigate in chat"** deep-link.
2. Clicking the link opens a **pre-created, saved** conversation (`/chat/<id>`) containing the full investigation as the first assistant message; the user continues by typing.
3. Those alert conversations also appear in a shared **"Alerts"** view in the app sidebar (visible to all app users).
4. **Anti-spam:** alert once per incident; a reminder every 48h while still active; re-alert on escalation; a one-line **"resolved"** card when it clears.
5. **Cost-bounded:** the LLM is invoked **only when actually alerting** (new incident / escalation); deduped/silent runs and reminders make **zero** LLM calls.
6. All delivery routes through the existing `outbound.py` allowlist + `egress.py` chokepoint; the webhook URL lives in a secret; the whole path is flag-gated to Tier-2.

**Non-goals:** the hourly Tier-1 sweep (stays webhook-silent); the full Phase-10 Entra bot / two-way Teams (this is the interim one-way card + deep-link-to-chat continuation); redacting user attribution (explicitly **not** redacted — see Security).

## Architecture — the two-stage flow (cost-correct ordering)

Everything up to the alert decision is **deterministic**; the LLM runs only when we're about to send. Per 5-min run, for each fired gate:

1. **Gate fires** (existing deterministic check).
2. **Compute `incident_key`** and look up `audit_alerts` (Delta). Deterministic dedup/escalation:
   - active + <48h since last alert/reminder + **not escalated** → **silent, return (no LLM).**
3. **Deterministic materiality backstop:**
   - clear-suppress (`info` + background-dominated + not-recurring + `normalityHint`=normal) → **silent, return (no LLM).**
   - clear-report (`warn`; or recurring; or overage with `minutesToBurndown` < threshold; or concentration `sharePct` ≥ high band) → mark report=true, go to 4.
   - otherwise **ambiguous** → go to 4 (LLM decides).
4. **Investigate once (LLM).** Maps trigger→investigation (throttle→`run_diagnosis`/`investigate_capacity_spike`; concentration→`investigate_user`/spike; pressure/overage→spike). Returns the narrative **and**, for ambiguous cases, a `report` verdict + reason. If verdict is suppress → **silent** (record reason), return.
5. **Deliver.** New incident or escalation → build the Adaptive Card + pre-create the saved chat + POST via the chokepoint; upsert `audit_alerts` row (`active`, timestamps, `chat_id`).
6. **Reminders & resolution (deterministic, no LLM):**
   - active + **>48h** since last alert/reminder → send a **reminder** card that **reuses the stored investigation** (from the pre-created chat) with **refreshed facts**; update `last_reminded_at`.
   - previously-active incident that **no longer fires** this run → send a one-line **"✅ resolved"** card; set `status=resolved` (a fresh recurrence later alerts anew).

**LLM budget per incident lifecycle:** ~1 (first detection) + 1 per genuine escalation. Flapping/sustained conditions cost **one** investigation, not one per 5-min run.

## Components

| # | Unit | File(s) | Responsibility |
|---|---|---|---|
| A | Materiality + orchestration | `automation/tier2_check.py` (extend) | the flow above; deterministic gates → dedup → backstop → investigate → deliver |
| B | Alerts memory + state machine | new `context_alerts.py` (or extend `context_findings.py`) + Delta table `audit_alerts` | dedup key, status transitions, 48h/escalation logic, audit |
| C | Investigation adapter for tier-2 | reuse `investigation/*` + a thin mapper | trigger→investigation, returns narrative + (ambiguous) verdict |
| D | Pre-created chat writer | new `adapters/chat_store_lakebase.py` | insert `Chat` + assistant `Message` into Lakebase `ai_chatbot`; returns `chat_id` |
| E | Webhook delivery | new `adapters/delivery_webhook.py` + `outbound.py` entry | build Adaptive Card, POST `{"attachments":[card]}` via egress; new/reminder/resolved variants |
| F | Tier-2 job wiring | `job.py` (`run_tier2_job`) | build the sinks + reasoner, pass `delivery_sinks`, gate on `TIER2_WEBHOOK_ENABLED` |
| G | Shared Alerts view | `e2e-chatbot-app-next/server/src/routes/alerts.ts` + a sidebar section | `GET /api/alerts` (system-owned public alert chats) + an "Alerts" list |

## Data model

**Delta `audit_alerts`** (catalog/schema `shakur-main.bi-fabrics-audit`, append/upsert, 90-day retention):
`incident_key` (PK-ish), `status` (`active`|`resolved`), `severity`, `check_type`, `resource`, `chat_id`, `first_alerted_at`, `last_alerted_at`, `last_reminded_at`, `resolved_at`, `escalation_count`, `materiality_reason`, `delivered` (bool), `run_at`.

**`incident_key` derivation (deterministic, stable across runs):**
- concentration → `concentration::{workspace}/{item}`
- throttle/pressure/overage → `{check}::{capacityId}` (capacity-scoped)

**Escalation** = recorded `severity` increases (info→warn) OR the primary metric worsens beyond a band (e.g. `peakCuPct` +20 pts, or `throttleMinutes` doubles). Deterministic; no LLM.

**Lakebase writes (Drizzle `ai_chatbot` schema, exact columns):**
- `Chat`: `id`=uuid4, `createdAt`=now, `title`=alert headline, `userId`=`'fabric-audit-agent'` (synthetic system user), `visibility`=`'public'`, `lastContext`=null.
- `Message`: `id`=uuid4, `chatId`, `role`=`'assistant'`, `parts`=`[{"type":"text","text":<investigation markdown>}]`, `attachments`=`[]`, `createdAt`=now, `traceId`=null.

## Delivery — Adaptive Card

`delivery_webhook.py` builds an Adaptive Card v1.4 and POSTs `{"attachments":[card]}` (UTF-8; `ensure_ascii=False` per project convention) to `POWER_AUTOMATE_ALERT_URL`. Card variants:
- **New/escalation:** severity title (emoji), `FactSet` (throttle mins / peak CU% / User→Item→Owner / % of base / recurrence "3rd time in 7 days"), a short investigation summary `TextBlock`, and `Action.OpenUrl` **"Investigate in chat"** → `<APP_URL>/chat/<chat_id>`.
- **Reminder:** "🔁 Still active (reminder N) — first seen <ago>" + refreshed facts + same link.
- **Resolved:** one-line "✅ Resolved — <incident> cleared".

Routing: registered in `outbound._ALLOWLIST` as `tier2_alert` → `sink="webhook"`; every payload passes `apply_egress_controls(..., sink="alert")` first (strips secrets, size-caps); `disclosure_line` appended. Gated by `TIER2_WEBHOOK_ENABLED` (default off) — the sink is only wired when the flag is set.

## Frontend — shared Alerts view

- `GET /api/alerts` → chats where `userId = 'fabric-audit-agent'` (system user), newest first, no per-user filter (public). Reuses the existing message/chat read path.
- Sidebar: an **"Alerts"** collapsible section above the personal history, listing alert conversations for everyone; click → `/chat/<id>`. Small, additive change to `sidebar-history.tsx` (or a sibling component). No change to the private per-user history.

## Config / secrets

- Secret `POWER_AUTOMATE_ALERT_URL` in the `fabric-audit` scope (the proven Power Automate URL). Never committed/logged.
- Env: `TIER2_WEBHOOK_ENABLED` (gate), `APP_URL` (deep-link base = the chat app URL), Claude endpoint for the tier-2 reasoner (only used when investigating).
- Tier-2 job identity needs **INSERT on `ai_chatbot`** (schema owned by the app SP) — planning task (grant the job's principal, or run the job as the app SP).

## Security / privacy

Per explicit decision: alert content (**including User→Item→Owner attribution**) goes to **both** the Teams channel **and** the public in-app Alerts view, **unredacted**. `egress.py` still strips secrets/tokens but intentionally preserves user-attribution (it is the point of the 30% concentration alert). This is the project's first shared/public data surface; the owner has accepted that all app users can see who is driving capacity.

## Risks (resolved in the plan)

- **Tier-2 job → Lakebase grant/auth:** the job must authenticate to Lakebase and hold INSERT on the SP-owned `ai_chatbot`. Decide identity + grant.
- **Drizzle schema coupling:** the Python writer must match the TS `Chat`/`Message` columns exactly; centralize the column mapping + a test that fails if the schema drifts.
- **LLM in Tier-2:** cost bounded by dedup-before-LLM ordering (above); confirm the tier-2 job has a Claude endpoint configured and that a reasoner failure degrades to a **facts-only** card rather than dropping the alert.
- **Secret hygiene:** URL only from the secret scope; never in git/logs; card content through egress.
- **Idempotency across job retries:** `incident_key` + `audit_alerts` status make re-runs safe (no duplicate chat/card for an already-active incident).

## Testing

- **Unit:** `incident_key` stability; state-machine transitions (new→alert, active<48h→silent, active>48h→reminder, escalate→re-alert, no-fire→resolved); deterministic materiality backstop rules; Adaptive Card builder (new/reminder/resolved shapes); egress passthrough.
- **Integration (injected fakes, no live posts):** a fired trigger → exactly one investigate call + one chat-write + one card payload; a repeat run within 48h → zero LLM, zero card; a >48h run → reminder reusing stored investigation (zero LLM); a cleared incident → resolved card + status flip.
- **Manual E2E:** force a trigger (or test hook) with `TIER2_WEBHOOK_ENABLED` → card lands in Teams → click → opens the saved investigation in chat → continue; the conversation appears in the in-app Alerts view.

## Open items for the plan

- Tier-2 job Lakebase identity + INSERT grant on `ai_chatbot`.
- Exact `audit_alerts` DDL + writer (mirror `context_findings` store pattern).
- Whether reminder cadence (48h) and escalation bands are env-tunable.
- Frontend Alerts section placement + styling (defer detail to plan).

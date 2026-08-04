# Design — Enable Chat History (Lakebase) for the Fabric Audit Agent chat app

- **Date:** 2026-08-04
- **Status:** Approved (design) — pending spec review
- **Sub-project:** #1 of 3 (history → Tier-2 Teams alerts → chart fixes)
- **Scope:** Turn on persistent chat history for the deployed `fabric-audit-agent` Databricks App. No new UI is built — the feature already exists in the template and is dormant.

---

## Context / Problem

The chat frontend (`fabric-audit-agent-app/e2e-chatbot-app-next/`, a Vercel-AI-SDK chatbot template) **already implements** persistent chat history:

- Postgres/Drizzle data layer in `packages/db/` — schema `ai_chatbot` with tables `Chat`, `Message`, `Vote` (`packages/db/src/schema.ts`), plus migrations `0000/0001/0002` in `packages/db/migrations/`.
- Save on send + reload on refresh (`server/src/routes/chat.ts`, `messages.ts`, `history.ts`).
- Sidebar history grouped by date (`client/src/components/sidebar-history.tsx`).
- Per-conversation routing `/chat/:id` (`client/src/App.tsx`, `pages/ChatPage.tsx`) that rehydrates a saved conversation.

**Every DB call is gated on `isDatabaseAvailable()`** (`packages/db/src/queries.ts`). The deployed app has **no database attached** (its app resources list has no `database`), so it runs in **ephemeral mode**: conversations are not saved, `/api/history` returns `204`, and the sidebar shows "Chat history is disabled."

The template's own bundle (`e2e-chatbot-app-next/databricks.yml`) contains the Lakebase wiring **commented out** — a `chatbot_lakebase` `database_instances` resource and an app `database` resource binding, under `# TODO (optional): Uncomment ... to enable persistent chat history`. Connection is by Databricks OAuth: `packages/db/src/connection.ts` builds the URL from `POSTGRES_URL` **or** `PG*` vars and uses a Databricks token (`getDatabricksToken()`) as the Postgres password.

**Root cause of "no history": no Lakebase database is bound to the app.** This sub-project attaches one and runs the schema migrations.

## Goals / Success Criteria

1. Sending a message, then refreshing, keeps the conversation (persisted to Lakebase).
2. The sidebar lists past conversations; selecting one reloads it.
3. Opening `/chat/<id>` in a fresh tab rehydrates that conversation's full history.
4. No "Chat history is disabled" banner; `isDatabaseAvailable()` is true in the deployed app.
5. Cost stays near-zero when idle (Lakebase autoscales down / suspends).

**Non-goals (later sub-projects):** Tier-2 → Teams alerts and the alert→saved-conversation deep-link (#2, which depends on this); chart render fixes / donut / color (#3). No changes to chat UI code, agent logic, or the MCP server.

## Approach (chosen): dedicated Lakebase instance, bound to the existing app

Considered and rejected: **(B) hand-set `POSTGRES_URL` + a manually-managed secret** — more manual, and diverges from the template's resource-binding + OAuth model, so it would drift from how the template expects to run.

Chosen approach:

1. **Provision a new, dedicated Lakebase Postgres instance** for this agent.
   - **Instance id:** `fabrics-audit-agent-memory` (Databricks database-instance names are lowercase/hyphen, no spaces).
   - **Display name:** "Fabrics Audit Agent Memory".
   - Small autoscale (min CU low, e.g. 1) with idle **suspend** enabled so it costs ~nothing when unused.
   - Dedicated (not a shared/other-team instance) — isolation matching how the agent's Delta memory lives in its own `shakur-main.bi-fabrics-audit`; also avoids the service-principal schema-ownership conflict the Lakebase skill warns about.

2. **Bind the instance to the existing `fabric-audit-agent` app as a `database` resource.** Databricks then injects the Postgres connection env into the app container (both the Python backend and the Node frontend process see it), and the app's service principal authenticates via its OAuth token — the template's `getDatabricksToken()` path. No secret to hand-manage.

3. **Create the schema once.** Run the template's Drizzle migrations (`0000`→`0002`) against the new database to create the `ai_chatbot` schema + `Chat`/`Message`/`Vote`, owned by the app service principal. The template has **no boot-time migrate**, so this is an explicit one-time step (run with a Databricks-token connection to the instance). A dedicated fresh schema sidesteps SP ownership conflicts.

4. **Redeploy the app.** With the `database` resource present, `isDatabaseAvailable()` becomes true and the already-built persistence + sidebar + `/chat/:id` activate. **Zero frontend/agent code changes.**

## Components touched

| Component | Change |
|---|---|
| Databricks Lakebase | New instance `fabrics-audit-agent-memory` + a database in it |
| `fabric-audit-agent` app resource config | Add a `database` resource binding (grants the app SP DB access + injects PG env) |
| Schema | One-time Drizzle migration → `ai_chatbot.{Chat,Message,Vote}` |
| `e2e-chatbot-app-next/databricks.yml` | (Optional, for reproducibility) uncomment/adjust the Lakebase stanza to document the binding — the live binding is on the actual app |
| Frontend / agent code | **None** |

## Data flow (already implemented)

```
browser → Vite/Express → POST /api/chat
   → saveMessages(user)         → ai_chatbot.Message
   → stream assistant reply     → onFinish → saveMessages(assistant + traceId)
   → createChat + async title   → ai_chatbot.Chat
GET /api/history  → getChatsByUserId → sidebar list
GET /api/messages/:id → getMessagesByChatId → /chat/:id rehydrate
```

## Risks / edge cases (resolved during planning via the `databricks-lakebase` skill)

- **App-SP Postgres grants:** the app service principal (`app-... fabric-audit-agent`) must own/have privileges on the `ai_chatbot` schema. Mitigated by a dedicated instance + schema created by that SP.
- **Cold-start latency:** first query after idle-suspend may lag a few seconds; acceptable for a chat app. Keep min CU low but non-zero if the first-message delay is objectionable.
- **PG env-injection format:** confirm whether the `database` binding provides `POSTGRES_URL` or discrete `PG*` vars; `connection.ts` supports both. Verify in the plan.
- **App deploy topology:** the app is deployed from the parent `fabric-audit-agent-app` source path via a workspace-managed `app.yaml` (`uv run start-app`), **not** via the `e2e-chatbot-app-next` sub-bundle. So the `database` binding must be attached to the **existing app** (apps update / app resource config), not created by deploying the sub-bundle (which would define a different app). This reconciliation is a planning task.
- **Migration execution environment:** the one-time `drizzle-kit migrate` needs network access + a token to the new instance (run locally against the instance endpoint, or as a one-off job). Decide the exact runner in the plan.
- **Privacy/retention:** conversations now persist. Lakebase default history retention applies; no extra PII handling in scope. Flag if a retention policy is later required.

## Verification / testing

Manual E2E after enabling (no automated suite for infra):
1. Open the app URL, send "What's the current capacity health?" → get a reply.
2. Refresh → the conversation is still there (not a blank new chat).
3. Sidebar shows the conversation under Today; no "history disabled" banner.
4. Open `/chat/<id>` in a new tab → full history rehydrates.
5. `databricks apps get fabric-audit-agent` shows the `database` resource; app `RUNNING`.
6. (Optional) query `ai_chatbot.Chat` / `Message` to confirm rows were written.

## Open items to settle in the implementation plan

- Exact Lakebase create command + autoscale/suspend params (via `databricks-lakebase`).
- The mechanism to attach the `database` resource to the existing app (update vs redeploy with resource config) and confirm PG env var names.
- The migration runner (local vs one-off job) and SP grant sequence.

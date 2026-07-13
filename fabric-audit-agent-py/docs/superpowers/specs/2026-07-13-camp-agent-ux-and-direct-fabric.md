# CAMP Agent — UX upgrades + direct Fabric access — Design Spec

**Date:** 2026-07-13 · **Status:** design, pre-plan · **Branch:** `feat/camp-agent-ux-and-direct-fabric`
**Approved directions (user, 2026-07-13):** fork & **own the vendored frontend** (`e2e-chatbot-app-next`,
now in the repo); direct Fabric access is **read-only** (guaranteed by the SP's read-only permissions —
do NOT add artificial filters that hide legitimate data the team needs); **no credentials in the public
repo** — ship an agent-app `app.yaml` with the same secret-backed resource references as the MCP app for
the user to populate.

## Purpose
Four improvements to the deployed CAMP agent (Databricks App = React chat frontend + Python agent
backend), keeping all existing behavior intact:
1. Auto-greeting on chat open with clickable capability **bubbles**.
2. **Direct Fabric access** (REST/SDK) alongside the MCP tools — read-only, agent chooses the path.
3. **Side-by-side** check cards (responsive grid) instead of a vertical stack.
4. Animated **"…"** loading indicator while a check runs.

## Invariants (unchanged)
Read-only is ABSOLUTE — direct Fabric access is GET/query only; never write/scale/trigger-refresh
(enforced by construction AND by the SP's read-only grant). Secrets are still scrubbed before egress
(masking credentials, never audit data). No real tenant/client IDs or secrets in the public repo.
Honesty rules, grounding, and the lean/visual voice all stay.

## Architecture (as-found)
- **Frontend** `fabric-audit-agent-app/e2e-chatbot-app-next` (Vite + React, now vendored). Relevant
  components: `greeting.tsx` (welcome text from `AppConfigContext.greeting`), `suggested-actions.tsx`
  (renders clickable starters; `onClick` already calls `sendMessage(...)` → starts the agent),
  `message.tsx` + `lib/tool-group-segments.ts` (groups consecutive `dynamic-tool` parts into a
  `tool-group` block), `components/elements/tool.tsx` + `elements/mcp-tool.tsx` (render each tool call
  as a card with a status badge). `start_app.py` prefers a present `e2e-chatbot-app-next/` over cloning
  → vendoring is the ownership switch.
- **Backend** `agent_server/agent.py`: `stream_handler` (MLflow `ResponsesAgentStreamEvent`) currently
  emits each in-progress check as a **text** item (`create_text_output_item(_progress_text(name,inp))`)
  — which is why checks render as stacked text, not cards. `_run_tool_loop` runs the model+tools; today
  tools come only from the MCP server (`FABRIC_MCP_URL`).

## Design

### Feature 1 — Auto-greeting + capability bubbles (frontend)
`suggested-actions` already auto-renders on the new-chat screen (no input needed) and its `onClick`
sends the text as the first user message, which triggers the agent. So this is customization, not new
plumbing:
- **`greeting.tsx`**: friendly self-introduction ("I'm CAMP — your read-only Fabric/Power BI capacity
  analyst. Pick something below or ask me anything.") — sourced from config (or inlined if config isn't
  wired for it; determined in the plan).
- **`suggested-actions.tsx`**: replace the two generic starters with the CAMP capability set, rendered as
  **bubbles/chips** (restyle from the current vertical bordered list to wrapped pill buttons). Each
  bubble's click text is a natural-language prompt that maps to a real capability, e.g.:
  Run a Fabric capacity audit · Check for unusual activity spikes · Look into a user's activity ·
  Analyze a specific query · Inspect a specific model · Review workspace usage · Check dataset refresh
  history · Identify top resource consumers · Summarize recent audit logs.
  (Prompts needing a parameter — a user, a model — send a lead-in that makes the agent ask for the
  specific,  or open a short follow-up; the agent already handles "which user?" gracefully.)

### Feature 2 — Direct Fabric access (backend, read-only)
New module **`agent_server/fabric_direct.py`**: a read-only Fabric client (stdlib `requests` + the
Fabric REST API; optionally `azure-identity` for token acquisition) authenticated with the SP creds
from env. Exposes a small set of **GET-only** capabilities the model can call as tools *alongside* the
MCP tools, e.g. `fabric_list_workspaces`, `fabric_list_items(workspace)`, `fabric_capacity_metrics`,
`fabric_refresh_history(dataset)`, `fabric_dataset_usage`. Wiring:
- Register these as additional tool definitions in the agent's tool list, so `_run_tool_loop` sees BOTH
  MCP tools and direct tools; the model picks per task (spec §"agent chooses the path").
- **Read-only by construction:** only GET requests; a hard allowlist of REST paths; no POST/PATCH/DELETE
  method is reachable. Also naturally bounded by the SP's read-only permission.
- **Proactive surfacing:** the direct client lets the agent pull metrics/anomalies without being asked
  (e.g. when answering "how's Fabric today", it may also flag a spike it noticed) — still read-only,
  still grounded, still labeled.
- **Credentials:** a new `fabric-audit-agent-app/app.yaml` env block referencing the SAME secret scope
  resources as the MCP app (`FABRIC_TENANT_ID`/`FABRIC_CLIENT_ID`/`FABRIC_CLIENT_SECRET` via `valueFrom`,
  plus the Fabric REST/LA/Eventhouse config), with NO values — the user copies the resource bindings.
  The direct client is **inert unless those env vars resolve** (no creds → the direct tools simply
  aren't offered; MCP path still works), so nothing breaks pre-configuration.

### Feature 3 — Side-by-side check cards (backend + frontend)
- **Backend:** in `stream_handler`, emit each check as a **structured tool-call event** (a function/tool
  output item with the check name, an `input-available`→`output-available` state, and a short result)
  instead of a plain text item. This makes the frontend render them as the existing tool **cards**
  (`dynamic-tool` parts) rather than stacked text. `_progress_text` stays as the human-readable card
  label (no tool names leaked). The final answer remains a normal text item.
- **Frontend:** in the `tool-group` render path (`message.tsx`), lay the grouped cards out in a
  **responsive CSS grid** (`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3`) so they sit
  side-by-side and wrap. Each card shows: check name, status (in progress / done / error), and a brief
  result once complete (already supported by the tool card; we restyle the container).

### Feature 4 — Animated "…" (frontend)
In the tool card (`elements/tool.tsx` status area), when state is in-progress (`input-available`), show
an animated "…" (three pulsing dots via a small CSS keyframe / Tailwind `animate`), appearing the moment
the card is created and disappearing when the result (`output-available`/error) lands.

## Testing
- **Backend (pytest):** `fabric_direct` — GET-only enforced (no write method reachable; a constructed
  write attempt raises/refuses); inert when unconfigured (tools not offered, no crash); URL allowlist
  honored; a planted secret in a response is scrubbed before it reaches the model/output. `stream_handler`
  — emits structured tool-call events (name/state/result) for each check; final answer still a text item;
  failure still ends the stream cleanly. Prompt-parity test stays green.
- **Frontend:** `npm run build` succeeds (TypeScript compiles); existing frontend tests (if any) pass;
  Biome/lint clean. Manual/visual check: bubbles render on load + click starts the agent; cards render in
  a grid; "…" animates while running.
- Offline/deterministic where possible; the direct Fabric client is exercised with an injected/faked HTTP
  layer (never a real tenant call in tests).

## Deploy
Both apps redeploy (agent app for all four; MCP unaffected unless a shared change lands): bump agent-app
version, `repos update` → `apps deploy fabric-audit-agent`. The vendored frontend builds at deploy
(`npm install && build`). Direct Fabric tools stay inert until the user populates the new `app.yaml`
secret resources. Shared infra — coordinate with the user.

## Explicitly NOT pursued
- **Any write/mutating Fabric action** — read-only absolute (no scale, refresh-trigger, delete).
- **A separate UI outside Databricks** — same Databricks App, same URL; we only own the frontend code.
- **Auto-pulling Databricks' upstream template** — we now maintain our vendored copy.
- **Per-user OBO** for the direct client in v1 — SP creds only (OBO stays Phase-7 gated); revisit if the
  team needs per-user scoping.

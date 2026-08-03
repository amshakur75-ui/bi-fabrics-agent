# Implementation Plan: CAMP UX + Direct Fabric

**Spec:** `docs/superpowers/specs/2026-07-13-camp-agent-ux-and-direct-fabric.md`
**Branch:** `feat/camp-agent-ux-and-direct-fabric` (frontend vendored at `4524cab`, spec `26fac58`)
**Method:** phased; verify each phase (backend pytest + `npm run build`) before the next. Keep existing
behavior intact. Read-only absolute; no secrets in the repo.

## Phase A — Feature 1: auto-greeting + capability bubbles (frontend only)
Most self-contained; the starter chips already auto-render on load and `onClick` already sends the
first message.
- **A1** `client/src/components/suggested-actions.tsx`: replace the two generic starters with the CAMP
  capability list; render as wrapped **bubble/pill** buttons (flex-wrap, rounded-full) instead of the
  vertical bordered list. Keep the existing `onClick → sendMessage` behavior.
- **A2** `client/src/components/greeting.tsx`: friendly CAMP self-introduction. If `AppConfigContext`
  doesn't let us set the greeting from our side, inline a CAMP default in the component.
- **AC:** on a new chat, a greeting + the capability bubbles appear with no input; clicking a bubble
  sends it and the agent starts. `npm run build` clean. Existing tests pass.

## Phase B — Features 3 + 4: side-by-side check cards + "…" (backend + frontend)
- **B1 (backend)** `agent_server/agent.py` `stream_handler`: emit each in-progress check as a
  **structured tool-call output item** (name = `_progress_text`, state input-available → output-available
  with a short result) instead of a text item, so the frontend renders it as a `dynamic-tool` card. Final
  answer stays a text item. Keep failure-clean streaming. Tests: assert structured tool events are
  emitted per check; final answer text item present; no tool-name leak in the label.
- **B2 (frontend)** `message.tsx` tool-group render: wrap grouped cards in a responsive grid
  (`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3`) so checks sit side-by-side and wrap.
- **B3 (frontend)** `components/elements/tool.tsx`: in the in-progress (`input-available`) state, show an
  animated "…" (pulsing dots) that clears on `output-available`/error.
- **AC:** multiple checks render as a wrapping grid of cards, each showing name/status/result; a running
  card shows animated "…"; `npm run build` clean; backend tests green.

## Phase C — Feature 2: direct Fabric access (backend, read-only) + app.yaml
- **C1** `agent_server/fabric_direct.py`: read-only Fabric client (SP token via env creds; stdlib
  `requests`). GET-only with a hard REST-path allowlist; no write method reachable. Inert (returns no
  tools) when creds/env absent. Scrub secrets from responses before returning.
- **C2** `agent_server/agent.py`: register the direct tools alongside the MCP tools in `_run_tool_loop`
  so the model chooses per task; direct tools omitted when the client is inert.
- **C3** `fabric-audit-agent-app/app.yaml`: add the SMTP-style secret-backed env block mirroring the MCP
  app (`FABRIC_TENANT_ID/CLIENT_ID/CLIENT_SECRET` via `valueFrom`, Fabric REST/LA/Eventhouse config),
  **no values** — user copies the resource bindings.
- **AC:** direct tools available only when configured; GET-only enforced by test (write attempt refused);
  secret scrubbed from a faked response; MCP path unaffected when direct client is inert; tests green.

## Checkpoint / Deploy
- [ ] Backend pytest green (agent-app suite + package unaffected); prompt parity green.
- [ ] `cd e2e-chatbot-app-next && npm install && npm run build` succeeds.
- [ ] Read-only held (no write path); no secrets in repo; existing behavior intact.
- [ ] Bump agent-app version; `repos update` → `apps deploy fabric-audit-agent`; verify live (bubbles on
      load, grid cards + "…", a direct-tool answer once creds set). Coordinate shared-infra deploy.

## Global constraints
Read-only absolute; secrets scrubbed; no repo secrets; keep existing functionality; no tool-name leaks to
users; lean/visual voice preserved. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Risks
| Risk | Mitigation |
|---|---|
| Structured tool-events break the stream / UI | Verify against the template's `dynamic-tool` shape; keep failure-clean fallback; test |
| Frontend build breaks at deploy | `npm run build` in CI/locally before deploy; vendored lockfile pins deps |
| Direct client leaks secrets or enables writes | GET-only allowlist; secret scrub; inert-unless-configured; tests assert all three |
| Bubbles needing a param (user/model) confuse the flow | Lead-in prompt that makes the agent ask for the specific |

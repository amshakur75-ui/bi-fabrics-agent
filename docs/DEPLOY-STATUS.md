# Fabric Audit Agent — Deploy Status

## Live Services

| Service | URL | Status |
|---------|-----|--------|
| **Agent App** (Phase 2) | `https://fabric-audit-agent-7405609570261849.9.azure.databricksapps.com` | RUNNING — verified end-to-end 2026-07-01 |
| **MCP App** (data tools) | `https://mcp-bi-fabrics-auditor-7405609570261849.9.azure.databricksapps.com/mcp` | RUNNING |
| **Claude endpoint** | `databricks-claude-opus-4-7` | READY |

## Verification (2026-07-01)

Tested directly against `/invocations` (bypassing the browser UI) using an OAuth token from
`databricks auth login` + `databricks auth token` via the Databricks CLI — a PAT does **not**
authenticate against `*.databricksapps.com` App URLs, but a CLI-issued OAuth token does.

Both gate questions returned grounded, tool-backed answers with `run_audit` confirmed in
`trajectory`/`toolResults`:
- *"Who is driving capacity on Enterprise A4A - SVT?"* → capacity `1faee871-…` at 177.6% CU
  peak (71.5 min throttled); item "Ent-Reporting-Sales" at ~48.5% CU concentration; verdict
  size-up. Cited an alternative hypothesis it ruled out (single dominant user).
- *"What caused the last capacity spike?"* → same audit data correlated into a spike narrative;
  explicitly stated what it could not confirm (interactive vs. scheduled-refresh trigger) rather
  than guessing.

Five bugs were fixed to reach this state (see commit history on `main` from `e485e0b` through
`ee7b8b7`):
1. **401 calling the MCP app** — a plain SP bearer token doesn't authenticate against another
   Databricks App's URL. Fixed by using `databricks_mcp.DatabricksMCPClient`, which performs the
   correct app-to-app OAuth negotiation via `DatabricksOAuthClientProvider`.
2. **`asyncio.run() cannot be called from a running event loop`** — `DatabricksMCPClient`'s sync
   `list_tools()`/`call_tool()` call `asyncio.run()` internally, which can't nest inside the
   already-running event loop our `@invoke()`/`@stream()` handlers run under. Fixed by using the
   async `alist_tools()`/`acall_tool()` variants throughout.
3. **401 calling the Claude serving endpoint** — `ws.config.token` is a PAT-only attribute; under
   the app's actual SP/OAuth auth it's empty, so the request silently sent
   `Authorization: Bearer None`. Fixed by using `ws.config.authenticate()`, which returns valid
   headers regardless of auth strategy.
4. **`ImportError` on `ResponsesAgent` from `mlflow.types.responses`** — that class lives in
   `mlflow.pyfunc`, not `mlflow.types.responses`, which only exports the standalone
   `create_text_output_item` function. Fixed by importing that function directly.
5. **400 Bad Request calling the Claude serving endpoint** — real Responses-API clients (the
   chat UI) send `content` as a list of blocks that mlflow parses into `ResponseInputTextParam`
   *objects*, not dicts. `_messages_from_request` only checked `isinstance(c, dict)`, silently
   dropping every block and sending Claude an empty message. Fixed by falling back to
   `getattr(c, "text", "")` for non-dict blocks.

## Known Issues — Phase 3 Backlog

1. **No capacity-name filter on `run_audit`.** `run_audit`'s `input_schema` takes no parameters,
   so it always audits whichever capacity the collector is configured against — it cannot target
   a capacity named in the question (e.g. "Enterprise A4A - SVT"). In a single-capacity estate
   this is invisible; in a multi-capacity estate it means the agent can only ever answer about
   one capacity, honestly abstaining on any other name rather than fabricating data (confirmed
   2026-07-01 — the abstain behavior itself worked correctly). **Real product gap, directly
   relevant to Phase 3**: `run_audit` needs a capacity-id/name parameter, `list_workspaces` (or a
   new tool) needs to resolve a human name to that id, and the collector/pipeline need to accept
   a capacity selector instead of a single fixed target.

2. **Inlined loop drift risk (minor, not urgent).** `agent_server/agent.py` still inlines its own
   copy of the tool loop (`_run_tool_loop`) and system prompt rather than importing the tested
   `fabric_audit_agent.agent.loop`/`system_prompt` package. `tests/test_agent_server.py`'s
   `TestInlinedLoopParity` class guards against behavioral drift between the two copies, but that
   mitigates the risk rather than eliminating it — a change to the real `loop.py` still has to be
   manually mirrored into `agent_server/agent.py`, and nothing fails until the parity tests are
   updated by hand. Worth revisiting once the agent app's packaging story is settled (e.g.
   vendoring `fabric_audit_agent` properly or publishing it as an installable dependency).

## Architecture

```
User → fabric-audit-agent (Databricks App)
           │
           ├── reasoning → databricks-claude-opus-4-7  [OpenAI chat-completions format]
           │                                             [§B1-alt adapter active — see below]
           │
           └── data tools → mcp-bi-fabrics-auditor (Databricks App, /mcp)
                               ├── CapacityEvents Eventhouse  (live CU%)
                               ├── SemanticModelLogs Eventhouse (per-item attribution)
                               └── Log Analytics (tenant-wide per-user activity)
```

## Claude Endpoint Protocol — IMPORTANT

`databricks-claude-opus-4-7` returns **OpenAI chat-completions format**, NOT Anthropic Messages:
- Response shape: `choices[0].message.content`, `choices[0].finish_reason`
- NOT: `content[0].text`, `stop_reason`

A `§B1-alt` adapter in `fabric-audit-agent-app/agent_server/agent.py` (`_build_claude_client`)
translates the request (Anthropic → OpenAI) and response (OpenAI → Anthropic blocks + stop_reason)
so the tool loop sees standard Anthropic format. Confirmed by B1 smoke test 2026-07-01.

If the endpoint is ever replaced with one that speaks native Anthropic Messages, remove the adapter
and replace `_build_claude_client` with a standard `anthropic.Anthropic(...)` client.

## Service Principals

| App | SP Name | SP Client ID |
|-----|---------|-------------|
| `fabric-audit-agent` | `app-1g3673 fabric-audit-agent` | `4bbc5413-2627-4be0-a93c-4a0af36f0dd3` |
| `mcp-bi-fabrics-auditor` | (earlier deploy, same workspace) | — |

The Entra SP used by the MCP server for data collection:
- Client ID: stored in `app.yaml` as `FABRIC_CLIENT_ID` (workspace-only, never committed)
- Tenant: stored in `app.yaml` as `FABRIC_TENANT_ID` (workspace-only, never committed)
- Secret: stored in Databricks secret scope `fabric-audit` key `FABRIC_CLIENT_SECRET`
- **ACTION REQUIRED**: rotate this secret — it was exposed in a prior chat session.

## User Authorization (OBO) — Pending Admin Action

The agent currently runs as its **service principal** (not the requesting user's identity).
To enable per-user identity (OBO):

1. A **workspace admin** must enable: Workspace Settings → Feature Preview → **User authorization for Databricks Apps**
2. After enabling, `get_user_workspace_client()` in `agent_server/agent.py` returns the user's downscoped token
3. The agent then inherits the user's read grants — fully read-only, auditable per-user

Until OBO is enabled, all requests run as the service principal. Data access is still read-only.

## Source Code Locations

| Component | Workspace Path | Repo Path |
|-----------|---------------|-----------|
| MCP server | `…/fabric-audit-agent-py/` | `fabric-audit-agent-py/` |
| Agent app | `…/fabric-audit-agent-app/` | `fabric-audit-agent-app/` |
| Agent app source (`agent.py`) | `…/fabric-audit-agent-app/agent_server/agent.py` | `fabric-audit-agent-app/agent_server/agent.py` |

**Note on inlined code:** `agent_server/agent.py` inlines the tool loop (`loop.py`) and system prompt
(`system_prompt.py`) from `fabric_audit_agent/agent/` to keep the agent app self-contained.
Tests in `fabric-audit-agent-app/tests/test_agent_server.py` cover the adapter and MCP client.
The inlined copies are currently identical to the originals — any changes to `loop.py` or
`system_prompt.py` must be reflected manually in `agent_server/agent.py`.

## Outstanding Admin Asks

1. **URGENT — Rotate Entra SP client secret** (exposed in prior chat session):
   - Azure Portal → App Registrations → find the SP by name (Client ID is in `app.yaml` / workspace secrets) → Certificates & secrets
   - Generate new secret → update Databricks secret scope: `databricks secrets put --scope fabric-audit --key FABRIC_CLIENT_SECRET`
   - Redeploy the MCP app after updating

2. **Enable User Authorization**: Databricks workspace admin → Settings → Feature Preview → User authorization for Databricks Apps

3. **Database shortcut / KQL permissions**: To enable cross-workspace SemanticModelLogs queries via Eventhouse shortcut, grant Contributor on the source KQL database to the service principal.

## MCP App Change Log (this deploy cycle)

**2026-07-01**: Activated `FABRIC_LA_WORKSPACE_ID` in MCP app's `app.yaml` (previously commented out).
- Before: `#- name: FABRIC_LA_WORKSPACE_ID` (commented)
- After: `- name: FABRIC_LA_WORKSPACE_ID / value: "45844fb8-0574-46ca-a4d9-a266b72847da"` (active)
- Effect: adds Log Analytics as a per-user attribution source for `run_audit` / `list_workspaces`
- Impact: additive and fault-tolerant — if LA auth fails, that collector is skipped with a warning
- Read-only: Log Analytics queries are read-only

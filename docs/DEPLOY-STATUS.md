# Fabric Audit Agent — Deploy Status

## Live Services

| Service | URL | Status |
|---------|-----|--------|
| **Agent App** (Phase 2) | `https://fabric-audit-agent-7405609570261849.9.azure.databricksapps.com` | RUNNING |
| **MCP App** (data tools) | `https://mcp-bi-fabrics-auditor-7405609570261849.9.azure.databricksapps.com/mcp` | RUNNING |
| **Claude endpoint** | `databricks-claude-opus-4-7` | READY |

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

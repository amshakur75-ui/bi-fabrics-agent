# B2-B6 Quick-Deploy (agent app on Databricks Apps)

Run these from your **local machine** (needs Databricks CLI with OAuth + git + uv).

---

## B2 — Clone the template

```bash
git clone https://github.com/databricks/app-templates /tmp/db-app-templates
cp -r /tmp/db-app-templates/agent-openai-advanced /tmp/fabric-audit-agent-app
cd /tmp/fabric-audit-agent-app
```

Verify the exact `@invoke`/`@stream` imports in `agent_server/agent.py` before overwriting.

---

## B3 — Drop in our handler + patch pyproject.toml

```bash
# 1. Pull our agent handler from the workspace
databricks workspace export \
  /Workspace/Users/abdishakur.mohamed@newellco.com/bi-fabrics-agent/fabric-audit-agent-py/app/agent.py \
  -o agent_server/agent.py

# 2. Vendor the fabric_audit_agent package (or install from GitHub — choose one):
#    Option A: vendor (simpler for now)
databricks workspace export \
  /Workspace/Users/abdishakur.mohamed@newellco.com/bi-fabrics-agent/fabric-audit-agent-py \
  --recursive -o fabric_audit_agent_src

cp -r fabric_audit_agent_src/fabric_audit_agent ./fabric_audit_agent
```

**Patch the template's `pyproject.toml`** — add under `[project]`:
```toml
dependencies = [
    "requests>=2",
    "msal>=1.24",
    "databricks-sdk>=0.28",
    "databricks-ai-bridge>=0.4",
    "databricks-mcp>=0.1",
    "mlflow>=2.16",
    "azure-kusto-data>=4.0",
]
```

---

## B4 — Configure `databricks.yml`

Replace the `resources.apps` section in the template's `databricks.yml` with:

```yaml
resources:
  apps:
    fabric_audit_agent_app:
      name: "fabric-audit-agent"
      description: "Read-only Fabric/Power BI capacity audit agent"
      source_code_path: .
      config:
        env:
          - name: DATABRICKS_CLAUDE_ENDPOINT
            value: "databricks-claude-opus-4-7"
          - name: FABRIC_MCP_URL
            value: "https://mcp-bi-fabrics-auditor-7405609570261849.9.azure.databricksapps.com/mcp"
      permissions:
        - level: CAN_USE
          group_name: users
      user_api_scopes:
        - serving.serving-endpoints
```

---

## B5 — Local test

```bash
uv run start-app
# Open http://localhost:8000
# Ask: "who is driving capacity on Enterprise A4A - SVT?"
# Expect: grounded answer with CU% figures + user attribution from MCP tool calls
```

---

## B6 — Deploy

```bash
databricks auth login   # OAuth — must be OAuth, not a PAT
databricks bundle validate
databricks bundle deploy
databricks bundle run fabric_audit_agent_app
```

App URL after deploy: `https://fabric-audit-agent-<hash>.azuredatabricksapps.com`

---

## B7 — Enable user authorization (admin task)

A **workspace admin** must enable user authorization in the Databricks workspace settings:
Workspace Settings → Feature Preview → User authorization for Databricks Apps → Enable.

After enabling, the agent runs as the requesting user (read-only, their grants).
Without it the agent uses the app's service principal — still read-only, but not per-user OBO.

---

## Env vars needed at deploy time

| Var | Value |
|-----|-------|
| `DATABRICKS_CLAUDE_ENDPOINT` | `databricks-claude-opus-4-7` |
| `FABRIC_MCP_URL` | `https://mcp-bi-fabrics-auditor-7405609570261849.9.azure.databricksapps.com/mcp` |
| `FABRIC_TENANT_ID` | In Databricks secret scope `fabric-audit` → `FABRIC_TENANT_ID` |
| `FABRIC_CLIENT_ID` | In Databricks secret scope `fabric-audit` → `FABRIC_CLIENT_ID` |
| `FABRIC_CLIENT_SECRET` | In Databricks secret scope `fabric-audit` → `FABRIC_CLIENT_SECRET` |

The Fabric secret env vars are inherited from the secret scope — reference them in `databricks.yml`
under `config.env` as `valueFrom: { secret: { scope: fabric-audit, key: FABRIC_... } }`.
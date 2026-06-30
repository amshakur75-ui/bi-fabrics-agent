# Phase 2 Part B — Deploy the agent on **Databricks Apps** (work-machine runbook)

The agent **logic** is built, reviewed, and offline-tested (347 passing). This runbook hosts it as a
**Databricks App** (the lowest-error path — see the research note at the bottom) that **calls your
existing MCP App** for tools and the in-tenant Claude endpoint for reasoning, with **user
authorization (OBO)** read-only.

```
  User → Agent (Databricks App)  ── calls ──▶  Claude serving endpoint   (reasoning)
                  │              ── calls ──▶  MCP App (already running)  (read-only data tools)
                  ▼
            grounded answer
```
**One agent.** The MCP App is *not* a second agent — it's the data-tool service the agent calls.

> **Why Apps, not Model Serving:** the `databricks/app-templates` agent template bundles the
> @invoke/@stream server + chat UI + OBO + MCP wiring + **local testing** + DABs deploy — you fill in
> a proven scaffold instead of assembling parts, on the *same* primitive your MCP App already uses.
> Sources at the bottom.

> **Honest caveats (don't skip):**
> - **User authorization (OBO) is Public Preview + admin-gated** — a workspace admin must enable it.
> - The OBO downscope-token resource list (SQL, Genie, **Model Serving Endpoint**, AI Search, UC
>   Tables/Connections, Files) does **not explicitly include a custom MCP server**. So govern the
>   agent→MCP hop with **Databricks Apps permissions + a read-only service principal**; let OBO
>   downscope the *data sources* the MCP reads once admin enables it. Don't assume OBO flows through
>   the MCP.
> - Three integration points are SDK/workspace-specific — **verify against the cloned template**, not
>   from memory: the `mlflow.genai.agent_server` decorators, the `databricks_mcp` client API, and the
>   Claude endpoint protocol (B1).

## Prerequisites
- Databricks CLI (new standalone) + `databricks auth login` (OAuth — **PATs are not supported** for Apps agents).
- The **MCP App already deployed**, `mcp-`-prefixed, served at `https://<mcp-app>/mcp` (rename the existing `fabric-audit-mcp` → `mcp-fabric-audit` if needed).
- Env for the agent app: `DATABRICKS_CLAUDE_ENDPOINT` (default `databricks-claude-opus-4-7`), `FABRIC_MCP_URL=https://<mcp-app>/mcp`.
- A read-only **service principal** the agent app runs as (for the MCP/data hop until OBO is enabled). Read-only is absolute.

## B1 — Smoke the Claude endpoint FIRST (the one real unknown)
In a notebook, confirm how your endpoint wants to be called. The loop only needs `.messages.create(...)` returning content blocks + `stop_reason`:
```python
import anthropic
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
c = anthropic.Anthropic(base_url=f"{w.config.host}/serving-endpoints/databricks-claude-opus-4-7", api_key=w.config.token)
print(c.messages.create(model="databricks-claude-opus-4-7", max_tokens=32,
      messages=[{"role":"user","content":"Reply OK."}], tools=[]).stop_reason)
```
- Works → proceed. **§B1-alt:** if the endpoint only speaks OpenAI chat-completions, replace `_build_claude_client` in `app/agent.py` with a thin adapter that calls the OpenAI-compatible endpoint (`w.serving_endpoints.query(...)`) and maps the response into the Anthropic block shape (`tool_calls`→`tool_use` blocks, `finish_reason=="tool_calls"`→`stop_reason="tool_use"`). The loop stays unchanged.

## B2 — Clone the template
```bash
git clone https://github.com/databricks/app-templates
cd app-templates/agent-openai-advanced
```
This gives you `agent_server/agent.py`, `agent_server/start_server.py`, `databricks.yml`, `pyproject.toml` and the chat UI. **Verify the exact `@invoke`/`@stream` import + Responses types here** — copy them, don't assume.

## B3 — Drop in our handler
- Replace the template's `agent_server/agent.py` with **our `app/agent.py`** (the @invoke/@stream handler that builds the OBO client + Claude client + **MCP-sourced tools** and runs our tested loop).
- Add deps to `pyproject.toml`: our package `fabric-audit-agent` (or vendor `fabric_audit_agent/` via `code`), plus `anthropic`, `databricks-sdk`, `databricks-ai-bridge`, `databricks-mcp`, `mlflow`.
- The reused, tested core is `fabric_audit_agent.agent.loop.run_tool_loop` + `...system_prompt.build_system_prompt` — unchanged.

## B4 — Configure `databricks.yml` (scopes + env + MCP permission)
- **user authorization (OBO) scopes** — least privilege; the Claude endpoint is the main one:
  ```yaml
  user_api_scopes:
    - serving.serving-endpoints     # call the Claude model as the user
    # add only the read-only data scopes the MCP ultimately needs (sql / dashboards.genie / ...)
  ```
- **env vars**: `DATABRICKS_CLAUDE_ENDPOINT`, `FABRIC_MCP_URL`.
- **permissions**: grant the agent app's service principal **CAN_USE on the MCP App** (and read-only on the Claude endpoint + the telemetry sources). MCP access is governed by **Databricks Apps permissions**.

## B5 — Run it locally (biggest error-reducer)
```bash
uv run start-app        # agent server + chat UI at http://localhost:8000
```
Ask "who is driving capacity on `<capacity>`?" and "why did it spike?" — confirm a grounded answer with a **monitored-CU** figure + cited evidence (or an honest abstention), and that the trace shows the MCP tool calls. Fix anything here before deploying.

## B6 — Deploy
```bash
databricks bundle deploy
databricks bundle run agent_openai_advanced
```
The app goes live with a chat UI + REST endpoint (query with a **Databricks OAuth token**, not a PAT). Streaming + the step budget keep you under the ~120s gateway timeout.

## B7 — Turn on user authorization + the eval gate
- Have a workspace admin **enable user authorization**; the agent then acts as the requesting user (inherits their read grants) via `get_user_workspace_client()` — already wired in `app/agent.py`.
- Gate promotion on evals: the offline suites (`python -m fabric_audit_agent eval-agent` / `eval-investigations`) plus MLflow `mlflow.genai` judges (groundedness / safety) over a labeled set — block updates that regress. This is the *real* groundedness gate the offline token-trace proxy stands in for.

## Done = Phase 2 complete
One agent, hosted on a Databricks App, calling Claude to think and the MCP App to fetch read-only data, under the user's identity, traced and eval-gated. Next: **Phase 3** (metric/semantic layer + runbooks), **Phase 4** (watchdog Job + Activator/Teams alerts).

---
**Sources (current Databricks docs):**
[Author + deploy an agent on Databricks Apps](https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent-db-app) ·
[Migrate an agent from Model Serving to Apps](https://docs.databricks.com/aws/en/generative-ai/agent-framework/migrate-agent-to-apps) ·
[Host a custom MCP server](https://docs.databricks.com/aws/en/generative-ai/mcp/custom-mcp) ·
[Agent authentication (user authorization / OBO)](https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication) ·
[app-templates repo](https://github.com/databricks/app-templates) ·
[Managed MCP servers](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp)

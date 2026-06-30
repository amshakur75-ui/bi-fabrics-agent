# Phase 2 Part B — Deploy the agent on Databricks (work-machine runbook)

The agent **logic** is built, reviewed, and offline-tested (Phase-2 Part A / PR #2). This runbook
deploys it: wrap as an MLflow `ResponsesAgent`, register to Unity Catalog, deploy with **OBO
read-only** auth, smoke-test, and gate on eval judges. Run these on the **Databricks-connected work
machine** (`am08570`).

> **Honest caveat:** the MLflow `ResponsesAgent` + Databricks OBO/deploy APIs evolve. Each step says
> what to verify. Confirm against current docs (the `agents.deploy` / `ResponsesAgent` / OBO pages)
> before running. The agent *logic* (`fabric_audit_agent.agent.investigator.investigate`) is fixed and
> tested — only this deploy wrapper is environment-specific.

## Prerequisites
- `pip install -e '.[prod]' mlflow>=3.1 databricks-sdk databricks-agents databricks-ai-bridge anthropic` (and `pydantic>=2`).
- Env: `DATABRICKS_CLAUDE_ENDPOINT` (default `databricks-claude-opus-4-7`); the live-source vars the tools use (`FABRIC_LA_WORKSPACE_ID` / `FABRIC_KUSTO_CLUSTER` + `FABRIC_KUSTO_DB` / `FABRIC_CAPACITY_EVENTS_CLUSTER`, etc.).
- UC: catalog `fabric_audit`, schema `bi_fabrics_agent` (already provisioned).
- Grants: the read-only SP / OBO identity can query the Claude serving endpoint + the read-only MCP server + the telemetry sources. **Read-only is absolute** — no write/refresh/scale on any resource.
- The package importable in the serving env (log with `code_paths` or install the wheel — see B3).

## B1 — the Databricks-Claude client (the one integration point to verify FIRST)
The agent file `app/agent.py` builds an Anthropic-Messages client pointed at the in-tenant Claude
endpoint (`_build_client()`), **per request**, under OBO. The loop only needs an object with
`.messages.create(model, max_tokens, system, messages, tools)` returning content blocks
(`.type` in `text`/`tool_use`) + `.stop_reason`.

**Smoke it in a notebook before anything else:**
```python
from app.agent import _build_client, _MODEL
r = _build_client().messages.create(model=_MODEL, max_tokens=64,
        messages=[{"role": "user", "content": "Reply with the word OK."}], tools=[])
print(r.stop_reason, [b.text for b in r.content if b.type == "text"])
```
- If that returns text → the Anthropic protocol works; proceed.
- **§B1-alt (fallback):** if the endpoint speaks only OpenAI chat-completions, replace `_build_client()`
  with an adapter that calls `WorkspaceClient(credentials_strategy=ModelServingUserCredentials()).serving_endpoints.query(name=_MODEL, messages=..., tools=...)` (the SDK owns OBO auth) and translates the
  OpenAI response into the Anthropic block shape the loop expects: map `choices[0].message.tool_calls`
  → `tool_use` blocks (`id`, `name`, `json.loads(arguments)`), text → a `text` block, and
  `finish_reason=="tool_calls"` → `stop_reason="tool_use"`. Keep the same `.messages.create(...)`
  signature so `fabric_audit_agent.agent.loop` is unchanged.

## B2 — the ResponsesAgent (already written: `app/agent.py`)
`CapacityInvestigatorAgent(ResponsesAgent)` maps the MLflow Responses request → the core
`investigate(...)` → a `ResponsesAgentResponse` (text output + `trajectory`/`toolResults`/`stoppedReason`
as `custom_outputs`), with `@mlflow.trace(span_type=AGENT)` + `mlflow.anthropic.autolog()` and
`set_model(...)` for models-from-code. **Verify** the `ResponsesAgentRequest.input` shape matches
`_messages_from_request` (adjust the flattener if your MLflow version differs).

## B3 — log + register to Unity Catalog (with the OBO AuthPolicy)
```python
import mlflow
from mlflow.models.resources import DatabricksServingEndpoint   # + DatabricksFunction / DatabricksTable / the MCP resource as applicable
from mlflow.models.auth_policy import AuthPolicy, SystemAuthPolicy, UserAuthPolicy

mlflow.set_registry_uri("databricks-uc")
system = SystemAuthPolicy(resources=[DatabricksServingEndpoint(endpoint_name="databricks-claude-opus-4-7")])
user = UserAuthPolicy(api_scopes=["serving.serving-endpoints"])   # READ-ONLY scopes only; add the MCP/UC scopes the tools need
with mlflow.start_run():
    info = mlflow.pyfunc.log_model(
        python_model="app/agent.py", name="agent",
        code_paths=["fabric_audit_agent"],          # ship the tested core with the model
        auth_policy=AuthPolicy(system_auth_policy=system, user_auth_policy=user),
        pip_requirements=["mlflow>=3.1", "anthropic", "databricks-sdk", "databricks-ai-bridge", "pydantic>=2"],
    )
mlflow.register_model(info.model_uri, "fabric_audit.bi_fabrics_agent.capacity_investigator")
```
- **Verify:** the exact resource classes + the minimal read-only `api_scopes`. Principle of least
  privilege — only the scopes the read-only tools actually call. Confirm `mlflow.models.predict(info.model_uri, input_data=...)` returns a grounded answer locally before deploying.

## B4 — deploy (OBO read-only)
Primary (simplest; OBO-on-Model-Serving covers our resources — Claude endpoint + MCP + UC functions):
```python
from databricks import agents
agents.deploy("fabric_audit.bi_fabrics_agent.capacity_investigator", version=<v>, tags={"phase": "2"})
```
- **Alternative (Databricks App)** if you hit an OBO resource limit on Model Serving: host `app/agent.py`
  behind a small serving app, name it normally (the `mcp-` prefix rule is for *MCP* apps only), secrets
  via `valueFrom` (the repo is PUBLIC — never inline tenant/client IDs). The App gives broader OBO scope.
- **Verify:** a low-privilege test user sees only their permitted workspaces (OBO inherits UC row/column
  grants). OBO is admin-gated/Public-Preview — confirm the tenant setting is enabled; if not, fall back
  to the read-only SP identity for the (Phase-4) watchdog path.

## B5 — OBO is enforced in code + policy
Already wired: `app/agent.py::_build_client` builds the user client **inside `predict`** (identity at
query time), and B3's `AuthPolicy` declares the OBO scopes. Nothing to add — just confirm the
deployed endpoint's "On behalf of user" setting is on.

## B6 — smoke test
From the AI Playground (the registered model appears as an endpoint) or a REST call, ask:
- "Who is driving capacity on `<capacity>`?" → expect a grounded answer naming the user with a
  **monitored-CU** figure + cited evidence, or an honest abstention if monitoring isn't enabled.
- "Why did capacity spike at `<time>`?" → expect the top driver + evidence, or abstention.
- Confirm the MLflow trace shows the tool calls (`investigate_user` / `investigate_capacity_spike`).
- **~120s Apps/gateway timeout:** keep answers within the step budget; stream via `predict_stream` if you
  add it. Long/scheduled investigations belong on the watchdog Job (Phase 4), not the synchronous path.

## B7 — eval / judges gate (the real groundedness check)
The offline suites (`python -m fabric_audit_agent eval-agent` and `eval-investigations`) gate trajectory
+ coverage-honesty deterministically. Add the LLM judges over a labeled set and block promotion on
regression:
```python
import mlflow
from mlflow.genai.scorers import Correctness, Guidelines   # + Safety / RelevanceToQuery as available
results = mlflow.genai.evaluate(data=labeled_df, predict_fn=<deployed endpoint>,
                                scorers=[Correctness(), Guidelines(guidelines="Every claim must cite a tool result; abstain if evidence is insufficient; never present monitored CU as authoritative capacity CU.")])
```
- This is the **real groundedness gate** the offline token-trace proxy stands in for. **Verify** the
  current `mlflow.genai` scorer API (judge names/imports evolve).

## Done = Phase 2 complete
Agent answers grounded capacity questions in natural language, read-only, OBO-scoped, traced, and
gated by evals. Next: **Phase 3** (metric/semantic layer + runbooks) and **Phase 4** (watchdog Job +
Activator/Teams alerts + the eval flywheel).

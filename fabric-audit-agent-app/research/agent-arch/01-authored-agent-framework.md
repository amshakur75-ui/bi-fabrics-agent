# Authored Mosaic AI Agent Framework Path — `ResponsesAgent` on Databricks

**Scope of this doc:** the "traditional agent you own, hosted in Databricks" option for the
`bi-fabrics-audit-agent` BRAIN. You write the agent loop in Python, wrap it in MLflow's
`ResponsesAgent` interface, log it as an MLflow model (models-from-code), register it to Unity
Catalog, and serve it — either via `databricks-agents` `agents.deploy()` (Model Serving) or via
Databricks Apps. The READ-ONLY MCP "playbook" tools you already built attach unchanged.

**Verdict up front (this use case):** A *good fit for Phase 1*, but only **lightly** — the value of
the authored path is the governance/observability wrapper (UC registration, MLflow Tracing,
serving, monitoring), NOT the orchestration. Since your reasoner is in-tenant
`databricks-claude-opus-4-7` and your tools are an MCP server, you can keep the actual brain dead
simple (a thin tool-calling loop) and let the framework provide the production scaffolding. It
becomes the **right** brain when you need a governed, observable, scheduled-callable endpoint —
which is exactly what Phase 2 (watchdog) wants. It is **overkill** if Phase 1 is only ever a
notebook/REPL investigation for one analyst, or if you'd rather run the loop inside the Claude
Agent SDK / a plain script and never need a hosted endpoint.

> Important currency note (2026): For **new** agents, Databricks now recommends **Databricks Apps**
> as the deployment target ("full control over agent code, server configuration, deployment
> workflow"), with `agents.deploy()` → Model Serving as the alternative. The *authoring* interface
> (`ResponsesAgent`) and the MLflow logging/UC-registration steps are **identical** for both targets;
> only the serving/runtime differs. Several Model Serving docs now carry a banner steering new use
> cases to Apps. ([author-agent / Apps][apps], [deploy-agent banner][deploy])

---

## 1. The authoring interface: `ResponsesAgent` (and why not `ChatAgent`/`ChatModel`)

### Current recommendation
- **Databricks recommends `mlflow.pyfunc.ResponsesAgent`** to author agents. It "lets you build
  agents with any third-party framework, then integrate it with Databricks AI features for robust
  logging, tracing, evaluation, deployment, and monitoring." ([author-agent-model-serving][authsrv])
- **`ChatAgent` and `ChatModel` are deprecated/legacy.** Per MLflow: *"Since MLflow 3.0.0,
  `ResponsesAgent` is recommended instead of `ChatModel`… and instead of `ChatAgent`."* `ChatModel`
  is deprecated since 3.0.0 and slated for removal. The Databricks "Legacy input and output agent
  schema" page explicitly says the `ChatAgent`, `ChatModel`, `SplitChatMessageRequest`, and
  `StringResponse` schemas are deprecated and to migrate to `ResponsesAgent`. ([legacy-schema][legacy],
  [mlflow responses-agent][mlflowra])
- Use `ChatModel` only if you need strict OpenAI **ChatCompletion** compatibility; otherwise
  `ResponsesAgent` (OpenAI **Responses** schema) is preferred. For this project there is no reason
  to touch the legacy interfaces — start on `ResponsesAgent`.

### What `ResponsesAgent` gives you out of the box
Multi-agent support; streaming output in small chunks; **comprehensive tool-calling message
history** (intermediate tool messages preserved — useful for an *investigator* that must show its
reasoning chain); tool-call confirmation; long-running tool support; **automatic MLflow tracing**;
typed Python authoring classes; native `custom_inputs`/`custom_outputs`; and **MLflow auto-infers a
valid model signature** so you can skip manual signature work. ([authsrv][authsrv])

### The `predict` / `predict_stream` contract
```python
from typing import Generator
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent,
)

class FabricAuditAgent(ResponsesAgent):
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        ...                                  # non-streaming
    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        ...                                  # streaming
```
- `request.input` is the list of message items; `request.custom_inputs` carries extra params (e.g.
  the target Fabric user, time window, "which telemetry tier to start at").
- Response is `ResponsesAgentResponse(output=[...items...], custom_outputs={...})` — put your
  ranked hypotheses / assumptions / confidence into `custom_outputs` for structured downstream use.
- **Streaming pattern:** emit multiple `output_text.delta` events with the *same* `item_id`, then a
  final `response.output_item.done` event with the full text; MLflow aggregates the deltas into one
  trace span. Helper methods on the base class: `create_text_delta(delta, item_id)`,
  `create_text_output_item(text, id)`, `create_function_call_item(...)`,
  `create_function_call_output_item(...)`. ([authsrv][authsrv], [mlflowra][mlflowra])
- A common non-streaming pattern is to call `predict_stream` internally and collect the
  `response.output_item.done` events into the `output` list.

### Authoring guidance / gotchas
- Use **synchronous code or callback patterns** inside `predict` to avoid event-loop conflicts.
- **Initialize per-request state inside `predict`, not `__init__`** — critical for the watchdog/OBO
  case because the *calling user's identity is only known at request time* (see §4).
- Don't rely on in-process caching across turns; serving is distributed/stateless. For Phase 2
  memory, persist to a Delta/UC table or use [AI agent memory][memory] patterns, not instance vars.

---

## 2. The tool-calling loop (where your MCP "playbook" tools plug in)

The brain is a standard tool-calling loop; the framework doesn't impose one, so you author it. The
canonical Databricks pattern (framework-independent, "MCP Python SDK" example) is:

1. **Discover tools.** For each MCP server URL, `DatabricksMCPClient(server_url, workspace_client).list_tools()`,
   then convert each tool's `inputSchema` into an OpenAI `tools=[{type:"function", function:{...}}]` spec.
2. **Call the LLM** (`databricks-claude-opus-4-7`) via the Databricks OpenAI client with the message
   history + tool specs.
3. **If the model emits `tool_calls`**, dispatch each to `mcp_client.call_tool(name, args)`, append a
   `function_call_output` message, and loop back to step 2. Repeat until no tool call (this is where
   you'd add a **`max_iterations` guard** and the telemetry-tier fallback logic:
   Eventhouse → Log Analytics → Capacity Events).
4. **Return** the final assistant text (+ `custom_outputs` with hypotheses/confidence).

The doc ships a complete `SingleTurnMCPAgent(ResponsesAgent)` example doing exactly this with
`_to_chat_messages`, `_fetch_tool_infos`, `_make_exec_fn`, `_call_llm`, and `mlflow.models.set_model(...)`.
([use-mcp-in-agents][usemcp])

**Connecting MCP tools** — the same client works for managed, external (MCP Service), and **custom**
MCP servers; only the URL + auth differ. Your existing read-only MCP server is the "custom server
hosted as a Databricks app" case (`https://<app-url>/mcp`) — point `DatabricksMCPClient` at it.
Install `databricks-mcp` (plus `mcp>=1.9`, `databricks-sdk[openai]`, `mlflow>=3.1`,
`databricks-agents>=1.0`). You can also use higher-level helpers if you adopt a framework:
`databricks_langchain.DatabricksMultiServerMCPClient` (LangGraph) or
`databricks_openai.agents.McpServer` (OpenAI Agents SDK). ([usemcp][usemcp], [managed-mcp][managedmcp])

**Timeouts (matters for a multi-step investigator):** complex tool-calling that runs minutes needs
two env vars raised on the endpoint — `MLFLOW_DEPLOYMENT_PREDICT_TIMEOUT` (default 120s) and
`MLFLOW_DEPLOYMENT_PREDICT_TOTAL_TIMEOUT` (default 600s). A months-back investigation with
Eventhouse fallbacks can blow past defaults. ([mlflowra][mlflowra])

---

## 3. MLflow logging + Unity Catalog registration (the same for both serving targets)

### Models-from-code (recommended)
Author the agent in `agent.py` and call `mlflow.models.set_model(FabricAuditAgent())` at module
scope. A separate **driver notebook** logs it. The agent code is captured as a Python file + a pinned
env; at serve time the env is restored and the file is executed to load the agent.
([log-agent][logagent])

```python
import mlflow
from mlflow.models.resources import (
    DatabricksServingEndpoint, DatabricksFunction, DatabricksGenieSpace,
    DatabricksSQLWarehouse, DatabricksTable, DatabricksApp, DatabricksUCConnection,
)

with mlflow.start_run():
    logged = mlflow.pyfunc.log_model(
        python_model="agent.py",                 # models-from-code
        artifact_path="agent",
        input_example={"input": [{"role": "user", "content": "..."}]},
        resources=[                               # automatic auth passthrough deps
            DatabricksServingEndpoint(endpoint_name="databricks-claude-opus-4-7"),
            DatabricksApp(app_name="fabric-audit-mcp"),   # your custom MCP server
            # ... every UC function / warehouse / table the MCP tools touch
        ],
        example_no_conversion=True,
    )
```
- With `ResponsesAgent`, **you can skip manual signatures** — MLflow infers them.
- The **`resources=[...]`** list is the heart of governance: it declares every Databricks-managed
  dependency so the platform can mint least-privilege credentials at deploy time (see §4). Helper:
  `DatabricksMCPClient(...).get_databricks_resources()` auto-derives a managed server's resources.
- **Validate before deploy** with `mlflow.models.predict()`. ([logagent][logagent], [authn][authn])

### Register to Unity Catalog
```python
mlflow.set_registry_uri("databricks-uc")
uc = mlflow.register_model(logged.model_uri, "catalog.schema.fabric_audit_agent")
```
Registration packages the agent as a UC model, so **UC permissions govern authorization** for the
agent and its resources — central to your "governable" requirement. ([logagent][logagent])

---

## 4. Authentication — the crux of your READ-ONLY + limited-access constraint

Three methods, mixable per-resource ([authn][authn]):

| Method | Agent runs as | When to use here |
|---|---|---|
| **Automatic auth passthrough** | the **deployer** (system service principal, least-privilege, auto-rotated short-lived M2M tokens) | Default. Good when every analyst should see the *same* audit surface and the agent's SP holds read grants. |
| **On-behalf-of-user (OBO)** | the **end user** making the request, with **downscoped** API scopes | **Strongly relevant:** your users *lack access to some Eventhouse clusters*. OBO enforces per-user UC controls + user-attributed auditing, so the agent can't read more than the user could — and you wrap each tool init in `try/except` to **gracefully skip** sources the user can't reach (exactly the telemetry fallback story). |
| **Manual (env vars / secrets)** | explicit SP creds | For non-Databricks/external resources (e.g. if a tool hits Azure Log Analytics directly via PAT/OAuth secret). Overriding security env vars disables passthrough for other resources. |

Decision rule from the docs: *need per-user access control or user-attributed auditing?* → OBO.
Otherwise, if all resources support passthrough → passthrough; else manual. For a **read-only audit
investigator where access differs per analyst**, OBO is the natural primary, with manual auth only
for the external telemetry hops.

**OBO mechanics to remember:**
- Public Preview; requires MLflow ≥ 2.22.1 and must be **enabled by a workspace admin**.
- Build the client with `WorkspaceClient(credentials_strategy=ModelServingUserCredentials())` and
  **initialize it inside `predict`** (identity known only at runtime).
- At log time, pass `AuthPolicy(SystemAuthPolicy(resources=[...]), UserAuthPolicy(api_scopes=[...]))`
  declaring the minimum REST API scopes (e.g. `model-serving`, `ai-search`, `sql`,
  `unity-catalog`, `genie`). Tokens are restricted to just those scopes (least privilege).
- **Limit:** on Model Serving, OBO only covers a fixed resource set (AI Search, Serving endpoint,
  SQL Warehouse, UC Connections, UC Tables/Functions via SQL Statement Execution, Genie, MCP). If
  you need broader OBO scopes, Databricks says deploy on **Apps**. Note also a documented caveat:
  the `model-serving` scope can transitively reach APIs your agent didn't intend. ([authn][authn])

This OBO + read-only-MCP combination is the single strongest argument for the authored framework
over a bare script: it makes "read-only, absolute" and "user lacks access to some clusters"
**enforced by the platform**, not by your code's good intentions.

---

## 5. Deploying & serving

### Option A — `agents.deploy()` → Model Serving
```python
from databricks import agents
deployment = agents.deploy("catalog.schema.fabric_audit_agent", uc.version,
                           scale_to_zero_enabled=True)   # cost-sensitive!
deployment.query_endpoint
```
Requires `mlflow>=3.1.3`, `databricks-agents>=1.1.0`. One call provisions, by default
([deploy][deploy]):
- **Model Serving endpoint** — scalable REST API, auto load-balancing; **`scale_to_zero_enabled=True`**
  to idle to zero (cuts cost, adds cold-start latency — fine for an on-demand investigator).
- **Secure auth** — short-lived least-privilege creds for declared `resources` (verified against
  deployer permissions before issuance).
- **Real-time MLflow Tracing** to an experiment + inference tables.
- **Review App** — web UI for stakeholders to chat and leave feedback (label traces).
- **Production monitoring (beta)** — scorers on live traffic.
- Other params: `deploy_feedback_model`, `environment_vars`, `tags`, `workload_size`. Re-deploying
  the same UC model name does **zero-downtime** version rollout. Manage with `list_deployments` /
  `get_deployments` / `delete_deployment`.
- **Deprecation flags to know:** request/assessment logs and the standalone *feedback model* are
  deprecated in favor of MLflow 3 tracing + `log_feedback`. Don't build on the legacy feedback model.

### Option B — Databricks Apps (Databricks' recommendation for new agents)
Same `ResponsesAgent` code; an async FastAPI server (MLflow `AgentServer`) with built-in
observability/routing, **git-based versioning, local IDE dev**, and full control of server config.
Declare resources under `resources.apps.<app>.resources` in `databricks.yml`, then
`databricks bundle deploy && databricks bundle run`. Better fit if you want code-first/CI-driven
ops and broader OBO scopes. ([apps][apps], [usemcp][usemcp])

### Querying the deployed brain (Phase 1 conversational + Phase 2 programmatic)
Recommended client is **`databricks_openai.DatabricksOpenAI`** ([query-agent][query]):
```python
from databricks_openai import DatabricksOpenAI
client = DatabricksOpenAI()                      # Model Serving
resp = client.responses.create(model="<endpoint>", input=msgs,
        extra_body={"custom_inputs": {"target_user": "...", "window_days": 120},
                    "databricks_options": {"return_trace": True}})
# Apps: model=f"apps/{app_name}" with an OAuth U2M token
```
Also REST (`POST .../serving-endpoints/responses` or `/<model>/invocations`, OpenAI-compatible) and
SQL `ai_query()` (Model Serving only). `databricks_options.return_trace` / the
`x-mlflow-return-trace-id` header return the trace id for audit.

---

## 6. Observability — MLflow Tracing (built-in)
- **Automatic tracing**: `mlflow.<library>.autolog()` captures LLM calls, tool use, agent steps for
  20+ libs; enabled by default in DBR ≥ 15.4 ML for LangChain/LangGraph/OpenAI/LlamaIndex.
- **Manual**: decorate any function with **`@mlflow.trace`** to add a span (name, inputs, outputs,
  latency); auto + manual spans merge into one trace. Deployed agents trace in real time to an
  MLflow experiment + inference tables. ([tracing][tracing])
- For an *investigator* this is gold: every hypothesis, every tool fallback, every cluster it
  *couldn't* reach is a span you can audit/replay — directly serves "governable/observable."
- **Caveat:** if the driver notebook lives in a Git folder, set a non-Git experiment via
  `mlflow.set_experiment()` before `agents.deploy()` or real-time tracing silently won't work.

---

## 7. The Phase 2 watchdog (scheduled / proactive / memory)
- **Lakeflow Jobs** (formerly Jobs) schedules a notebook/Python task on cron or **table-update
  triggers** (2026: can trigger on system-table / Delta-shared updates). The task simply calls the
  deployed endpoint (`DatabricksOpenAI.responses.create`, REST, or `ai_query`) with a "scan these
  capacities" prompt + `custom_inputs`. ([lakeflow jobs][jobs])
- **Auth for unattended runs:** a scheduled job has no interactive user, so OBO doesn't apply —
  run the job as a **service principal** with read grants and use automatic passthrough / SP token.
  This is consistent with read-only.
- **Memory:** serving is stateless; persist findings/state to a Delta/UC table the agent reads on
  the next run, per Databricks [AI agent memory][memory] guidance. Tracing + inference tables give
  you the historical record the watchdog reasons over.
- The same registered UC model + endpoint serves **both** Phase 1 (interactive) and Phase 2
  (scheduled) — you build the brain once.

---

## 8. Effort, limits, and the recommendation

**Effort (Phase 1, realistic):** small. Write `agent.py` (~one `ResponsesAgent` with the MCP
tool-loop, ~150 lines, the doc gives a near-complete template), a driver notebook
(log → register → deploy, ~30 lines), set OBO `AuthPolicy` + `resources`, raise the predict
timeouts, turn on `scale_to_zero`. Days, not weeks — *if* you keep the loop thin and lean on the
managed pieces.

**What it buys you:** UC-governed model + permissions; platform-enforced least-privilege / OBO
(your read-only + limited-access constraints become *infrastructure*, not code discipline);
first-class MLflow Tracing for auditability; a Review App for analyst feedback; a single hosted
endpoint callable from chat *and* from a scheduled Job; framework-agnostic (your in-tenant
Claude + your MCP tools, no lock-in to LangGraph/etc.).

**Limits / friction:** Model Serving cold-starts with scale-to-zero; OBO is Public Preview,
admin-gated, MLflow-version-gated, with a *fixed* resource set on Model Serving (broader scopes
need Apps); the legacy feedback model + request/assessment logs are deprecated (use MLflow 3
tracing/`log_feedback`); predict timeouts must be tuned for long investigations; serving is
stateless so Phase 2 memory needs your own UC table. The framework gives you *scaffolding, not
reasoning* — the hypothesis-ranking/confidence logic is still yours to write in the loop.

**Use it when:** you want a governed, observable, **deployed** brain that Phase 2 can schedule, with
per-user read enforcement — i.e. the actual target architecture. **Don't bother when:** Phase 1 is a
throwaway notebook for one trusted analyst with full access, or you'd rather host the loop yourself
(Claude Agent SDK / plain service) and never need a Databricks-managed endpoint, Review App, or
inference tables. In that case the `ResponsesAgent` ceremony is pure overhead.

**Lean for `bi-fabrics-audit-agent`:** **Adopt the authored `ResponsesAgent` path, but keep the
brain minimal and prefer the Databricks Apps deployment target** (Databricks' current
recommendation; better OBO scope coverage, git/CI, local dev) with OBO auth as primary and a
service-principal/passthrough path for the scheduled watchdog. The framework's real payoff for *this*
project is governance + tracing + a dual-use endpoint — not orchestration.

---

## Sources (#docs)
- [Author an AI agent and deploy it on Databricks Apps][apps] — `docs.databricks.com/.../agent-framework/author-agent`
- [Author a ResponsesAgent (Model Serving)][authsrv] — `docs.databricks.com/.../agent-framework/author-agent-model-serving`
- [Log and register AI agents][logagent] — `learn.microsoft.com/.../agent-framework/log-agent`
- [Deploy an agent (Model Serving) — `agents.deploy()`][deploy] — `learn.microsoft.com/.../agent-framework/deploy-agent`
- [Query an agent][query] — `learn.microsoft.com/.../agent-framework/query-agent`
- [Authentication for AI agents — passthrough / OBO / manual][authn] — `learn.microsoft.com/.../agent-framework/agent-authentication-model-serving`
- [Legacy input/output agent schema — ChatAgent/ChatModel deprecation][legacy] — `learn.microsoft.com/.../agent-framework/agent-legacy-schema`
- [Use MCP servers in agents — DatabricksMCPClient, tool loop, deploy][usemcp] — `learn.microsoft.com/.../mcp/use-mcp-in-agents`
- [Databricks managed MCP servers][managedmcp] — `learn.microsoft.com/.../mcp/managed-mcp`
- [MLflow Tracing — GenAI observability][tracing] — `docs.databricks.com/.../mlflow3/genai/tracing/`
- [AI agent memory (stateful agents)][memory] — `docs.databricks.com/.../agent-framework/stateful-agents`
- [Lakeflow Jobs][jobs] — `docs.databricks.com/.../jobs/`
- [MLflow — ResponsesAgent for Model Serving][mlflowra] — `mlflow.org/docs/latest/genai/serving/responses-agent/`

[apps]: https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent
[authsrv]: https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent-model-serving
[logagent]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/log-agent
[deploy]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/deploy-agent
[query]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/query-agent
[authn]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-authentication-model-serving
[legacy]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-legacy-schema
[usemcp]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/use-mcp-in-agents
[managedmcp]: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/managed-mcp
[tracing]: https://docs.databricks.com/aws/en/mlflow3/genai/tracing/
[memory]: https://docs.databricks.com/aws/en/generative-ai/agent-framework/stateful-agents
[jobs]: https://docs.databricks.com/aws/en/jobs/
[mlflowra]: https://mlflow.org/docs/latest/genai/serving/responses-agent/

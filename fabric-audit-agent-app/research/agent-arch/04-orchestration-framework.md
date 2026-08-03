# 04 — Orchestration / Reasoning-Loop Framework

**Decision scope:** Which reasoning-loop / orchestration framework to author the
`bi-fabrics-audit-agent` with — a **read-only** Fabric/Power BI capacity *investigator*
running on Databricks that must (1) PLAN a multi-step investigation, (2) call read-only
MCP tools (granular + coded "playbook" tools, already built), (3) gracefully FALL BACK
across data sources, and (4) form ranked hypotheses + assumptions + confidence.

**Reasoner:** in-tenant Databricks-hosted Claude (`databricks-claude-opus-4-7`), exposed
both as OpenAI-compatible Chat/Responses **and** the Anthropic Messages API, hosted *inside
the Databricks security perimeter* — prompts/completions stay in-tenant.

**Packaging target:** MLflow `ResponsesAgent` + MLflow `AgentServer`, deployed on Databricks
Apps via Databricks Asset Bundles (DABs).

**Date of research:** 2026-06-29. Currency target: 2025–2026.

**Candidates compared:** LangGraph · OpenAI Agents SDK · raw Anthropic Messages tool-use loop
(plain Python) · DSPy.

---

## TL;DR recommendation

- **Recommended: LangGraph** (graph / state-machine), wrapped in MLflow `ResponsesAgent`.
  It is one of the two **first-class, Databricks-documented** authoring frameworks (official
  `agent-langgraph` app template exists), it natively expresses **deterministic playbook
  subgraphs + an LLM-planner escape hatch in the *same* graph**, it has the strongest
  reliability primitives (checkpointers, `interrupt()`, conditional re-plan/fallback edges,
  v1.1 model-retry middleware), and it has full MLflow autolog tracing. This is the best
  balance of *reliable, testable multi-step reasoning* with *escape-hatch flexibility* for a
  read-only investigator.
- **Runner-up / fallback: raw Anthropic Messages tool-use loop (plain Python)**, wrapped in
  `ResponsesAgent`. Anthropic's own "Building Effective Agents" guidance says start here; it
  is maximally transparent, hits the in-tenant Claude endpoint via its **native** Messages API
  (no beta adapter), and `ResponsesAgent` already gives you tracing/streaming/packaging. Choose
  it if LangGraph's abstraction proves to be more ceremony than the investigation logic warrants.
- **Overkill for the orchestration role: OpenAI Agents SDK** (its non-OpenAI/Claude path is a
  *best-effort beta* LiteLLM adapter — a reliability tax against a non-OpenAI in-tenant reasoner)
  **and DSPy** (a prompt/weight *optimizer*, not an orchestrator; needs ≥~50 labeled examples and
  a metric — wrong tool for an open-ended investigator with no labeled gold set).

---

## 0. The constraint that frames everything: Databricks Agent Framework + `ResponsesAgent`

The framework choice is *downstream* of how Databricks packages agents. Databricks' canonical
"Author an AI agent and deploy it on Databricks Apps" page is explicit and **framework-agnostic
at the packaging boundary**:

> "Databricks recommends MLflow `ResponsesAgent` to build agents. `ResponsesAgent` lets you
> build agents with **any third-party framework**, then integrate it with Databricks AI features
> for robust logging, tracing, evaluation, deployment, and monitoring capabilities."
> — [docs.databricks / learn.microsoft author-agent][db-author]

> "The template uses the OpenAI Agents SDK as the agent framework... **You can author agents
> using any framework. The key is wrapping your agent with MLflow `ResponsesAgent` interface.**"
> — [author-agent][db-author]

`ResponsesAgent` gives you (per the same doc): multi-agent support, **streaming output**,
**comprehensive tool-calling message history** (intermediate tool messages — important for an
investigator that must show its work), tool-call confirmation, long-running tool support, OpenAI
Responses-schema compatibility, **automatic MLflow tracing** (aggregates streamed responses), and
auto-inferred model signatures for AI Playground / Agent Evaluation / Monitoring compatibility.
[author-agent][db-author]; [MLflow ResponsesAgent][mlflow-ra]

**Practical consequence #1 — two frameworks are "blessed" with official templates.** The
`databricks/app-templates` repo ships **both** `agent-openai-agents-sdk` and `agent-langgraph`
(each implementing the Responses API via `AgentServer(agent_type="ResponsesAgent")` and
`predict()` / `predict_stream()`). LangGraph and OpenAI SDK are the two paths shown with first-class
code; pure Python and DSPy are "any framework" via the generic `ResponsesAgent` subclass.
[author-agent][db-author]; [app-templates][db-templates]; [MLflow ResponsesAgent][mlflow-ra]

**Practical consequence #2 — the investigation loop lives *inside* `predict_stream`.** Whatever
framework you pick, it runs as the body of a `ResponsesAgent.predict_stream` method. So the
framework's only job is to *structure the reasoning loop*; serving, schema, streaming envelope,
auth (app SP or on-behalf-of-user via `get_user_workspace_client()`), and MLflow tracing are
handled by `ResponsesAgent` / `AgentServer` regardless. This **lowers switching cost** between
the four candidates and means "framework maturity on Databricks" reduces largely to "does
Databricks document it + does MLflow autolog it."

**MLflow autolog tracing coverage** (relevant to "streaming + tracing"): MLflow Tracing supports
**OpenAI, LangChain, LangGraph, Anthropic, DSPy**, Databricks, Bedrock, AutoGen. On serverless
compute you must explicitly call the matching `mlflow.<lib>.autolog()` (e.g.
`mlflow.langchain.autolog()` covers LangGraph as a LangChain extension; `mlflow.anthropic.autolog()`;
`mlflow.dspy.autolog()`; `mlflow.openai.autolog()`). All four candidates therefore have a real
autolog path. [MLflow tracing integrations][mlflow-trace]; [Tracing LangGraph][db-trace-lg];
[Tracing DSPy][db-trace-dspy]

**MCP tool integration** (the agent's tools are already built as read-only MCP tools): Databricks
**recommends MCP as the primary way to connect agents to tools** — managed MCP servers (Genie, AI
Search, Databricks SQL, UC functions), external servers registered as governed **MCP Services**, or
self-hosted MCP on Databricks Apps. The tools-overview diagram literally labels the caller as
"agent code, built with frameworks like **LangGraph or the OpenAI SDK**." All routes are governed in
Unity Catalog (grants, policies, audit) — well aligned with a **read-only** posture, since you grant
only `CAN_QUERY` / read scopes in `databricks.yml` and on-behalf-of-user scopes (e.g. `sql`,
`dashboards.genie`). [Connect agents to tools][db-tool]; [author-agent][db-author]

> Read-only enforcement is **not** a property of the orchestration framework — it is enforced by
> Unity Catalog grants + MCP scopes + the fact the tools themselves are read-only. The framework
> only needs to *not* require write side-effects (none of the four do).

---

## 1. The core design question: deterministic playbooks vs LLM-driven planning

This use case is a *hybrid*: you have **coded "playbook" tools** (deterministic, known-good
investigation recipes) AND you need **LLM planning** to decide which playbook/granular tool to run
next, to fall back when a data source is missing, and to synthesize ranked hypotheses. Anthropic's
own guidance is the right lens here:

> "**Workflows** are systems where LLMs and tools are orchestrated through **predefined code
> paths**. **Agents**... are systems where LLMs **dynamically direct their own processes and tool
> usage**." Use workflows for predictability; use agents "where it's difficult or impossible to
> predict the required number of steps." — [Anthropic, Building Effective Agents][anthropic-bea]

> "**Start by using LLM APIs directly: many patterns can be implemented in a few lines of code.**"
> Frameworks "often create extra layers of abstraction that can obscure the underlying prompts and
> responses, making them harder to debug... [and] make it tempting to add complexity when a simpler
> setup would suffice." Add complexity "only when it demonstrably improves outcomes."
> — [Anthropic, Building Effective Agents][anthropic-bea]

The investigator needs **both** modes: deterministic playbook execution (workflow) wrapped by a
**bounded** dynamic planner (agent) that picks next steps, re-plans on missing data, and stops.
The four frameworks differ sharply in how cleanly they express *both at once*:

| Need | LangGraph | OpenAI Agents SDK | Raw Anthropic loop | DSPy |
|---|---|---|---|---|
| Deterministic playbook as code path | **Native** (a node / subgraph; sequential pipeline) | Awkward (everything is "agent + tools"; determinism via guardrails/handoffs) | **Native** (just call the function) | N/A (modules optimize prompts, not control flow) |
| LLM-driven planning loop | **Native** (`create_react_agent`, planner node, conditional edges) | **Native** (the run loop is the core concept) | **Native** (you write the while-loop) | `ReAct` module exists but it's an *optimization target*, not an orchestrator |
| Mix both in one unit | **Best** — planner node + deterministic subgraphs in one graph | Mediocre — bolt deterministic steps around the SDK loop | **Good** — you hand-write the branch logic | Poor |
| Escape-hatch flexibility | High (drop to a custom node / raw call any time) | Medium | **Highest** (it's all your code) | Low |

LangGraph is explicitly marketed and used for exactly this *"deterministic and non-deterministic
workflows"* mix: "sequential pipelines provide deterministic, step-by-step flows when autonomy is
not needed," "composable subgraphs... like Lego blocks," and a "Planner agent... breaks [a query]
down into sub-tasks," with conditional edges that "can incorporate LLM-based decision-making for
intelligent path selection." A common production pattern is a **hybrid**: a dynamic planner inside a
LangGraph node, with the deterministic graph providing "persistent state and auditability."
[LangGraph workflows][db-lc-wf]; [LangGraph conditional edges / production patterns][lg-prod]

---

## 2. Per-framework assessment for THIS use case

### A. LangGraph — **RECOMMENDED**

**Fit with Databricks Agent Framework + `ResponsesAgent`:** First-class. Official
`agent-langgraph` template; documented `LangGraphResponsesAgent` pattern implementing `predict()`
+ `predict_stream()` (filtering `response.output_item.done` events into the Responses stream).
`databricks_langchain.ChatDatabricks` is the documented client for the in-tenant endpoint and
supports Unity AI Gateway routing (`use_ai_gateway=True`). [author-agent][db-author];
[ResponsesAgent intro][mlflow-ra-intro]; [app-templates][db-templates]

**In-tenant Claude endpoint:** Uses `ChatDatabricks(endpoint="databricks-claude-...")` — a native
Databricks client, **no third-party adapter**. Works against the OpenAI-compatible surface; stays
in-perimeter. [author-agent][db-author]; [Anthropic Messages on Databricks][db-anthropic-msg]

**MCP tools:** Documented LangGraph + MCP path (tools-overview diagram names LangGraph explicitly);
`@tool` + `create_react_agent` shown in the official doc; managed/Services/custom MCP all reachable.
[author-agent][db-author]; [Connect agents to tools][db-tool]

**Deterministic playbooks vs LLM planning:** **Strongest of the four.** Playbooks = deterministic
nodes/subgraphs; planning = planner node + `create_react_agent` + conditional edges. Fallback across
data sources = conditional edges routing to alternate source nodes on error/empty. You can co-locate
both in one auditable graph. [LangGraph workflows][db-lc-wf]; [LangGraph production patterns][lg-prod]

**Reflection / retry / fallback:** Best-in-class. Checkpointers snapshot state every step (resume,
time-travel, recovery points for "interruption, timeout, human handoff, service restart");
`interrupt()` for human-in-the-loop approval/inspection of the plan as structured JSON; **v1.1
(Dec 2025) model-retry middleware** with configurable exponential backoff; recommended pattern of
`error_count`/`last_error` state fields + conditional edge to retry/fallback/re-plan nodes — directly
matching "gracefully fall back across data sources." [LangGraph state mgmt / failure recovery][lg-state];
[LangGraph HITL][lg-hitl]; [LangGraph production/retry][lg-prod]

**Streaming + tracing:** `predict_stream` streams; `mlflow.langchain.autolog()` traces LangGraph
(as a LangChain extension), nested spans auto-logged. [Tracing LangGraph][db-trace-lg]

**Maturity:** Mature, widely deployed in production through 2025–2026; stable v1.x line.
[LangGraph production review][lg-review]

**Complexity cost:** Real but justified. Graph + state schema + checkpointer is more ceremony than a
while-loop; Anthropic's caution about "extra layers of abstraction" applies. Mitigation: keep the
graph small (planner + a handful of playbook nodes + a synthesis node), lean on `create_react_agent`
for the dynamic leg, and rely on MLflow tracing for transparency. The reliability primitives are
exactly what an investigator that must be *testable and recover gracefully* needs, so the trade is
favorable here.

### B. Raw Anthropic Messages tool-use loop (plain Python) — **RUNNER-UP / FALLBACK**

**Fit + `ResponsesAgent`:** "Any framework" path — subclass `ResponsesAgent`, write the loop in
`predict`/`predict_stream`. Fully supported, just no template scaffolding. [author-agent][db-author];
[MLflow ResponsesAgent][mlflow-ra]

**In-tenant Claude endpoint:** **Best alignment.** The in-tenant endpoint exposes the **native
Anthropic Messages API** (`/serving-endpoints/anthropic/v1/messages`, model
`databricks-claude-opus-4-7`), hosted inside the Databricks perimeter. The `anthropic` SDK / Messages
loop talks to it directly — no compatibility shim, full access to Anthropic-native features
(tool-use blocks, prompt caching, thinking). [Anthropic Messages on Databricks][db-anthropic-msg]

**MCP tools:** You wire MCP tool schemas into the `tools=[...]` array and dispatch
`tool_use` blocks yourself — more code, but total control over fallback ordering and retry.

**Playbooks vs planning:** Both native and maximally explicit — playbook = a Python function;
planning = the model emitting tool calls in your while-loop. Reflection/retry/fallback are whatever
you code (evaluator-optimizer pattern, bounded retries). Anthropic explicitly recommends this
starting point. [Anthropic, Building Effective Agents][anthropic-bea]

**Streaming + tracing:** Stream via Messages streaming; `mlflow.anthropic.autolog()` traces calls;
`ResponsesAgent` aggregates the stream. [MLflow tracing integrations][mlflow-trace]

**Maturity:** Maximal — it's the SDK + your code; nothing to outgrow.

**Complexity cost:** *Lowest conceptual* abstraction but *highest hand-written* surface — you
re-implement checkpointing, durable resume, HITL pause/resume, and structured re-plan yourself. For
a single-tenant read-only investigator that doesn't need multi-day pauses, that may be perfectly fine;
if you later need durable resume / HITL approval, you'll be rebuilding what LangGraph gives free.
**This is why it's the fallback, not the primary.**

### C. OpenAI Agents SDK — **OVERKILL / poor fit for the non-OpenAI reasoner**

**Fit + `ResponsesAgent`:** First-class (it's *the* default template,
`agent-openai-agents-sdk`). Clean `@function_tool`, handoffs, guardrails, built-in run loop,
Responses-schema native. [author-agent][db-author]; [app-templates][db-templates]

**In-tenant Claude endpoint — the problem:** The SDK is OpenAI-first. Non-OpenAI models (Claude)
go through **LiteLLM / Any-LLM adapters that the SDK itself labels "best-effort, beta."** Pointing it
at the in-tenant Claude endpoint means an extra adapter layer and beta-grade reliability against your
*primary* reasoner — a poor trade when reliability matters. (Databricks' template does show a
`databricks_openai` async client + `set_default_openai_client`, which mitigates but still routes
Claude through OpenAI-shaped Chat Completions rather than native Messages.)
[OpenAI Agents SDK models / LiteLLM][oai-models]; [author-agent][db-author]

**Playbooks vs planning:** Planning loop is native and good; **deterministic playbooks are awkward**
— the SDK's worldview is "agents + tools + handoffs + guardrails," so encoding a fixed recipe means
bending guardrails/handoffs into control flow rather than just writing a node. Less natural than
LangGraph for the hybrid.

**Reflection/retry/fallback:** Guardrails (input/output) and handoffs exist, but there's no built-in
durable checkpointer / time-travel / `interrupt()` equivalent for graceful multi-source fallback;
you build that around the loop.

**Streaming + tracing:** Streaming yes; `mlflow.openai.autolog()` traces it. [MLflow trace][mlflow-trace]

**Maturity:** Production-ready since Mar 2025 (evolved from Swarm); solid. The disqualifier here is
**model fit**, not maturity. [OpenAI Agents SDK guardrails/handoffs][oai-guardrails]

### D. DSPy — **WRONG TOOL for the orchestration role**

DSPy is a **programmatic prompt/weight optimizer** (signatures + modules like `ChainOfThought`/`ReAct`
+ a metric + an optimizer that compiles prompts), not a control-flow orchestrator. It "pays off when
three conditions are met: the task has a measurable quality metric, you have **at least ~50 labeled
examples**, and prompt quality directly impacts business outcomes." [DSPy guidance][dspy-when];
[Building agents with DSPy][dspy-agents]

For an **open-ended read-only investigator** there is no labeled gold set of "correct investigations"
and the value is in *control flow + fallback + ranked hypotheses*, not in squeezing a classification
metric. DSPy also has weaker native primitives for deterministic-playbook orchestration and
multi-source fallback; it was "initially designed for single-objective optimization." It does have
`mlflow.dspy.autolog()` tracing and a Databricks tracing page, so it's *deployable* — but as the
*orchestrator* it's a mismatch. [DSPy guidance][dspy-when]; [Tracing DSPy][db-trace-dspy]

> Possible *narrow* future use: optimize a sub-prompt (e.g., the hypothesis-ranking step) with DSPy
> **inside** a LangGraph node *if* you accumulate labeled traces later — not as the top-level loop.

---

## 3. Scorecard (this use case)

Weighted for: reliable + testable multi-step reasoning, deterministic-playbook + LLM-planning hybrid,
escape-hatch flexibility, in-tenant Claude fit, MCP, tracing, maturity, complexity cost.

| Criterion | LangGraph | Raw Anthropic loop | OpenAI Agents SDK | DSPy |
|---|---|---|---|---|
| Databricks `ResponsesAgent` fit | High (template) | High (DIY) | High (default template) | Medium (any-framework) |
| In-tenant Claude endpoint fit | High (`ChatDatabricks`) | **Highest (native Messages)** | **Low (best-effort beta adapter)** | Medium |
| MCP read-only tools | High (documented) | High (manual) | High (documented) | Medium |
| Deterministic playbooks | **High** | High | Low | Low |
| LLM-driven planning | High | High | High | Medium (as optimize target) |
| Hybrid (both at once) | **Best** | Good | Mediocre | Poor |
| Reflection/retry/fallback | **Best (checkpoint/interrupt/retry mw)** | Manual | Partial (guardrails) | Weak |
| Streaming + MLflow tracing | High | High | High | High |
| Maturity (2025–26) | High | Highest | High | Medium |
| Complexity cost (lower=better) | Medium | **Low–Medium** | Medium | High-for-this-job |
| **Overall for this use case** | **★ Best** | **Runner-up** | Overkill (model mismatch) | Wrong tool |

---

## 4. Recommendation

1. **Build with LangGraph**, wrapped in MLflow `ResponsesAgent` (`predict_stream`), starting from
   the official `agent-langgraph` template. Model the investigator as: **planner node** (LLM picks
   next playbook/granular tool) → **deterministic playbook/granular-tool nodes** (read-only MCP
   calls) → **conditional edges** for fallback across data sources and bounded re-plan → **synthesis
   node** (ranked hypotheses + assumptions + confidence). Add a checkpointer (state persistence /
   recovery / optional HITL approval of the plan), use v1.1 retry middleware for transient endpoint
   errors, and enable `mlflow.langchain.autolog()`. Talk to the reasoner via
   `ChatDatabricks("databricks-claude-opus-4-7")`.

2. **Fallback option: raw Anthropic Messages tool-use loop in plain Python**, wrapped in
   `ResponsesAgent`. Switch to (or start with) this if LangGraph's graph/state ceremony outweighs the
   investigation logic, or if you want native Anthropic Messages features (prompt caching, thinking,
   native tool-use blocks) against the in-tenant endpoint with zero adapter layers. You keep MLflow
   tracing + Responses packaging either way; the migration cost between the two is low because both
   live inside the same `ResponsesAgent.predict_stream` boundary.

3. **Avoid for the orchestration role:** OpenAI Agents SDK (beta LiteLLM adapter against a non-OpenAI
   in-tenant reasoner = avoidable reliability risk) and DSPy (an optimizer needing labeled data +
   metric; mismatched to an open-ended investigator). DSPy may have a *narrow* later role optimizing a
   single sub-prompt inside a LangGraph node once labeled traces exist.

---

## Sources / #docs

- [db-author] Databricks — Author an AI agent and deploy it on Databricks Apps (Azure / AWS / GCP):
  https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/author-agent ·
  https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent
- [db-tool] Databricks — Connect agents to tools (MCP Services, managed/custom MCP, UC functions):
  https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-tool
- [db-templates] Databricks app-templates (agent-langgraph, agent-openai-agents-sdk):
  https://github.com/databricks/app-templates/tree/main/agent-langgraph ·
  https://github.com/databricks/app-templates/tree/main/agent-openai-agents-sdk
- [db-anthropic-msg] Databricks — Query with the Anthropic Messages API (in-perimeter, model ids):
  https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/query-anthropic-messages
- [mlflow-ra] MLflow — ResponsesAgent for Model Serving:
  https://mlflow.org/docs/latest/genai/serving/responses-agent/
- [mlflow-ra-intro] MLflow — ResponsesAgent Introduction:
  https://mlflow.org/docs/latest/genai/flavors/responses-agent-intro/
- [mlflow-trace] MLflow Tracing Integrations (OpenAI/LangChain/LangGraph/Anthropic/DSPy/...):
  https://docs.databricks.com/aws/en/mlflow3/genai/tracing/integrations/
- [db-trace-lg] Databricks — Tracing LangGraph:
  https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/integrations/langgraph
- [db-trace-dspy] Databricks — Tracing DSPy:
  https://docs.databricks.com/aws/en/mlflow3/genai/tracing/integrations/dspy
- [anthropic-bea] Anthropic — Building Effective Agents (workflows vs agents; start simple):
  https://www.anthropic.com/research/building-effective-agents
- [db-lc-wf] LangChain — Workflows and agents:
  https://docs.langchain.com/oss/python/langgraph/workflows-agents
- [lg-prod] LangGraph production patterns / conditional edges / v1.1 retry middleware:
  https://www.kalviumlabs.ai/blog/langgraph-in-production-stateful-multi-step-agents/ ·
  https://dev.to/jamesli/advanced-langgraph-implementing-conditional-edges-and-tool-calling-agents-3pdn
- [lg-state] LangGraph state management / checkpoints / failure recovery:
  https://eastondev.com/blog/en/posts/ai/20260424-langgraph-agent-architecture/
- [lg-hitl] LangChain — Human-in-the-loop:
  https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- [lg-review] LangGraph production review (2025):
  https://sider.ai/blog/ai-tools/langgraph-review-is-the-agentic-state-machine-worth-your-stack-in-2025
- [oai-models] OpenAI Agents SDK — Models / LiteLLM (best-effort beta non-OpenAI support):
  https://openai.github.io/openai-agents-python/models/ ·
  https://openai.github.io/openai-agents-python/models/litellm/
- [oai-guardrails] OpenAI Agents SDK — Guardrails / Handoffs:
  https://openai.github.io/openai-agents-python/guardrails/ ·
  https://openai.github.io/openai-agents-python/handoffs/
- [dspy-when] DSPy — when it pays off (metric + ~50 labeled examples):
  https://myengineeringpath.dev/tools/dspy-guide/
- [dspy-agents] DSPy — Building AI Agents / RAG as Agent:
  https://dspy.ai/tutorials/customer_service_agent/ · https://dspy.ai/tutorials/agents/

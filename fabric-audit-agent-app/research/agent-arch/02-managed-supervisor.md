# The Managed Path: Databricks AI Playground + Agent Bricks Multi-Agent Supervisor (+ Supervisor API)

**Scope:** Evaluating the *managed brain* options for the read-only Fabric/Power BI capacity **investigator** agent. The read-only MCP tools are fixed and out of scope; this doc is only about whether a managed orchestrator (AI Playground prototyping → Agent Bricks Supervisor Agent → Supervisor API) is the right "brain" vs. an authored ResponsesAgent.

**Date of research:** 2026-06-29. All Databricks/MS Learn docs cited carry `ms.date: 2026-06-29`, so this is current. Supervisor Agent went **GA 2026-02-10**.

**TL;DR for this use case:** The fully managed *no-code* Supervisor Agent is **interactive-first, opinionated, and low-control** — good for a Phase-1 conversational demo, but you will outgrow it on memory, autonomy, deterministic playbooks, and eval. The sleeper option is the **Supervisor API (Beta)** — Databricks runs the agent loop for you (offloading the planning/tool-execution loop), but *you* choose the in-tenant Claude model, set the system prompt (`instructions`), invoke it programmatically, and run it in **background mode** (autonomy). That is the natural managed landing zone for this agent. See recommendation at the bottom.

---

## 1. The three managed tiers (Databricks' own framing)

Databricks explicitly lays out three ways to build a tool-calling agent, in increasing control / decreasing magic [#supervisor-api]:

1. **Agent Bricks Supervisor Agent** (no-code, "recommended"): Fully declarative, human-feedback optimization (ALHF) for highest quality. UI-driven.
2. **Supervisor API** (Beta): Build a custom agent programmatically — *choose models at runtime, control which tools per request, set the system prompt* — while still **offloading agent-loop management to Databricks**. "Also the right choice when you need control over model choice while offloading agent loop management."
3. **AI Gateway unified/native APIs**: You write your own agent loop; Databricks provides only the LLM inference layer. (This is effectively the authored-ResponsesAgent path's inference substrate — covered in the authored-agent research doc, not here.)

The **AI Playground** sits *before* tier 1 as a no-code prototyping surface that exports into either an authored agent (Databricks Apps / notebook) or hands off to the Supervisor API.

---

## 2. AI Playground — what it is and its ceiling

The AI Playground is a no-code chat surface to query LLMs and **prototype** tool-calling agents [#playground].

- **Tools you can attach:** `system.ai.python_exec` and other **UC Functions**; ad-hoc **Function definition**; **AI Search** index (RAG with citations); and **MCP** servers — both **managed Databricks MCP servers** and **external MCP servers**. So a custom MCP can be exercised here.
- **Model choice:** Yes — pick any "Tools enabled" model; compare models side-by-side.
- **System prompt:** Yes, configurable in the Playground (the export "uses the same model, system prompt, and tools you configured").
- **It is prototyping, not a runtime.** To get a servable/queryable artifact you **export**:
  - **Export to Databricks Apps (recommended):** installs the `agent-openai-agents-sdk` template — a deployable app with chat UI, MCP tool wiring, auth. This is the on-ramp to the **authored** path (you then own `agent_server/agent.py`).
  - **Create agent notebook (legacy):** generates a Python notebook defining a tool-calling **MLflow `ResponsesAgent`**, logs/registers it, and deploys to Model Serving.
  - **Hand off to Supervisor API:** if your Playground config has ≥1 tool and a Supervisor-compatible model, **Get code → Curl API** emits a **Supervisor API `POST .../mlflow/v1/responses`** call — "if you want Databricks to run the agent loop for you … instead of writing your own. Choose this option when you don't need custom Python logic between tool calls."

**Ceiling:** Playground itself is a sandbox. It is where the user has "been using" the brain, but it does not run autonomously and has no memory/eval of its own — it's a launcher into one of the three tiers above.

---

## 3. Agent Bricks Multi-Agent Supervisor — what it actually is

A **managed orchestration layer** (GA 2026-02-10) that coordinates specialist agents/tools, governed end-to-end by Unity Catalog [#supervisor-doc][#ga-blog][#arch-blog].

### Orchestration model
- Uses a **dynamic supervisor pattern** (Databricks' arch blog explicitly references **LangGraph's supervisor pattern**): an LLM coordinator analyzes the query, **delegates** to the right subagent/tool, and **synthesizes** results. Routing is LLM-driven, not a deterministic state machine you author.
- Each subagent/tool gets a **Description** field; the supervisor uses these descriptions to decide delegation. "Provide as much detail as possible to help improve its task delegation." This is the *primary* lever you have over routing — descriptions, not code.

### What it can connect to (subagent/tool types) [#supervisor-doc]
Genie Space; Published dashboard; **Knowledge Assistant** endpoint; **Model serving endpoint**; **UC function**; **UC table**; **UC volume**; **AI Search index** (Delta Sync only); nested **Supervisor Agent**; **Web search** (built-in, Foundation Model APIs, per-call user approval); **External MCP server** (via UC connection); **Custom MCP server** (hosted on a Databricks App); **Custom agent** (Databricks App). Up to **30 tools/agents** in the UI; **"You cannot use more than 20 agents in a single supervisor system."**

### Connecting a custom MCP server (the read-only Fabric tools)
- Host your MCP server as a **Databricks App** named with the **`mcp-` prefix** (required for Playground/AI-Gateway recognition); it must speak **streamable HTTP transport**; endpoint is `https://<app-url>/mcp` [#custom-mcp].
- Add it to the supervisor as a **Custom MCP server** subagent; end users need **`CAN_USE`** on the Databricks App [#supervisor-doc].
- Governed/monitored via **Unity AI Gateway → MCPs** tab; external MCPs use UC connections with managed OAuth [#mcp-overview].

### Customization limits (the crux for this use case)
- **System prompt:** *No exposed control.* You get an **Instructions** field ("specify guidelines for how the supervisor should respond") and a **Description** field. You do **not** author the supervisor's planning prompt. (Docs never expose a system-prompt knob; the arch blog confirms routing internals are unspecified.)
- **Planning loop:** *Not controllable.* The dynamic supervisor pattern is internal. No documented control over number of tool hops, reasoning depth, or the delegate→synthesize cycle. No "deterministic playbook" mechanism — you can only nudge via Instructions + per-tool Descriptions + ALHF examples.
- **Model choice:** *Not exposed* in the no-code Supervisor Agent. The docs and blogs never state which model powers it or let you pick one. (Contrast: the Supervisor API *does* let you pick the model — see §4.)
- **Memory:** *No session/long-term memory documented.* Uses **default storage** only for "temporary data transformations, model checkpoints, and internal metadata"; deleted with the agent. No cross-conversation memory primitive — a problem for "investigates user activity months back" continuity unless you supply state via tools/tables yourself.
- **Improvement = ALHF (Agent Learning on Human Feedback):** the sanctioned tuning surface. Add labeled questions + natural-language **Guidelines** in the **Examples** tab; SMEs review via shared link; "Databricks will retrain and optimize the supervisor from the new data." Powerful for quality, but it's *feedback-shaped*, not code/playbook-shaped.

### Governance / permissions (a genuine strength)
- **On-Behalf-Of (OBO) auth:** supervisor acts as a transparent proxy for the human user; **every tool execution validated against the user's Unity Catalog permissions** [#ga-blog].
- **Built-in access controls:** "end users only access the subagents and data they have access to." If the user lacks access to *all* subagents the supervisor ends the conversation; partial access → it routes away from inaccessible subagents [#supervisor-doc].
- Agent-level perms: **Can Manage** / **Can Query** (Can Query allows API + Playground querying).
- This aligns well with the **read-only absolute** constraint — but note: read-only must still be enforced by your MCP tools/UC grants; the supervisor doesn't add a read-only guarantee by itself, and **code execution** sub-tools can run arbitrary Python (see Warning in docs).

### Serving, programmatic invocation, autonomy
- Produces a **comprehensive serving endpoint** (Serverless Real-time Inference SKU). You can **query it programmatically** — Playground → **Get code** → **Curl/Python API** [#supervisor-doc].
- **SDK management (Beta):** `databricks-sdk` `w.supervisor_agents.*` to create the supervisor and add/update/remove tools programmatically (only `description` is updatable on a tool).
- **Autonomy:** The no-code Supervisor Agent is **interactive/on-demand** by design ("interact with the endpoint by submitting prompts in Playground or build a chat app"). The arch blog calls it intentionally **human-supervised**. There is **no built-in scheduler/watchdog** — Phase-2 autonomy would require you to drive the endpoint from a Databricks Job/Lakeflow or the Supervisor API's background mode.

### Evaluation / observability of the managed supervisor
- **MLflow** experiment tracking + tracing ("every interaction is tracked and measurable"). In Playground you can toggle an **AI Judge** and **Synthetic task generation** if AI-assistive features are enabled [#supervisor-doc][#ga-blog].
- Eval is real but **shallow vs. authored agents**: you don't define custom scorers/judges in code or wire a rich offline eval harness from inside the no-code product. For ranked-hypothesis/confidence outputs you'd want bespoke graders — that pushes toward authored eval (MLflow `genai.evaluate` + custom judges) or the Supervisor API + your own eval.

### Cost model
- Billed under **Serverless Real-time Inference** SKU; **"billed for the use of the Supervisor plus all charges of all agents used."** Serverless = pay per active DBU. Agent Bricks answers had a promo of **$0.075/answer**. Underlying tool compute billed separately (Genie = serverless SQL; UC functions = serverless general; AI Search = vector-search pricing; custom MCP = Databricks Apps pricing) [#mcp-overview][#pricing]. **Cost-sensitivity caveat:** the supervisor LLM loop is opaque (no `temperature`/token control surfaced), so per-investigation cost is harder to bound than an authored loop where you control hops, model, and caching.

---

## 4. Supervisor API (Beta) — the managed-but-controllable middle (most relevant tier)

This is the option that most directly fits a cost-sensitive, governable, programmatic, partly-autonomous investigator while still **not** making you write the agent loop [#supervisor-api].

- **What it is:** An **OpenResponses-compatible** endpoint `POST ai-gateway/mlflow/v1/responses`. You send `model` + `tools` + `instructions` in one request; **Databricks runs the agent loop** (repeatedly calls the model, selects+executes tools, synthesizes the answer).
- **Model choice — yes, and it's in-tenant Claude:** Supported models are exactly the in-tenant Claude family: `databricks-claude-opus-4-6`, `-opus-4-5`, `-opus-4-1`, `-sonnet-4-6`, `-sonnet-4-5`, `-sonnet-4`, `-haiku-4-5`. **"Change this field to switch providers without changing the rest of your code."** This is the reasoner the project wants (in-tenant Databricks Claude), with cost control via model tier (Haiku for cheap passes, Opus for hard hypotheses).
- **System prompt — yes:** `instructions` parameter = "a system prompt to guide the supervisor's behavior." So unlike the no-code product, you *do* control the prompt → enables deterministic-ish playbook framing.
- **Tools (hosted):** `genie_space`, `dashboard`, `uc_function`, `table`, `knowledge_assistant`, `serving_endpoint`, `web_search`, `vector_search_index`, `volume`, `app`, `uc_connection`, plus **client-side `function`** tools your code executes. System-managed connectors for GitHub/Google Drive/Atlassian/SharePoint/Glean.
- **MCP caveat (important):** For `uc_connection` you use an **external MCP server** connection or a `system_ai_agent_*` connector. **"Custom MCP servers on Apps are NOT supported"** by the Supervisor API. So if your read-only Fabric tools are a *custom MCP App*, the Supervisor API can't call them as `uc_connection` — you'd either (a) expose them via an **external MCP server + UC connection**, (b) wrap them as a `serving_endpoint`/`app` ResponseAgent, (c) register them as **UC functions**, or (d) run them as **client-side `function` tools**. (The *no-code* Supervisor Agent *does* support custom MCP Apps — a real divergence between the two managed tiers.)
- **Autonomy — yes (background mode):** `background=True` returns a response ID immediately; poll with `responses.retrieve()`. Max **30-min** runtime per background request; **no streaming in background**; **no durable/exactly-once** execution. Good for "investigate months of activity" long runs; for Phase-2 watchdog you'd still trigger it from a Job/schedule. MCP tool calls in background require **explicit user approval** (`mcp_approval_request`).
- **Memory — stateless by design:** "The Supervisor API doesn't store conversation state between requests." You pass full history each call. So *you* own memory (e.g., persist prior investigations in a UC table and re-inject) — same as you'd do in an authored agent. Background responses retained ≤30 days.
- **Governance:** Runs the loop **with the caller's credentials**; tools respect the caller's **UC permissions**. From a Databricks App you choose app-SP vs user (OBO) authorization. Routed through **Unity AI Gateway** → inference tables, rate limits, fallbacks apply (usage tracking not yet in Beta).
- **Observability:** Pass `trace_destination` (catalog/schema/table_prefix) to write **full agent-loop OpenTelemetry traces to UC tables**; `return_trace` to get the trace inline; MLflow distributed tracing to stitch app + loop traces.
- **Code execution:** Built-in **sandboxed serverless** Python/SQL/shell, **no internet egress**, only reads `table` tools you declared — relevant for read-only posture (no exfil path) but also means the model can run code you didn't author.
- **No `temperature`/inference-param control** ("The server manages these internally") — a mild loss of determinism vs. a fully authored loop.

---

## 5. Honest pros / cons vs. an authored ResponsesAgent

### When the **no-code Supervisor Agent** is *sufficient*
- Phase-1 **conversational, on-demand** Q&A where a human is in the loop.
- You're happy delegating across a few Genie spaces + your Fabric tools and letting the LLM route.
- You want **ALHF** + UC/OBO governance + a turnkey endpoint and chat UI **without writing code**.
- Eval needs are light (Playground AI-Judge + MLflow tracing suffice).
- You can tolerate opaque cost/planning and no custom memory.

### When you **outgrow** the no-code Supervisor Agent
- **Deterministic playbooks:** investigator needs a fixed telemetry-fallback order (source A → B → C), ranked-hypothesis scaffolding, explicit confidence scoring. The no-code supervisor only gives Instructions/Descriptions/ALHF — no authored control flow. **Outgrown.**
- **Memory across months/sessions:** no built-in long-term memory. **Outgrown** (unless you bolt on UC-table memory via tools).
- **Autonomy (Phase 2 watchdog):** no scheduler; interactive-first. **Outgrown** (need Jobs/Lakeflow trigger; Supervisor API background mode is the bridge).
- **Model choice / cost shaping:** no exposed model selection in the no-code product. **Outgrown.** (Supervisor API fixes this.)
- **Rich eval:** custom judges/scorers for hypothesis quality — need authored MLflow eval. **Outgrown.**

### Authored ResponsesAgent — what you gain (and pay for)
- **Full control:** your own planning loop (LangGraph etc.), deterministic playbooks, custom memory store, model choice, per-hop cost control, custom MLflow judges, custom guardrails for read-only.
- **Same governance substrate available:** ResponsesAgent still deploys on Model Serving / Databricks Apps with UC + OBO + MCP wiring + MLflow tracing — you don't lose governance by authoring.
- **Cost:** more engineering; you own the loop, retries, eval harness, and ops. (Covered fully in the authored-agent research doc.)

### Middle ground (likely best fit): **Supervisor API**
- Keeps the managed agent loop (less code, faster) **but** gives model choice (in-tenant Claude), `instructions` system prompt, programmatic + background invocation, UC/OBO governance, and trace-to-UC eval hooks.
- You still own memory (stateless API) and you must route custom MCP tools through a supported channel (external-MCP UC connection / serving_endpoint / UC functions / client-side functions), since custom-MCP-Apps aren't accepted.
- Lacks: deterministic inter-tool Python logic ("choose this when you don't need custom Python logic between tool calls"), `temperature` control, durable execution.

---

## 6. Recommendation lean for THIS use case

For the Fabric/PBI capacity **investigator** (read-only, cost-sensitive, governable; Phase-1 conversational, Phase-2 autonomous watchdog; ranked hypotheses + confidence + telemetry fallback; multi-month lookback):

- **Prototype in AI Playground** to validate the Fabric MCP tools + Claude model + prompt quickly. Cheap, no commitment.
- **Do not anchor on the no-code Supervisor Agent as the production brain.** It's excellent for a governed Phase-1 *demo*, but its no-model-choice, no-system-prompt, no-memory, no-deterministic-playbook, interactive-only profile means you'll outgrow it precisely on this agent's defining features (playbooks, confidence scoring, autonomy, months-back memory).
- **Strongest managed landing zone = Supervisor API (Beta):** in-tenant Claude model choice (cost shaping Haiku↔Opus), `instructions` system prompt for playbook framing, programmatic + **background** invocation for autonomy, UC/OBO governance, and trace-to-UC for eval — without hand-writing the agent loop. Caveat: resolve the **custom-MCP routing gap** (expose Fabric tools as external-MCP UC connection, UC functions, a ResponseAgent serving_endpoint, or client-side `function` tools) and own memory yourself (UC table re-injection).
- **Graduate to an authored ResponsesAgent** when you need deterministic logic *between* tool calls (strict fallback ordering, structured hypothesis ranking), custom eval judges, durable/exactly-once runs, or `temperature`/loop control — i.e., the full Phase-2 watchdog. Both Supervisor API and authored agents share the same UC/OBO/MLflow/Apps governance substrate, so this is an evolution, not a rewrite of governance.

**One-line lean:** Start managed (Playground → Supervisor API), keep the authored ResponsesAgent as the deliberate upgrade once deterministic playbooks, custom eval, and durable autonomy become hard requirements.

---

## Sources (#docs)

- [#supervisor-doc] Use Supervisor Agent to create a coordinated multi-agent system — MS Learn (Azure Databricks), ms.date 2026-06-29: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-bricks/multi-agent-supervisor
- [#supervisor-api] Supervisor API (Beta) — MS Learn, ms.date 2026-06-29: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-bricks/supervisor-api
- [#playground] Get started: Query LLMs and prototype AI agents with no code (AI Playground) — MS Learn, ms.date 2026-06-01: https://learn.microsoft.com/en-us/azure/databricks/getting-started/gen-ai-llm-agent
- [#mcp-overview] Model Context Protocol (MCP) on Databricks — MS Learn, ms.date 2026-06-29: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/
- [#custom-mcp] Host your own MCP server (Databricks Apps) — MS Learn, ms.date 2026-06-29: https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/custom-mcp
- [#ga-blog] Agent Bricks Supervisor Agent is Now GA (GA 2026-02-10) — Databricks Blog: https://www.databricks.com/blog/agent-bricks-supervisor-agent-now-ga-orchestrate-enterprise-agents
- [#arch-blog] Supervisor Agent Architecture: Orchestrating Enterprise AI at Scale — Databricks Blog: https://www.databricks.com/blog/multi-agent-supervisor-architecture-orchestrating-enterprise-ai-scale
- [#mcp-blog] Accelerate AI Development with Databricks: MCP and Agent Bricks — Databricks Blog: https://www.databricks.com/blog/accelerate-ai-development-databricks-discover-govern-and-build-mcp-and-agent-bricks
- [#author-agent] Author an AI agent and deploy it on Databricks Apps — docs.databricks.com: https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent
- [#pricing] Agent Bricks pricing / Databricks pricing (Serverless Real-time Inference SKU, $0.075/answer promo): https://www.databricks.com/product/pricing/agent-bricks
- [#sdk] Supervisor Agents SDK reference (databricks-sdk-py, Beta): https://databricks-sdk-py.readthedocs.io/en/latest/workspace/supervisoragents/supervisor_agents.html

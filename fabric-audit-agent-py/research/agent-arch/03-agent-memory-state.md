# Agent Memory & State on Databricks — for the BI Fabrics Audit Agent

**Scope:** Memory and state options for a READ-ONLY Fabric/PBI capacity *investigator* agent running on Databricks (in-tenant Databricks Claude as reasoner). The agent is read-only on the Fabric estate but MAY persist its OWN memory/state to its own store.

**Use case shape:**
- **Phase 1** = on-demand conversational investigator → needs **session/conversation continuity**.
- **Phase 2** = autonomous watchdog (scheduled, proactive) → needs **cross-run state** so it can say "this user's pattern shifted vs last month / we already flagged this."
- Must **investigate months back** and **remember prior investigations/findings** → needs **long-range investigation history** + **semantic recall**.

Research date: 2026-06-29. Sources are current as of June 2026 (docs `ms.date: 2026-06-23`; DAIS 2026 announcements June 16, 2026).

---

## 0. TL;DR recommendation for THIS agent

A **three-tier memory stack, all on Lakebase + Unity Catalog**:

| Tier | Need | Store | Mechanism |
|---|---|---|---|
| **Short-term / session** | (a) conversation continuity | **Lakebase (Postgres OLTP)** | LangGraph checkpointer keyed by `thread_id`; passed via `ResponsesAgent` `custom_inputs.thread_id` |
| **Long-term findings history** | (b) long-range investigation history + (c) cross-run watchdog state | **Delta tables in Unity Catalog** (durable, queryable rollups of every investigation + flagged findings) | Agent writes a findings row per run; watchdog reads "last month vs this month" from Delta |
| **Semantic recall** | retrieve "have we seen this pattern / did we already flag this?" | **Lakebase Search (`lakebase_vector` + `lakebase_text`)** OR **Mosaic AI Vector Search** over the findings notes | Hybrid vector + BM25 recall of prior findings/notes |

Rationale: Lakebase is the **Databricks-blessed agent state store** and gives you low-latency session memory + (with Lakebase Search) semantic recall in **one Postgres engine**, governed by Unity Catalog. Delta gives you the durable, auditable, time-series **findings history** the watchdog reasons over across months and across scheduled runs. See §6 for the full mapping and §7 for the build decision (Apps vs Model Serving).

---

## 1. Native Databricks "agent memory" feature (2025–2026)

Databricks now treats agent memory as a **first-class, documented capability**, not just "bring your own Postgres." Two layers exist:

### 1a. Documented memory built on Lakebase (GA-track, available now)
- **"AI agent memory"** docs page exists for both **Databricks Apps** and **Model Serving** deployment targets. Memory "lets AI agents remember information from earlier in the conversation or from previous conversations… Use Databricks **Lakebase**, a fully-managed Postgres OLTP database, to manage conversation state and history." [docs: stateful-agents]
- Split into **short-term** (single session, via thread IDs + checkpointing) and **long-term** (extract & store key insights across sessions; build a user knowledge base over time). You can implement **either or both** in the same agent. [docs: stateful-agents, state-management]
- Shipped as **prebuilt app templates** you clone:
  - `agent-langgraph-advanced` — LangGraph + **built-in checkpointing with Lakebase**, thread-based conversation context **and persistent user insights across sessions**. [github: databricks/app-templates]
  - `agent-openai-advanced` — OpenAI Agents SDK + Lakebase for durable state / automatic conversation history.
- **Model Serving** path is **Public Preview**; docs explicitly recommend **Databricks Apps for new use cases** ("full control over agent code, server configuration, and deployment workflow"). [docs: stateful-agents-model-serving]

### 1b. Managed **Agent Memory Service** (announced DAIS 2026, June 16 2026)
- Part of **Agent Bricks**' "Context" pillar. "Developers building agents can now **connect their agents to managed memory on Databricks, powered by Lakebase under the hood**, allowing agents to manage their own context, session history, and persist them across sessions and **eventually across agents** as well." [blog: agent-bricks-dais-2026]
- This is a higher-level *managed* abstraction over the same Lakebase substrate. GA/preview status not explicitly stated as of the announcement. **Watch this** — if/when GA, it may remove the need to hand-roll the LangGraph checkpointer wiring. For now, the **documented Lakebase + checkpointer templates (§1a) are the concrete, buildable path.**

> Takeaway: There IS a native memory story, and it standardizes on **Lakebase**. The managed Agent Memory Service is the future-facing convenience layer; the templates are what you build on today.

---

## 2. Session / conversation memory in an authored agent

This covers requirement **(a) conversation continuity** for the Phase-1 investigator.

### 2a. `ResponsesAgent`, `custom_inputs`, and thread/conversation id
- Databricks recommends authoring with the **MLflow `ResponsesAgent`** interface; MLflow auto-infers a Databricks-compatible signature. [docs: author-agent]
- `ResponsesAgent` natively supports **`custom_inputs`** and **`custom_outputs`** — the channel for non-chat fields like `session_id`, `client_type`, `thread_id`, `checkpoint_id`. Access via `request.custom_inputs`. [docs: stateful-agents-model-serving, agent-legacy-schema]
- **Thread/session keying pattern** (exact, from docs):
  ```python
  # In predict_stream / predict
  custom_inputs = request.custom_inputs or {}
  thread_id = custom_inputs.get("thread_id", str(uuid.uuid4()))  # new thread if absent
  checkpoint_id = custom_inputs.get("checkpoint_id")             # optional, for branching
  # Return the thread_id back so the client can continue the conversation:
  return ResponsesAgentResponse(output=outputs, custom_outputs={"thread_id": thread_id})
  ```
- **Client passes thread_id** to a deployed endpoint via `extra_body`:
  ```python
  response = client.responses.create(
      model=endpoint,
      input=[{"role": "user", "content": "..."}],
      extra_body={"custom_inputs": {"thread_id": thread_id}},
  )
  ```
- **Auto-population:** Clients that pass MLflow **`ChatContext`** (the AI Playground, the Review app) **automatically supply conversation id and user id** for short-term/long-term memory use cases. So in Playground you get continuity for free; in your own UI you manage `thread_id` yourself. [docs: stateful-agents-model-serving]

### 2b. Short-term memory = LangGraph checkpointer on Lakebase
- The mechanism is a **LangGraph checkpointer** backed by Lakebase Postgres, keyed by `thread_id`. Each turn writes a checkpoint; the next turn with the same `thread_id` resumes with prior-turn awareness.
- Docs show a `CheckpointSaver(instance_name=LAKEBASE_INSTANCE_NAME)` context manager used to build the graph and to read `graph.get_state_history(config)` where `config = {"configurable": {"thread_id": thread_id}}`. (This is Databricks' wrapper over LangGraph's Postgres checkpointer.) [docs: stateful-agents-model-serving]
- **Time travel / branching (Model Serving short-term memory):** you can `graph.get_state_history(...)` to list checkpoints (each has `checkpoint_id`, `timestamp`, `next_nodes`, `message_count`) and `graph.update_state(config, values=...)` to fork. Passing a `checkpoint_id` in `custom_inputs` resumes/branches from that point; **the `thread_id` stays the same across a branch.** Useful for "what if we re-investigate from the point before X happened." [docs: stateful-agents-model-serving]

### 2c. Long-running investigations (matters for a deep audit)
- **Databricks Apps enforce an ~300s HTTP connection timeout.** Long investigations need **background execution** via `LongRunningAgentServer` from `databricks-ai-bridge` (in the advanced templates):
  - `background=true` in the request body → returns a response ID immediately, runs async.
  - `GET /responses/{id}` → fetch final result or attach to an in-progress stream.
  - **Resumable streaming:** every SSE has a `sequence_number`; reconnect with `starting_after=N`.
  - `TASK_TIMEOUT_SECONDS` env var caps background task duration (default **1 hour**), independent of the HTTP timeout. [docs: stateful-agents]
- Relevance: a "investigate this user's last 3 months" run can exceed 300s — design Phase 1 to use background mode.

---

## 3. Long-term memory options

This covers requirement **(b) long-range investigation history** and **(c) cross-run watchdog state**.

### 3a. Lakebase / Databricks OLTP Postgres — low-latency agent state
- **What it is:** "a fully managed Postgres database integrated into the Databricks platform… automatic scaling, instant branching, and **native Unity Catalog integration**." Now branded **Lakebase Autoscaling** (provisioned instances are being upgraded to it). [docs: oltp/projects/index]
- **Explicit AI use case:** "Use Lakebase as an online feature store for ML models, or **as a state store for AI agents.**" / "Persist your AI agent's chat sessions and messages in Lakebase so users can resume conversations and your agent can reason over prior turns **across deploys**." [docs: oltp/projects/index; product/lakebase]
- **Architecture:** **decoupled compute and storage** for independent scaling; **scale-to-zero** (suspend inactive computes to cut cost); **read replicas**; **instant restore / point-in-time branching** (new branch from any point in your history window); **HA failover**. [docs: oltp/projects/index]
- **Lakehouse integration (key for this agent):**
  - **Synced tables:** sync Unity Catalog Delta tables → Postgres for low-latency reads (e.g., serve a precomputed Fabric capacity baseline to the agent).
  - **Lakebase Change Data Feed (Public Preview):** store row-level Postgres changes back as **Unity Catalog Delta tables** for downstream pipelines, **audit**, and external consumers — i.e., your agent's writes become auditable Delta automatically.
  - **Register in Unity Catalog** for unified governance.
- **Latency/cost:** Lakebase is positioned for "real-time OLTP" / "low-latency." The product page does not publish ms/QPS numbers; usage-based pricing, "only pay for the products you use," scale-to-zero. (No DBU/CU figures published on the public pages — confirm via the Databricks pricing page for your tenant.) Lakebase Search benchmarks give a sense of scale (§3c).
- **Maps to:** (a) session checkpoints, (c) low-latency watchdog working state ("currently-open investigations," "last-seen counters per user"). Good for hot, frequently-updated, point-lookup state.

### 3b. Delta tables in Unity Catalog — durable rollups / findings history
- Delta in UC is the natural home for the **durable, append-only, queryable investigation history**: one row per investigation/finding with `user_id`, `capacity_id`, `run_ts`, `window`, `metrics_json`, `verdict`, `flagged`, `confidence`, `provenance`. This is what the watchdog scans to compute **month-over-month deltas** and to answer "did we already flag this?"
- Why Delta (not only Lakebase) for history: it's columnar/analytical, time-travel-capable, cheap at rest, governed by Unity Catalog, and is the same surface your BI/SQL tooling already queries. Lakebase CDF (§3a) can even *feed* this automatically from the agent's Postgres writes.
- The Databricks memory-scaling guidance explicitly endorses this layering: **episodic** records (raw run logs / tool-call trajectories) periodically **distilled** into **semantic** memories (compressed rules/patterns), with **consolidation** pipelines that "remove duplicates, prune outdated information, and resolve conflicts." [blog: memory-scaling-ai-agents]
- **Maps to:** (b) long-range history and (c) cross-run watchdog state (the canonical, auditable record).

### 3c. Semantic recall — Lakebase Search vs Mosaic AI Vector Search
For "have we seen this pattern before / fetch the relevant past finding," you need **semantic + keyword retrieval** over the findings/notes corpus.

**Option A — Lakebase Search (Beta, on AWS + Azure; DAIS 2026):**
- Two native Postgres extensions: **`lakebase_vector`** (vector search, RaBitQ quantization, ~32x compression, scales to >1B vectors, NVMe-cached working set) and **`lakebase_text`** (native BM25). **Hybrid search in a single SQL query** via reciprocal rank fusion. [blog: announcing-lakebase-search]
- **Agent-native design:** consolidates "the entire agent loop — retrieval, reasoning, action, **memory** — into one backend. Agents can write new learnings to memory on one turn, and need that exact data fully indexed and searchable on the next." This **operational** (read-after-write) property is exactly what a memory store needs — vs a read-only snapshot index.
- Benchmarks (LAION-100M, 768-dim): Recall@10 0.955, **P99 30ms**, 51 QPS. Tiered cost: RAM ~$3,000/TB/mo, NVMe ~$100/TB/mo, object storage ~$20/TB/mo.
- **Best fit here:** keeps session memory AND semantic recall in **one Lakebase Postgres** — fewer moving parts, immediate searchability of just-written findings.

**Option B — Mosaic AI Vector Search (GA):**
- Built-in, governed vector search over a **Delta source table**; index stays synced (Continuous Sync = near-real-time but provisions a streaming cluster, costs more; or triggered sync). [docs: vector-search]
- `VectorSearchRetrieverTool` binds an index to the LLM as a tool-calling retriever. [docs: unstructured-retrieval-tools]
- **Best fit when** your findings already live in Delta and you want the mature, fully-managed retriever tool with auto-embedding. Slight write-to-searchable lag vs Lakebase Search's read-after-write.
- **Maps to:** semantic recall for both (a) (recall relevant past context mid-conversation) and (b)/(c) (find prior similar findings before re-flagging).

---

## 4. How memory persists across scheduled runs (the Phase-2 watchdog)

The watchdog runs on a **Lakeflow Job** schedule (Scheduled trigger: simple interval or cron). Each run is a fresh process, so **all "what did we know last time" state must live in a store outside the run**:

1. **Durable findings history → Delta in UC.** Each scheduled run appends rows (per-user/per-capacity metrics + verdicts). To detect "pattern shifted vs last month," the run queries the prior window's rows from this Delta table. This is the backbone of cross-run continuity and is fully auditable. [§3b]
2. **Hot cross-run state → Lakebase.** "Already-flagged" dedupe keys, last-seen counters, open-investigation status — point lookups/updates the watchdog reads at the start of each run and writes at the end. Lakebase's scale-to-zero keeps idle cost low between scheduled runs; data persists "across deploys." [§3a]
3. **Semantic dedupe → Lakebase Search / Vector Search.** Before raising a finding, embed it and search prior findings: if a near-duplicate exists and is still fresh, suppress/escalate instead of re-flagging ("we already flagged this"). [§3c]
4. **Conversational continuity is NOT needed in the watchdog** (no human turn-taking), so the LangGraph checkpointer/`thread_id` machinery is a Phase-1 concern; Phase-2 relies on Delta + Lakebase tables. If you want the watchdog to *resume a multi-step investigation*, you can still use a `thread_id` per investigation and LangGraph checkpoints in Lakebase.

**Freshness/staleness caveat (called out by Databricks):** "an agent that learned last quarter's schema may keep querying tables that have since been renamed." Store **freshness signals + confidence + provenance** alongside each memory and let consolidation prune/resolve conflicts. [blog: memory-scaling-ai-agents] For a capacity auditor this is critical — Fabric capacities, users, and workspaces change month to month.

---

## 5. Patterns, latency, cost, governance

- **Short-term vs long-term write policy:**
  - *Short-term*: write **every turn** (checkpoint) — cheap, ephemeral-ish, keyed by `thread_id` in Lakebase.
  - *Long-term*: **don't** persist every turn. Use **distillation** — periodically compress episodic run logs into semantic findings; run **consolidation** to dedupe/prune/resolve conflicts. Track **provenance** (which memories influenced a response), **confidence**, **freshness**. [blog: memory-scaling-ai-agents]
- **Latency:** Lakebase = OLTP/low-latency point ops (session checkpoints, counters). Lakebase Search P99 ~30ms at 100M vectors. Delta = analytical scans (history rollups), not point-latency-critical. Use synced tables to serve a Delta baseline to the agent at Postgres latency.
- **Cost levers:** Lakebase **scale-to-zero** between scheduled runs; tiered storage (RAM/NVMe/object) for Lakebase Search; Vector Search Continuous Sync costs more (streaming cluster) than triggered sync — for a monthly/weekly watchdog, **triggered/scheduled sync is cheaper and sufficient**.
- **Governance (strong fit for a read-only audit agent):**
  - Lakebase **registers in Unity Catalog**; UC governs the agent's own memory tables.
  - Memory-scaling guidance: UC **row-level security, column masking, and ABAC extend to memory entries themselves** — and personal vs organizational memory can be scoped separately. [blog: memory-scaling-ai-agents]
  - **Lakebase CDF** turns the agent's Postgres writes into auditable Delta — every memory mutation is traceable. This satisfies "read-only on Fabric, but the agent persists its own governed, audited state."

---

## 6. Mapping options → the three required capabilities

| Capability | Primary store | Secondary | Mechanism |
|---|---|---|---|
| **(a) Conversation continuity** (Phase 1) | **Lakebase** (LangGraph checkpointer, `thread_id`) | Vector/Lakebase Search for mid-chat recall of past findings | `ResponsesAgent.custom_inputs.thread_id` ↔ `custom_outputs.thread_id`; checkpoint per turn; time-travel/branch via `checkpoint_id` |
| **(b) Long-range investigation history** (months back) | **Delta tables in UC** (append-only findings/runs) | **Lakebase Search / Vector Search** for semantic lookup over notes | Distillation of episodic logs → semantic findings; query prior windows |
| **(c) Cross-run watchdog state** (Phase 2) | **Lakebase** (hot: flagged-keys, counters, open investigations) + **Delta** (durable history) | **Lakebase Search / Vector Search** for dedupe | Lakeflow Job (cron) reads prior state at start, writes at end; scale-to-zero between runs |

---

## 7. Build decision & recommended stack

**Deployment target:** Build the Phase-1 conversational agent on **Databricks Apps** (Databricks' recommendation for new use cases; full control over code/server/deploy; Lakebase auth handled automatically; supports background execution for long investigations). Reserve **Model Serving + Lakebase checkpoints** only if you need an MLflow-served endpoint with time-travel; note it's **Public Preview** and docs steer new work to Apps. [docs: stateful-agents, stateful-agents-model-serving]

**Authoring:** MLflow **`ResponsesAgent`**; carry `thread_id`/`session_id`/`user_id` through `custom_inputs`/`custom_outputs`. Use **`ChatContext`** auto-population in Playground/Review app.

**Memory stack (recommended):**
1. **Lakebase (Postgres)** — session checkpoints (LangGraph) + hot watchdog state. *The Databricks-native agent state store.*
2. **Delta tables in Unity Catalog** — durable, auditable findings/run history; the watchdog's "last month vs this month" source of truth. (Optionally fed by **Lakebase CDF**.)
3. **Lakebase Search** (preferred, one-engine, read-after-write) **or Mosaic AI Vector Search** (if findings stay in Delta and you want the GA managed retriever tool) — semantic recall + dedupe of prior findings.
4. **Watch the managed Agent Memory Service** (Agent Bricks, DAIS 2026) — may later replace hand-rolled checkpointer wiring.

**Why this beats alternatives:** Lakebase is purpose-built and documented for agent state; pairing it with Delta gives you the analytical, time-travel, governed history a *capacity auditor* must reason over across months; Lakebase Search adds semantic recall without a second system; and everything is governed by Unity Catalog with provenance/audit via CDF — exactly what a read-only-on-Fabric, persists-own-state agent needs.

---

## Sources (#docs)

1. AI agent memory (Apps) — https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/stateful-agents
2. AI agent memory (Model Serving, Public Preview; time-travel, `custom_inputs.thread_id`, checkpointer code) — https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/stateful-agents-model-serving
3. Agent state and memory (Lakebase OLTP; deployment targets) — https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/state-management
4. Lakebase Postgres overview (Autoscaling, branching, UC integration, synced tables, CDF, agent state use case) — https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/
5. Author an AI agent and deploy it on Databricks Apps (`ResponsesAgent`, `custom_inputs`/`custom_outputs`) — https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/author-agent
6. Legacy input/output agent schema (custom_inputs fields) — https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/agent-legacy-schema
7. Lakebase product page (decoupled compute/storage, branching, resume across deploys) — https://www.databricks.com/product/lakebase
8. Announcing Lakebase Search (lakebase_vector/lakebase_text, hybrid, benchmarks, agent-native read-after-write; Beta AWS+Azure) — https://www.databricks.com/blog/announcing-lakebase-search-agent-native-retrieval-built-lakebase-postgres
9. Memory scaling for AI agents (episodic/semantic, distillation, consolidation, provenance/freshness/confidence, UC governance of memory) — https://www.databricks.com/blog/memory-scaling-ai-agents
10. Agent Bricks DAIS 2026 (managed Agent Memory Service on Lakebase) — https://www.databricks.com/blog/agent-bricks-dais-2026
11. Databricks AI Search / Mosaic AI Vector Search (GA, Delta sync, VectorSearchRetrieverTool) — https://learn.microsoft.com/en-us/azure/databricks/vector-search/vector-search
12. Connect agents to unstructured data (VectorSearchRetrieverTool) — https://docs.databricks.com/aws/en/generative-ai/agent-framework/unstructured-retrieval-tools
13. Run jobs on a schedule / triggers (Lakeflow Jobs, cron) — https://learn.microsoft.com/en-us/azure/databricks/jobs/triggers
14. App templates (agent-langgraph-advanced, agent-openai-advanced) — https://github.com/databricks/app-templates
15. Lakebase Agent Memory template (devhub) — https://www.databricks.com/devhub/templates/lakebase-agent-memory

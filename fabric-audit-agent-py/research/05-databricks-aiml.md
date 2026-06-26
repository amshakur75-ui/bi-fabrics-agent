# 05 — Databricks AI/ML Platform (beyond serving Claude)

Research focus: hardening, evaluating, and adding conversational/dashboard surfaces to the **bi-fabrics-audit-agent** (READ-ONLY Fabric/PBI capacity audit agent on Databricks, MCP server + Mosaic AI agent).

Scope note — these are EXPLICITLY excluded per the brief and NOT re-covered here: capacity telemetry; OAuth/scopes/tenant settings; Fabric/PBI REST; Databricks Apps; custom/managed MCP basics; Mosaic AI Agent Framework basics; serving Claude (endpoint names, `get_open_ai_client`); Asset Bundles; UC/secrets; Kusto; `databricks-sdk`.

Date of research: 2026-06-22. Docs are Azure Databricks (`learn.microsoft.com/azure/databricks`) unless noted; AWS/GCP equivalents exist at the parallel paths.

Naming changes to be aware of:
- **Mosaic AI Vector Search → "Databricks AI Search"** (renamed; SDK class now `AISearchClient`, formerly `VectorSearchClient`).
- **Mosaic AI Gateway → "Unity AI Gateway"** (new Beta version in left nav governs LLM endpoints, agents, AND MCP servers; a "previous" version still governs serving endpoints).

---

## 1. MLflow 3 — Tracing / GenAI Observability

**TITLE:** MLflow Tracing — GenAI observability
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/
**Summary:** MLflow 3 Tracing is end-to-end observability for GenAI apps/agents. Built on **OpenTelemetry**, it records inputs, outputs, intermediate steps, latency, **cost**, and **token usage** at every step. Lightweight `mlflow-tracing` package captures traces from 20+ GenAI libraries plus custom logic, dev → prod. Managed MLflow on Databricks adds UC governance (prompts, apps, traces, datasets, models all governed in Unity Catalog) and the new **`LoggedModel`** abstraction for versioning GenAI apps with full lineage (run ↔ dataset ↔ metrics ↔ traces). Genie Code can analyze trace data in natural language.
**Exact identifiers:**
- Automatic instrumentation: `mlflow.openai.autolog()`, `mlflow.langchain.autolog()`, `mlflow.anthropic.autolog()`, `mlflow.bedrock.autolog()`, plus 20+ library integrations (one `autolog()` per library).
- Manual tracing: `@mlflow.trace` decorator; `mlflow.start_span()` context manager; `mlflow.set_experiment(...)`.
- The lightweight package is `mlflow-tracing` (vs full `mlflow[databricks]>=3.1`).
- Trace primitives: spans with inputs/outputs/attributes; `mlflow.search_traces()` (same filter syntax used by monitoring `filter_string`).
- Session/user tags: `mlflow.trace.session` (groups traces into conversations — required for multi-turn judges).
**How it helps:** Instrument the audit agent's reasoning loop, MCP tool calls, and Fabric/PBI REST collectors. Every audit run becomes a trace tree: which capacity, which detectors fired, which MCP/REST calls, latency, **token cost per audit**. Critical for debugging silent collector failures and proving auditability/compliance of a read-only agent. Use `mlflow.anthropic.autolog()` to capture the Claude reasoner's calls without extra code.

**TITLE:** MLflow 3.0 — Build, Evaluate, and Deploy Generative AI with Confidence (blog)
**URL:** https://www.databricks.com/blog/mlflow-30-unified-ai-experimentation-observability-and-governance
**Summary:** Announces MLflow 3's unification of tracking + evaluation + observability + governance for GenAI, the `LoggedModel` object, and UC-centered governance of models/apps/prompts/datasets/traces.
**How it helps:** Strategic framing for "one platform to harden + evaluate + govern" the audit agent.

---

## 2. MLflow 3 — log_model / register_model / Unity Catalog Model Registry

**TITLE:** Log, load, and register MLflow models
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow/models
**Summary:** Standard MLflow Model packaging (flavors: pyfunc, sklearn, pytorch…) for batch (Spark UDF) or REST serving. MLflow 3 adds `LoggedModel` with its own metrics/params/lineage. With MLflow 3 the **default registry URI is `databricks-uc`**, so the registry IS the Unity Catalog model registry (three-level `catalog.schema.model` namespace, access control, lineage, cross-workspace discovery).
**Exact identifiers:**
- `mlflow.<flavor>.log_model(model, ...)`; `mlflow.pyfunc.log_model(...)`.
- `mlflow.<flavor>.load_model(modelpath)`; `mlflow.pyfunc.load_model(model_path)`; `model.predict(model_input)`.
- `mlflow.pyfunc.spark_udf(spark, model_path, env_manager="virtualenv")` for batch scoring.
- `mlflow.register_model("models:/{model_id}", "{registered_model_name}")` (MLflow 3) / `mlflow.register_model("runs:/{run_id}/{model-path}", "...")` (2.x).
- `mlflow.set_registry_uri("databricks-uc")`.
- URI schemes: `models:/{model_id}`, `runs:/{run_id}/{path}`, `models:/{name}/{version}`, `models:/{name}/{stage}`; UC volumes `dbfs:/Volumes/catalog/schema/volume/...`.
- `mlflow.pyfunc.get_model_dependencies(...)`; `mlflow.models.predict(...)` to validate before deploy; `mlflow.artifacts.download_artifacts(f"models:/{name}/{version}")`.
- Privileges to register: `USE CATALOG`, `USE SCHEMA`, `CREATE MODEL`/`CREATE FUNCTION` on the schema.
**How it helps:** Package the audit agent itself as a pyfunc/`ResponsesAgent` model and register it in UC (`catalog.schema.fabric_audit_agent`) → versioning, model aliases (`@champion`/`@challenger`), access control, lineage from audit logic to the serving endpoint. Enables controlled promotion of new detector/reasoner versions and rollback.

**TITLE:** Manage model lifecycle in Unity Catalog
**URL:** https://learn.microsoft.com/en-us/azure/databricks/machine-learning/manage-model-lifecycle/
**Summary:** UC model registry: centralized access control, auditing, lineage, discovery across workspaces; **model aliases** (mutable named pointers like `@champion`) replace legacy stages; tags; version management. Serving reads registered models as REST endpoints.
**Exact identifiers:** registered model = `catalog.schema.model`; aliases via `MlflowClient().set_registered_model_alias(name, alias, version)`; `models:/catalog.schema.model@champion`.
**How it helps:** Govern audit-agent versions with `@champion`/`@challenger` aliases; safely A/B a new reasoner; audit who deployed which version (compliance for a read-only governance tool).

---

## 3. Agent Evaluation — Scorers & LLM Judges (MLflow 3)

**TITLE:** Scorers and LLM judges
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/concepts/scorers
**Summary:** Scorers = the unified eval interface; they parse a **Trace**, run an assessment, and attach a `Feedback` to the trace. Return pass/fail, boolean, numeric, or categorical. Four tiers: **built-in judges** (minimal config), **custom LLM judges** (full), **code-based scorers** (deterministic business logic), **third-party scorers** (OSS frameworks). The SAME scorer is reused in dev eval and prod monitoring.
**Exact identifiers:**
- `mlflow.genai.evaluate(...)` runs scorers in dev.
- Import path: `from mlflow.genai.scorers import ...`.
- Judge model override: `Correctness(model="databricks:/databricks-gpt-5-mini")` — format `<provider>:/<model-name>` (e.g. `databricks:/databricks-gpt-oss-20b`).
- `make_judge(...)` (`from mlflow.genai import make_judge`) for custom-prompt judges with `feedback_value_type=Literal[...]`.
- `@scorer` decorator and class-based `Scorer` for code-based scorers.
- Judge alignment to human labels: see `align-judges`.
**How it helps:** Build an eval suite for the audit agent: a `Correctness` judge against a labeled set of "known capacity issues," `Guidelines` judges enforcing tone/format of recommendations, and **code-based scorers** asserting the agent is truly READ-ONLY (e.g. scorer that fails if any trace span shows a write/mutating REST verb). This is the safety net for a governance agent.

**TITLE:** Built-in LLM judges
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/concepts/judges/
**Summary:** Predefined, research-validated judges using Databricks-hosted LLMs for relevance, safety, groundedness, correctness. Some need ground-truth/expectations, others don't.
**Exact identifiers (scorer class names, `mlflow.genai.scorers`):**
- `Correctness` — output vs expected facts (needs expectations/ground truth).
- `RelevanceToQuery` — is the answer relevant to the request (no ground truth needed).
- `Safety` — harmful/unsafe content (no ground truth).
- `Guidelines` — pass/fail vs natural-language rules (e.g. `guidelines=["The response must be in English"]`).
- `RetrievalGroundedness` — is the answer grounded in retrieved context.
- `RetrievalRelevance` / `RetrievalSufficiency` — retrieved-context quality (RAG).
- Multi-turn: `ConversationCompleteness`, `UserFrustration`.
- `ScorerSamplingConfig(sample_rate=..., filter_string=...)`.
**How it helps:** `Safety` + `Guidelines` guard the agent's recommendations; `RetrievalGroundedness`/`RetrievalRelevance` validate any RAG over runbooks/Fabric docs (see Vector Search below); `UserFrustration` catches when conversational audit users get stuck.

**TITLE:** Code-based scorer examples
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/code-based-scorer-examples
**Summary:** Examples of deterministic `@scorer` functions (exact match, format validation, latency/cost thresholds, custom heuristics).
**How it helps:** Encode hard audit invariants: every recommendation must cite a capacity ID; severity must be one of an allowed enum; no PII leaked; bounded latency/cost per audit.

**TITLE:** What is AI Agent Evaluation? (blog)
**URL:** https://www.databricks.com/blog/what-is-agent-evaluation
**Summary:** Conceptual overview of agent evaluation methodology (quality dimensions, judges, human feedback loop).
**How it helps:** Justifies an eval-driven dev loop before shipping detector/reasoner changes.

---

## 4. Agent Monitoring (production monitoring of traces)

**TITLE:** Monitor GenAI apps in production
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/production-monitoring
**Summary (Beta):** Schedule MLflow 3 scorers to run automatically on a **configurable sample** of production traces; results attach as `Feedback`. Two-step pattern `.register()` then `.start()` for ALL scorer types (≤20 scorers per experiment). Same scorers as dev. Supports built-in, Guidelines, custom-prompt (`make_judge`), code-based, and multi-turn judges. Background monitoring; results visible 15–20 min later in Traces tab + monitoring dashboards.
**Exact identifiers / patterns:**
- `Safety().register(name="my_safety_judge").start(sampling_config=ScorerSamplingConfig(sample_rate=0.7))`.
- `ScorerSamplingConfig(sample_rate=..., filter_string="attributes.status = 'OK' AND attributes.timestamp_ms > ...")` (filter uses `mlflow.search_traces()` syntax).
- Multi-turn: `ConversationCompleteness().register(...).start(...)`, `UserFrustration()`; groups by `mlflow.trace.session`; conversation considered complete after 5 min idle, tunable via env var `MLFLOW_ONLINE_SCORING_DEFAULT_SESSION_COMPLETION_BUFFER_SECONDS`.
- UI: experiment **Judges** tab → **New LLM judge** → "Run on all future traces" + Sample rate + Filter string. (No custom-code judge via UI — template copy only.)
- **Constraints (production monitoring):** only `@scorer` decorator scorers (NOT class-based `Scorer`); must be defined/registered **from a Databricks notebook** (code is serialized for remote exec); functions must be **self-contained** (inline imports, no external refs, no import-requiring type hints). Pre-available pkgs: `databricks-agents`, `mlflow-skinny`, `openai`, Serverless env v2 packages.
- Prereqs: MLflow experiment with traces; serverless budget policy; **SQL warehouse ID** if traces are in UC.
- Best practice: `sample_rate=1.0` for safety/security checks; 0.05–0.2 for expensive judges.
**How it helps:** Continuously score live audit runs — e.g. 100% `Safety` + a read-only-invariant code scorer, lower-rate `Correctness` against periodically labeled data. Detects drift when Fabric/PBI APIs change and the agent starts hallucinating. Dashboards give an ops view of agent quality over time.

---

## 5. Review Apps & Human Feedback

**TITLE:** Evaluate and monitor AI agents (overview, links Review App + human feedback)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/
**Summary:** Eval/monitor builds on Tracing. Domain experts give feedback via an integrated **Review App**, producing labeled eval datasets; human annotations and `Feedback` attach to traces. Agent Evaluation SDK is in `mlflow[databricks]>=3.1` (databricks-agents).
**Exact identifiers:** human feedback / `dev-annotations`; `Feedback` objects; review-app collection of expert labels → eval dataset for `mlflow.genai.evaluate()` and judge alignment (`align-judges`).
**How it helps:** Send sampled audit findings to FinOps/capacity-admin SMEs in the Review App; their thumbs-up/down builds a labeled set to align custom judges (so "is this recommendation correct?" matches expert judgment) and to regression-test new detector versions.

---

## 6. Mosaic AI Vector Search / "Databricks AI Search"

**TITLE:** Databricks AI Search (formerly Vector Search)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/vector-search/vector-search  (canonical now /ai-search/ai-search)
**Summary:** Vector search engine in the platform; index built from a Delta table, governed by Unity Catalog. **HNSW** ANN with **L2** distance (normalize embeddings for cosine). Supports **hybrid keyword-similarity search** (BM25 + vectors fused via **Reciprocal Rank Fusion**, `rrf_param=60`), full-text search (Beta), filtering, reranking, ACLs, sync-selected-columns. Three embedding options + full-text option.
**Exact identifiers / config:**
- SDK class **`AISearchClient`** (formerly `VectorSearchClient`); package `databricks-vectorsearch`.
- Auth: `AISearchClient(workspace_url=..., service_principal_client_id=..., service_principal_client_secret=...)` (recommended for prod; ~100ms faster than PAT) or `personal_access_token=...`.
- Index types: **Delta Sync Index (Databricks-computed embeddings)**, **Delta Sync Index (self-managed embeddings)**, **Direct Vector Access Index** (manual upsert via REST), **Full-text index** (storage-optimized, BM25, no vectors).
- Query types: ANN (default), **HYBRID**, `query_type="FULL_TEXT"`.
- Endpoint types: **Standard** (~320M vectors @ dim 768; High-QPS preview) and **Storage-optimized** (>1B vectors @ 768, 10–20x faster indexing, ~250ms query latency, triggered sync only).
- Embedding model serving endpoints (Foundation Model APIs): e.g. `databricks-gte-large-en`, `databricks-bge-large-en` (via FMAPI). SQL function `vector_search(...)`.
- Requirements: UC-enabled, serverless compute, source table **Change Data Feed** enabled (standard endpoints), `CREATE TABLE` on the index schema; reserved column `_id`.
- Limits: 500 endpoints/workspace; 50 indexes/endpoint; 50 columns; dim ≤4096; query text ≤32764 chars; ANN ≤10,000 results, hybrid ≤200.
**How it helps:** Give the audit agent **RAG over governance knowledge** — Fabric/PBI capacity best-practice docs, internal runbooks, prior remediation tickets, the org's capacity policies. When the agent finds an overloaded capacity it can retrieve the matching remediation playbook and cite it. Hybrid search is ideal because capacity/SKU identifiers (e.g. `F64`, workspace GUIDs) are exact keywords that pure vector search misses. Pair retrieval with `RetrievalGroundedness`/`RetrievalRelevance` judges (section 3).

**TITLE:** Announcing Hybrid Search GA in Databricks AI Search (blog)
**URL:** https://www.databricks.com/blog/announcing-hybrid-search-general-availability-mosaic-ai-vector-search
**Summary:** Hybrid search GA — learned keyword index over the vector index; strong on SKUs/product keys/identifiers.
**How it helps:** Confirms hybrid is the right retrieval mode for identifier-heavy capacity/governance content.

**TITLE:** Create AI Search endpoints and indexes
**URL:** https://learn.microsoft.com/en-us/azure/databricks/vector-search/create-vector-search
**Summary:** How-to for `create_endpoint`, `create_delta_sync_index`, `create_direct_access_index`, choosing managed vs self-managed embeddings, columns-to-sync.
**Exact identifiers:** `AISearchClient.create_endpoint(...)`, `.create_delta_sync_index(...)`, `.create_direct_access_index(...)`, `.get_index(...).similarity_search(query_text=..., columns=[...], num_results=..., filters={...}, query_type="HYBRID")`.
**How it helps:** Concrete API to stand up the knowledge index that feeds the agent.

---

## 7. Feature Engineering / Feature Store in Unity Catalog

**TITLE:** Databricks Feature Store (Feature Engineering in Unity Catalog)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/
**Summary:** Central registry for features in UC — governance, lineage, point-in-time joins, cross-workspace discovery. Register feature tables + models in UC so the model auto-retrieves features at inference time (caller supplies only the primary key).
**Exact identifiers:**
- `FeatureEngineeringClient` (UC; `databricks-feature-engineering` pkg) — newer; `FeatureStoreClient` is the legacy client.
- `FeatureLookup`, `create_training_set(...)`, automatic feature lookup at serving time (batch from offline store, real-time from online store).
- Declarative features API (UC functions with aggregations/time windows); Materialized features API (scheduled batch materialization to online store via cron).
**How it helps:** Treat engineered capacity signals as governed features — e.g. rolling 7/30-day CU utilization, throttle-event counts, peak-to-average ratios, growth slope per capacity. Compute once, reuse across detectors and any ML scoring (e.g. an anomaly/forecast model). Point-in-time joins prevent leakage when training a "capacity-at-risk" classifier. Lineage shows which raw telemetry produced each feature — useful for an auditable governance agent.

**TITLE:** Databricks Online Feature Stores
**URL:** https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/online-feature-store
**Summary:** Low-latency online serving of features (powered by Databricks Lakebase), consistent with offline tables; powers real-time feature lookup in model serving and Feature Serving Endpoints.
**How it helps:** If the agent must answer "is capacity X at risk right now?" in a chat turn, serve precomputed features online for sub-second lookups rather than recomputing telemetry per request.

**TITLE:** Model Serving with automatic feature lookup
**URL:** https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/automatic-feature-lookup
**Summary:** Models logged with feature metadata auto-join features at scoring time from primary keys.
**How it helps:** A registered "capacity risk" model needs only a capacity ID at inference; features are fetched automatically — clean integration with the UC model registry (section 2).

---

## 8. AI/BI Genie (NL→SQL conversational surface + managed MCP)

**TITLE:** Use the Genie Spaces API
**URL:** https://learn.microsoft.com/en-us/azure/databricks/genie/conversation-api
**Summary:** Genie Spaces = domain-specific NL→SQL interfaces over UC tables. **Conversation APIs** = stateful multi-turn querying (follow-ups, history) for embedding in chatbots/agents/apps. **Management APIs** = programmatic create/configure/deploy of spaces for CI/CD. Responses return generated SQL + structured tabular results (no rendered charts — render client-side). Stateful conversation retains context.
**Exact identifiers (REST `/api/2.0/genie/...`):**
- `POST /api/2.0/genie/spaces/{space_id}/start-conversation` (body `{"content": "..."}`) → returns `conversation.id`, `message.id`, `status` (`IN_PROGRESS`).
- `GET /api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}` — poll; `attachments` populate progressively (SQL appears during `PENDING_WAREHOUSE`/`EXECUTING_QUERY`); statuses `IN_PROGRESS`→`COMPLETED`/`FAILED`/`CANCELLED`.
- `GET .../messages/{message_id}/query-result/{attachment_id}` — tabular results (attachment has `text`, `query` SQL, `attachment_id`).
- `POST .../conversations/{conversation_id}/messages` — follow-up.
- Management: `POST /api/2.0/genie/spaces` (with escaped `serialized_space` JSON, version `2`), `GET /api/2.0/genie/spaces`, `GET /api/2.0/genie/spaces/{id}?include_serialized_space=true`, Update/Delete space, `DELETE .../conversations/{id}`, list conversations/messages, message comments.
- `serialized_space` schema: `config.sample_questions`, `data_sources.tables`/`metric_views` (+ `column_configs`, `enable_entity_matching`, `exclude`), `instructions.text_instructions` (max 1), `example_question_sqls` (+ `parameters`), `sql_functions`, `join_specs` (`--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--`), `sql_snippets.{filters,expressions,measures}`, `benchmarks.questions` (ground-truth SQL). IDs = 32-char lowercase hex; collections must be pre-sorted.
- **Trusted assets**: `attachments[].query.parameters` present ⇒ answer came from a trusted asset. Reasoning traces: `query_attachments` of type `GenieQueryAttachments`.
- Prereqs: DBSQL entitlement, `CAN USE` on a SQL pro/serverless warehouse. Auth U2M (OAuth) or M2M service principal. Throughput: ~5 questions/min/workspace (free tier); poll every 1–5s, exp backoff, 10-min cap; ≤10,000 conversations/space; start a NEW conversation per session.
**How it helps:** Add a **self-service conversational surface** over the audit agent's own output tables (findings, capacity utilization, recommendations stored in UC Delta). Stakeholders ask "which capacities throttled last week?" / "show top 5 by overage cost" in Teams/Slack/an app — Genie generates governed SQL and returns rows. Curate the space with example SQL + measures (e.g. `total_overage_cost`) + benchmarks so answers are reliable. Trusted-asset checks let the agent verify provenance.

**TITLE:** Genie Spaces (overview) / Conversational Analytics — Genie Agents
**URL:** https://learn.microsoft.com/en-us/azure/databricks/genie/  •  https://www.databricks.com/product/genie/agents
**Summary:** Genie as a managed conversational analytics surface; embeddable in Slack/Teams/custom apps; curated with datasets, SQL examples, business-semantic expressions, instructions.
**How it helps:** Genie is the lowest-effort "talk to the audit data" UI without building a bespoke NL→SQL layer.

**TITLE:** Model Context Protocol (MCP) on Databricks — managed Genie MCP
**URL:** https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/
**Summary:** Databricks exposes **managed MCP servers**, including a Genie MCP server, so an agent can query Genie Spaces as MCP tools (alongside UC Functions and Vector Search MCP servers).
**How it helps:** The audit agent's reasoner can call the managed **Genie MCP** as a tool to run NL→SQL over capacity tables, instead of hand-writing SQL — complements its existing Fabric MCP tools. (Mechanics of MCP itself already covered elsewhere; included here for the Genie-as-MCP angle only.)

---

## 9. AI/BI Dashboards

**TITLE:** Dashboards (AI/BI, formerly Lakeview)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/dashboards/
**Summary:** AI/BI Dashboards (formerly Lakeview) are governed data objects over UC. Support scheduled refresh, email/Slack subscriptions, embedding via iframe, and publishing. NL exploration enabled by publishing a Genie Space alongside the dashboard.
**Exact identifiers:** managed via **Lakeview API** (`/api/2.0/lakeview/...`) or Workspace API.
**How it helps:** Build a standing **Fabric/PBI capacity audit dashboard** — utilization trends, throttling events, top overage capacities, findings-by-severity — fed by the agent's UC output tables.

**TITLE:** Use dashboard APIs to create and manage dashboards (Lakeview)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/dashboard-crud-api  •  REST ref: https://docs.databricks.com/api/workspace/lakeview
**Summary:** CRUD + publish + schedule/subscribe via Lakeview API.
**Exact identifiers:** `POST /api/2.0/lakeview/dashboards`, `GET/PATCH .../{dashboard_id}`, **`POST /api/2.0/lakeview/dashboards/{dashboard_id}/published`** (publish), schedules & subscriptions endpoints, embed via iframe.
**How it helps:** Deploy/version the audit dashboard as code (alongside the agent), schedule daily refresh, and auto-email/Slack a capacity summary to FinOps — a **push** surface complementing Genie's **pull** surface.

**TITLE:** Manage dashboard and Genie Space embedding
**URL:** https://learn.microsoft.com/en-us/azure/databricks/ai-bi/admin/embed
**Summary:** Approve domains and embed published dashboards/Genie Spaces in external apps via iframe.
**How it helps:** Surface the audit dashboard + Genie chat inside an internal capacity-governance portal or Teams tab.

---

## 10. Mosaic AI Gateway / "Unity AI Gateway" (governance, rate limits, usage tracking, guardrails)

**TITLE:** Unity AI Gateway (new, Beta) — governs LLMs, agents, MCP servers, coding agents
**URL:** https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/
**Summary:** Central AI governance layer for LLM endpoints, agents, **MCP servers**, and coding tools. Analyze usage, configure permissions, enforce guardrails, manage capacity across providers. Unified UI, observability, expanded API coverage. Free during Beta. Notably governs MCPs (visibility, access control, audit logging across all MCP interactions) — directly relevant since the audit agent IS an MCP server + Mosaic AI agent.
**Exact identifiers / sub-features:**
- LLM/agent governance: configure endpoints, query via OpenAI client, **usage tracking via system tables**, cost observability (billable usage system table + usage dashboard), **inference tables** (UC Delta request/response logging), **rate limits**, **traffic splitting** across model backends, coding-agent integration (Cursor, Gemini CLI, Codex CLI, **Claude Code**).
- Route agent LLM calls through the gateway: Agent Framework `author-agent#ai-gateway`.
- MCP governance pages: external MCP install (managed connections), host custom MCP as a Databricks App, connect clients.
**How it helps:** Put the agent's LLM calls AND its MCP traffic behind Unity AI Gateway → per-user/endpoint rate limits (cap audit-run spend), usage tracking + cost attribution per principal/tag, inference tables for full audit log of every model/MCP request-response (compliance evidence for a governance tool), and access control over who can invoke the audit MCP.

**TITLE:** Configure Unity AI Gateway on model serving endpoints (previous version)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/configure-ai-gateway-endpoints
**Summary:** The "previous" gateway configures usage tracking, payload logging, **rate limits**, and **AI Guardrails** directly on a model serving endpoint via the `ai_gateway` block.
**Exact identifiers / config (`ai_gateway` block on endpoint):**
- `usage_tracking_config` (enable usage logging to system tables).
- `inference_table_config` (payload/inference logging to a UC catalog.schema).
- `rate_limits` — scopes `user` or `endpoint`; `calls`/`renewal_period` (e.g. per-minute).
- `guardrails` — `safety` (block unsafe content), **PII** detection/masking (`pii.behavior`: BLOCK/MASK), `invalid_keywords`, `valid_topics` (topic moderation), applied to `input`/`output`.
- `fallback_config` and traffic splitting on the served-entities config.
- System tables: `system.serving.endpoint_usage`, `system.serving.served_entities`.
**How it helps:** Enforce **PII masking** and **safety guardrails** on the agent's model endpoint (defense-in-depth beyond the `Safety` judge), set per-user rate limits so a runaway audit loop can't blow the model budget, and use `system.serving.endpoint_usage` to chargeback/track the agent's model spend. Inference tables give the request/response audit trail.

**TITLE:** External models in Mosaic AI Model Serving
**URL:** https://learn.microsoft.com/en-us/azure/databricks/generative-ai/external-models/
**Summary:** Govern third-party providers (OpenAI/Anthropic/etc.) behind one Databricks endpoint with gateway features (rate limits, usage, guardrails, fallbacks).
**How it helps:** If the agent ever calls an external LLM, route it through an external-model endpoint so the same governance/guardrails apply uniformly.

**TITLE:** Mosaic AI Gateway: Secure, Responsible LLM Access / Unity AI Gateway product
**URL:** https://www.databricks.com/product/artificial-intelligence/unity-ai-gateway
**Summary:** Product overview — rate limits, AI Guardrails (PII filtering, unsafe-content blocking), access control via UC, usage tracking + inference tables for chargebacks and data-leakage audit, multi-provider cost control.
**How it helps:** Business case for routing all of the agent's model + MCP endpoints through one governed gateway.

---

## 11. AI Functions on serverless (SQL/Python LLM functions)

**TITLE:** Enrich data using AI Functions
**URL:** https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-functions
**Summary (Public Preview):** Built-in functions to apply LLMs/research models to data from anywhere on Databricks (DBSQL, notebooks, Lakeflow pipelines, Workflows). Fully **serverless**, production-grade batch. Two kinds: **task-specific** (Databricks-managed models) and general-purpose **`ai_query`** (any supported Foundation Model API / custom / external endpoint). Costs land under `MODEL_SERVING`/`BATCH_INFERENCE` (or `AI_FUNCTIONS` product for `ai_parse_document`/`ai_extract`/`ai_classify`).
**Exact identifiers (functions):**
- General: **`ai_query`** (prompt + chosen endpoint; supports batch inference, `returnType`/response format/structured output; recommend Databricks-hosted `databricks-` foundation models for batch).
- Document processing: `ai_parse_document`, `ai_extract`, `ai_classify`, `ai_prep_search` (Beta).
- Transform: `ai_fix_grammar`, `ai_translate`, `ai_summarize`, `ai_mask`.
- Analyze: `ai_analyze_sentiment`, `ai_similarity`.
- Generate: `ai_gen`.
- Forecast: `ai_forecast` (table-valued, time-series extrapolation).
- Search: `vector_search` (queries an AI Search index from SQL).
- Cost queries via `system.billing.usage` filtered on `billing_origin_product='MODEL_SERVING'` + `offering_type='BATCH_INFERENCE'` (or `'AI_FUNCTIONS'`).
**How it helps:**
- `ai_query` / `ai_summarize` / `ai_gen`: batch-summarize the day's audit findings per capacity into a one-line narrative directly in SQL — no serving infra. Can call the Claude endpoint for the summary.
- `ai_classify`: bucket free-text remediation notes or detector outputs into severity/category labels at scale.
- `ai_forecast`: **forecast capacity CU utilization** forward to flag capacities trending toward throttling — a strong proactive audit signal computed in plain SQL.
- `ai_extract`/`ai_parse_document`: pull structured fields from PDF capacity policies/contracts into the knowledge base.
- `vector_search` SQL function: RAG retrieval from inside the same SQL pipeline that produces the audit tables.
- `ai_mask`: scrub PII from any user/owner names before storing or surfacing findings.

**TITLE:** ai_query function (reference)
**URL:** https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_query
**Summary:** Reference for `ai_query(endpoint, request [, returnType, responseFormat, failOnError, modelParameters])` — real-time or batch inference against a serving endpoint, with typed/structured outputs.
**How it helps:** Exact signature to drive batch summarization/classification of audit results against the Claude (or a `databricks-`) endpoint, with `responseFormat` for structured JSON findings.

---

## How the pieces compose for bi-fabrics-audit-agent

- **Harden:** MLflow Tracing on every audit run (cost/latency/tool-call visibility) → Unity AI Gateway in front of the model + MCP endpoints (rate limits, PII/safety guardrails, inference-table audit log, usage chargeback) → register the agent in the UC model registry with `@champion`/`@challenger` aliases.
- **Evaluate:** dev eval suite with built-in + custom + **read-only-invariant code scorers** via `mlflow.genai.evaluate()`; Review App for SME labels → judge alignment; then `register()/start()` the same scorers for sampled **production monitoring**.
- **Conversational surface:** AI/BI **Genie Space** over the agent's UC findings tables (Conversation API + managed Genie **MCP** tool); **AI/BI Dashboard** (Lakeview API) for scheduled push to FinOps; both embeddable.
- **Smarter analysis:** **AI Search (hybrid)** RAG over governance runbooks/policies; **Feature Store (UC)** for governed capacity features feeding a "capacity-at-risk" model; **AI Functions** (`ai_forecast`, `ai_query`, `ai_classify`, `ai_mask`) for serverless forecasting, summarization, classification, and PII masking inside SQL pipelines.

---

## Flat URL list

https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/
https://www.databricks.com/blog/mlflow-30-unified-ai-experimentation-observability-and-governance
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/tracing/integrations/
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/getting-started/tracing/tracing-ide
https://learn.microsoft.com/en-us/azure/databricks/mlflow/models
https://learn.microsoft.com/en-us/azure/databricks/machine-learning/manage-model-lifecycle/
https://learn.microsoft.com/en-us/azure/databricks/mlflow/model-registry-3
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/concepts/scorers
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/concepts/judges/
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/code-based-scorer-examples
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/
https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/production-monitoring
https://www.databricks.com/blog/what-is-agent-evaluation
https://learn.microsoft.com/en-us/azure/databricks/vector-search/vector-search
https://learn.microsoft.com/en-us/azure/databricks/vector-search/create-vector-search
https://www.databricks.com/blog/announcing-hybrid-search-general-availability-mosaic-ai-vector-search
https://www.databricks.com/blog/announcing-mosaic-ai-vector-search-general-availability-databricks
https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/
https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/online-feature-store
https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/automatic-feature-lookup
https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/concepts
https://api-docs.databricks.com/python/feature-engineering/latest/feature_engineering.client.html
https://learn.microsoft.com/en-us/azure/databricks/genie/conversation-api
https://learn.microsoft.com/en-us/azure/databricks/genie/
https://www.databricks.com/product/genie/agents
https://www.databricks.com/blog/genie-conversation-apis-public-preview
https://learn.microsoft.com/en-us/azure/databricks/generative-ai/mcp/
https://learn.microsoft.com/en-us/azure/databricks/dashboards/
https://learn.microsoft.com/en-us/azure/databricks/dashboards/tutorials/dashboard-crud-api
https://docs.databricks.com/api/workspace/lakeview
https://learn.microsoft.com/en-us/azure/databricks/ai-bi/admin/use-apis
https://learn.microsoft.com/en-us/azure/databricks/ai-bi/admin/embed
https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/
https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/configure-ai-gateway-endpoints
https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/overview-serving-endpoints
https://learn.microsoft.com/en-us/azure/databricks/generative-ai/external-models/
https://www.databricks.com/product/artificial-intelligence/unity-ai-gateway
https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-functions
https://learn.microsoft.com/en-us/azure/databricks/large-language-models/ai-query
https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/functions/ai_query
https://www.databricks.com/blog/introducing-serverless-batch-inference

# 08 — Prior Art: How Others Built Similar Diagnostic / Investigator Agents

**Scope.** Survey of commercial and open-source/published agents that do the same *job* as our planned
**read-only Microsoft Fabric / Power BI capacity investigator** — root-cause analysis, "who-did-what"
attribution over telemetry, hypothesis generation with confidence, multi-source fallback, human-in-the-loop.
The goal is to borrow proven patterns and avoid known traps. Currency: 2025–2026 sources throughout.

**TL;DR — the convergent design.** Every credible system in this space has independently converged on the
same shape: **a deterministic / curated layer that encodes domain expertise, wrapped by an LLM agent loop
that forms hypotheses, queries read-only tools to confirm/refute them, and shows its work.** The LLM is the
reasoning glue; it is *not* trusted to invent the telemetry semantics or to run free-form mutations. Our
Fabric investigator should be built the same way.

---

## Part 1 — Commercial observability / SRE copilots

### 1.1 Datadog Bits AI SRE — the strongest direct analog
Bits AI SRE (announced DASH 2025, GA Dec 2025) is an autonomous on-call agent that investigates the moment
an alert fires, reads the same telemetry a human would, "understands your architecture," and follows your
runbooks to surface likely root causes.
([intro](https://www.datadoghq.com/blog/bits-ai-sre/),
[press release](https://www.datadoghq.com/about/latest-news/press-releases/datadog-launches-bits-ai-sre-agent-to-resolve-incidents-faster/))

**Architecture / patterns (the parts worth stealing):**
- **Hypothesis-driven loop, not summarization.** Bits "formulates hypotheses about the root cause, validates
  or rejects each using data from targeted queries, and repeats until it reaches a root cause." Crucially it
  "focuses on the causal relationship between the alert and *specific* telemetry for a hypothesis, rather than
  looking at all available telemetry at once."
  ([how we built it](https://www.datadoghq.com/blog/building-bits-ai-sre/))
- **Sub-hypothesis decomposition (a tree).** A hypothesis is broken into sub-hypotheses; if evidence supports
  one, the agent digs deeper, else it backtracks — "just like a human SRE following the most promising lead."
  In their example it pushed past "out of memory" to "corrupt sourcemaps + inefficient parsing → oversized
  Kafka payloads." ([deeper reasoning](https://www.datadoghq.com/blog/bits-ai-sre-deeper-reasoning/))
- **Agent Trace view = the trust mechanism.** Every step is exposed — tools called, data queried, intermediate
  analysis — so humans can "validate the approach, inspect how hypotheses were formed and eliminated, and
  diagnose situations where results differ from expectations." This is their answer to "why should I believe
  the agent." ([deeper reasoning](https://www.datadoghq.com/blog/bits-ai-sre-deeper-reasoning/))
- **Human-in-the-loop triage, not auto-remediation.** The agent stops at conclusions + suggested actions;
  humans trigger follow-ups (Slack/Teams notify, create incident/case, Jira) directly in chat. Seven triage
  actions, context auto-populated. ([deeper reasoning](https://www.datadoghq.com/blog/bits-ai-sre-deeper-reasoning/))
- **Evaluation on real labeled incidents + LLM judge.** "We worked across hundreds of teams to collect and
  label real incidents and used them to create a benchmark dataset"; an LLM-as-judge scores outputs against
  ground truth. ([how we built it](https://www.datadoghq.com/blog/building-bits-ai-sre/))

**What was HARD / what they fixed (explicitly documented):**
- **Tool-call fan-out poisoned the context.** Early versions issued *12+ simultaneous tool calls* across logs/
  traces/metrics, creating "input bloat that degraded performance." The fix was *selective, hypothesis-scoped*
  querying. This is the single most important anti-pattern for us: do **not** dump every Capacity-Metrics page
  into the context. ([how we built it](https://www.datadoghq.com/blog/building-bits-ai-sre/))
- **Latency / iteration cost.** Even the rebuilt agent takes ~3–4 min per investigation; the 2× speedup came
  from a new "agent harness" and tighter MCP tool integration. ([deeper reasoning](https://www.datadoghq.com/blog/bits-ai-sre-deeper-reasoning/))

### 1.2 Dynatrace Davis AI + Davis CoPilot — the deterministic-causal / generative split
Dynatrace's "hypermodal" framing is the clearest articulation of *where the LLM belongs vs. where it does
not*. **Causal AI** walks the Smartscape topology graph to determine root cause deterministically and
reproducibly; **generative AI (Davis CoPilot)** sits on top to explain in natural language, author queries/
dashboards, and enrich the technical root cause with community knowledge.
([Davis CoPilot launch](https://www.dynatrace.com/news/blog/hypermodal-ai-dynatrace-expands-davis-ai-with-davis-copilot/),
[RCA blog](https://www.dynatrace.com/news/blog/transform-your-operations-with-davis-ai-root-cause-analysis/),
[RCA concepts docs](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/concepts))

- **Key borrowable idea:** causal/predictive AI "provides context to Davis CoPilot and automatically enriches
  prompts," yielding "precise, reproducible results." Translation for us: **pre-compute the deterministic
  capacity facts (throttling stages, top CU contributors, the timepoint funnel) and feed them to the LLM as
  grounded context** rather than asking the LLM to derive them. The reproducibility claim is the selling point
  to skeptical capacity admins.
- Davis can correlate *multiple* problems, find a common root cause, and propose steps — analogous to
  correlating several overloaded timepoints to one offending artifact/user.

### 1.3 Honeycomb Query Assistant — the most candid "what actually broke" account
Honeycomb's NL→query assistant and their now-canonical blog *"All the Hard Stuff Nobody Talks About when
Building Products with LLMs"* is the richest source of production failure modes for exactly our query-
generation subproblem.
([Query Assistant intro](https://www.honeycomb.io/blog/introducing-query-assistant),
[Hard Stuff blog](https://www.honeycomb.io/blog/hard-stuff-nobody-talks-about-llm))

**Proven lessons (each maps directly to a Fabric design decision):**
- **Large schemas blow the context window.** Customers had **>5000 unique fields**; they constrained input to
  "fields that received data in the past seven days," and *still* sometimes had to truncate. "There's no
  complete solution to the context window problem." → For us, the FUAM / Capacity-Metrics schema is large;
  we must do **schema retrieval / curation**, not dump the whole model.
- **Chaining LLM calls did not help and multiplies error and latency.** Latency 2–15+s per call; "90% accuracy
  × 5 chained calls ≈ 59%." → Prefer one well-grounded call per step with tool execution between, not deep
  LLM→LLM chains.
- **Few-shot beat everything else** they tried; chain-of-thought was unreliable on vague inputs ("slow" → no
  query at all). → Curate a few-shot example library of good Fabric investigations.
- **Prompt injection is unsolved.** Mitigations: non-destructive outputs, validation/parsing of generated
  artifacts, rate limiting, **and disconnecting the LLM from any write path to the database.** → Hard-enforce
  read-only at the connection/credential layer, not in the prompt.
- **Frame output as "best effort."** Query Assistant explicitly returns a *best-effort* query the user can
  inspect and edit — managing expectations rather than claiming correctness. → Present hypotheses, not verdicts.
- **"An LLM isn't a product, it's an engine for features"** — most of the work is ordinary product scoping,
  validation, and testing. → Don't over-invest in agent cleverness; invest in tools, evals, and UX.

### 1.4 Grafana — two-tier: deterministic detectors (Sift) + prompt-driven agent (Assistant Investigations)
Grafana ships **both** layers as separate products, which validates the layered design:
- **Sift** runs *curated detectors* over cluster signals automatically (no prompt) and returns a list of
  "interesting results" — deterministic, ML-driven anomaly checks.
- **Assistant Investigations** is the prompt-driven SRE agent: it scans recent dashboards to find relevant
  panels/queries, runs multi-step investigations across metrics/logs/traces/profiles, and "produces a
  structured report with hypotheses and source queries."
([Sift docs](https://grafana.com/docs/grafana-cloud/machine-learning/sift/),
[Assistant GA blog](https://grafana.com/blog/grafana-assistant-ga-assistant-investigations-preview/),
[context-aware LLM agent](https://grafana.com/blog/2025/05/07/llm-grafana-assistant/))
- **Borrowable:** *every hypothesis is presented with the source query that produced its evidence* — auditable
  and reproducible. Grafana's own writeup claimed a **3.5× faster** root-cause find vs. manual.
  ([tale of two responses](https://grafana.com/blog/2025/11/17/a-tale-of-two-incident-responses-how-our-ai-assist-helped-us-find-the-cause-3-5x-faster/))

### 1.5 New Relic AI
New Relic AI does NL→NRQL plus agentic monitoring/diagnostics over its telemetry. The pattern matches the
others (NL → query generation → grounded explanation) and reinforces that **text-to-query over a proprietary
telemetry language is the core capability**, which for us is text-to-SQL (Delta) and text-to-KQL (Workspace
Monitoring Eventhouse / Log Analytics). (General product positioning; treat NRQL generation as confirmation
of the broad pattern rather than a unique source.)

---

## Part 2 — Open-source & published RCA / SRE agents

### 2.1 HolmesGPT (Robusta + Microsoft) — closest OSS blueprint; study this first
OSS agentic investigator, CNCF Sandbox (Oct 2025), now co-maintained by Microsoft. **Read-only by design,
respects RBAC, full audit logging of every tool call** — "safe to run in production."
([GitHub](https://github.com/HolmesGPT/holmesgpt),
[CNCF blog](https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/),
[DeepWiki architecture](https://deepwiki.com/robusta-dev/holmesgpt))

**Concrete architecture (from DeepWiki + docs):**
- **The loop** lives in `ToolCallingLLM.call()` (`holmes/core/tool_calling_llm.py`): send messages + tool
  definitions → receive tool calls → execute via `ToolExecutor` → append results to history (with **context-
  window limiting**) → iterate until the LLM gives a final answer **or max investigation steps are exhausted.**
- **Toolsets** = collections of related read-only tools bound to a data source, managed by `ToolsetManager`.
  Categories: Kubernetes, cloud (AWS/Azure/GCP, often via **MCP**), observability (Prometheus, Datadog,
  New Relic, Grafana), utility. Tools are declared in **YAML** (name, description, templated command), e.g.
  `kubectl get pod {{ pod }} -n {{ namespace }}`. The LLM picks tools from the provided definitions.
  ([CNCF blog](https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/),
  [custom toolsets docs](https://docs.robusta.dev/improve_holmes_docs/configuration/holmesgpt/custom_toolsets.html))
- **Runbooks / custom instructions** let you "encode operational expertise for others to reuse" (e.g. a DNS-
  failure or PVC-provisioning playbook) — the agent follows the runbook's investigation steps.
- **Guardrails on context:** `TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT` caps how much of the window a tool
  response may consume; `TOOL_MEMORY_LIMIT_MB` + streaming prevents OOM on huge log dumps. (Directly relevant
  to large Capacity-Metrics/activity-log responses.)
- **Evaluation** is first-class: an **LLM evaluation framework** with **Braintrust** reporting and a **mock
  system** for deterministic tests; the project explicitly supports "custom evals to benchmark performance,
  cost, latency of models." ([CNCF blog](https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/))

**Borrowable wholesale:** the **YAML toolset + runbook** structure, the read-only/RBAC/audit posture, the
context-window-budgeting guardrails, and the eval+mock harness. Our Fabric "toolsets" = Capacity Metrics
queries, Workspace Monitoring KQL, Activity Events, Scanner inventory; our "runbooks" = the throttling RCA
funnel and "who consumed my capacity" playbook.

### 2.2 k8sgpt — the canonical "deterministic analyzer → LLM explain" split
k8sgpt scans with **built-in deterministic analyzers** (14 enabled by default: Pod, Service, Deployment,
Node, …) that have "SRE experience codified into" them, then *optionally* calls an LLM only to **explain**
the already-found issues. ([GitHub](https://github.com/k8sgpt-ai/k8sgpt))
- **Anonymization before the LLM:** sensitive identifiers (e.g. pod name `fake-deployment`) are masked to a
  token (`tGLcCRcHa1Ce5Rs`) in the request, then de-anonymized in the response. → Direct template for masking
  tenant/user/workspace identifiers before they leave our boundary if we ever use an external model.
- **Pluggable analyzers** run as separate services and register into the scan; **read-only** throughout.
- **Borrowable:** do the *detection/attribution deterministically* (we already have a documented 3-step
  throttling funnel — see Part 3) and reserve the LLM for explanation, narrative, and next-step planning.
  This bounds hallucination because the LLM never invents the numbers.

### 2.3 Published RCA research — RCACopilot, RCAgent, "LLM agents for RCA"
- **RCACopilot (Microsoft, EuroSys '24)** is the most production-validated academic result. Two stages:
  (1) **incident-handler matching** by alert type aggregates runtime diagnostics deterministically; (2) an
  **LLM predicts the root-cause *category* and writes an explanatory narrative**. Evaluated on **a year of
  real Microsoft incidents, RCA accuracy up to 0.766**; the diagnostic-collection component has run at
  Microsoft **for 4+ years across 30+ teams.**
  ([MSR publication](https://www.microsoft.com/en-us/research/publication/automatic-root-cause-analysis-via-large-language-models-for-cloud-incidents/),
  [arXiv PDF](https://arxiv.org/pdf/2305.15778),
  [ACM](https://dl.acm.org/doi/10.1145/3627703.3629553))
  - Borrowable: **route by alert/symptom type to a specialized handler** (e.g. "interactive rejection" vs.
    "background rejection" vs. "report-level spike"), then have the LLM classify the root-cause category and
    narrate — categories make the output *evaluable* (accuracy against labeled history).
- **RCAgent (arXiv 2310.16340)** = autonomous tool-augmented agent emphasizing **data-privacy / on-prem
  models** for cloud RCA. Validates the OBO/read-only + keep-data-in-tenant posture.
  ([arXiv](https://arxiv.org/html/arXiv:2310.16340))
- **"Exploring LLM-based Agents for RCA" (ACM, 2024)** studies retrieval of similar past incidents + agentic
  evidence gathering and discusses eval methodology. ([ACM PDF](https://dl.acm.org/doi/pdf/10.1145/3663529.3663841))
  - Borrowable: **retrieve similar historical investigations** from our Delta/LA memory and few-shot them in.

### 2.4 Commercial SRE incident copilots (PagerDuty, ServiceNow, Cleric, Resolve.ai, Parity, Azure SRE Agent)
Consistent themes across the category (treated as corroboration of the patterns above):
- **Build/maintain an environment model** (topology / knowledge graph of services & dependencies) the agent
  reasons over — analogous to our Fabric inventory (Scanner API) + capacity/workspace/item graph.
- **Read-only investigation, human-approved action** is the near-universal stance for production trust.
- **Investigation packaged as a timeline/report** attached to the incident, with evidence links.

---

## Part 3 — Microsoft / Databricks first-party agents & the Fabric capacity substrate

### 3.1 The Fabric capacity domain logic the investigator MUST encode (deterministic layer)
Microsoft already documents a **manual 3-step root-cause funnel** for capacity throttling — this is our
"runbook" and our deterministic analyzer, ready to codify:
1. Did utilization exceed 100% (is there an overload at all)?
2. Did throttling actually trigger, and which stage — **interactive delay → interactive rejection →
   background rejection** (driven by CU **smoothing** over future windows)?
3. Drill to the **specific workspace + item + operation** that caused the overload, distinguishing
   **interactive vs. background** operations.
([throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling),
[troubleshoot guide](https://learn.microsoft.com/en-us/fabric/enterprise/capacity-planning-troubleshoot-throttling))
- The **Capacity Metrics app — Timepoint detail page** is Microsoft's own "who-did-what" surface: it filters
  operations by **operation ID, user, and CU thresholds** at a given timepoint — exactly the attribution our
  agent must reproduce/query. ([timepoint page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-page))

### 3.2 Telemetry sources + retention → the multi-source fallback plan
- **Microsoft Fabric Capacity Metrics** — CU by timepoint, top operations, throttling stages (primary spike
  evidence).
- **Workspace Monitoring** — telemetry in a **KQL-queryable Eventhouse** (semantic-model logs, query activity,
  refresh durations, XMLA operations), attributable by capacity/workspace/report/user — the natural
  text-to-KQL partner to the Metrics app. ([overview](https://learn.microsoft.com/en-us/fabric/fundamentals/workspace-monitoring-overview),
  [semantic-model logs](https://fabric.guru/analyzing-semantic-model-logs-using-fabric-workspace-monitoring))
- **FUAM (Fabric Unified Admin Monitoring)** — open-source Microsoft accelerator (`microsoft/fabric-toolbox`):
  pulls Capacity Metrics by timepoint, **activity logs**, **active-items inventory**, and **Scanner API
  metadata** into a Lakehouse via PySpark; this is the broad historical substrate.
  ([FUAM architecture](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Architecture.md))
- **"Months-back" attribution = a retention problem to design around (critical):**
  - **Power BI Activity Events API: only ~30 days** of retention.
  - **Microsoft 365 unified audit log (via Purview): 90 days Standard, 1 year with Audit Premium (E5), up to
    10 years with add-on.** ([activity log guidance](https://learn.microsoft.com/en-us/power-bi/guidance/admin-activity-log),
    [audit/Purview](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-admin-auditing))
  - **Implication:** for investigations older than ~30 days the agent must fall back from the Activity Events
    API to **persisted FUAM/Delta history** or the **unified audit log**; encode this fallback explicitly and
    have the agent **state which source answered and its freshness/retention caveat.**

### 3.3 Databricks-native build patterns (our actual stack) — what's proven
- **Mosaic AI Agent Framework + MLflow `ResponsesAgent`:** authoring with `ResponsesAgent` auto-infers a
  signature compatible with AI Playground, Agent Evaluation, and Agent Monitoring; tool-calling LLM with
  Unity Catalog functions as tools; deployable on **Databricks Apps**.
  ([author agent docs](https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent),
  [framework + eval blog](https://www.databricks.com/blog/announcing-mosaic-ai-agent-framework-and-agent-evaluation))
- **MLflow Tracing:** traces every reasoning step (which tool failed, where reasoning drifted); the dev-time
  trace is logged for every production request — our equivalent of Datadog's Agent Trace view, for free.
- **Agent Evaluation / LLM judges:** define test questions + expected answers; judges score correctness/
  groundedness with a yes/no + written rationale, calibrated by SME feedback. → Build a labeled set of past
  capacity incidents (à la RCACopilot/Datadog) and judge against it.
- **Genie (text-to-SQL):** confirms both the value and the limits. **Trusted assets** (parameterized
  *certified* SQL queries + Unity Catalog UDFs) provide *verified* answers for anticipated questions — i.e. a
  curated semantic/query layer rather than free generation. Hard limits worth noting: **≤30 tables per space**,
  ~20 questions/min/workspace; quality depends heavily on curated instructions/examples.
  ([trusted assets](https://learn.microsoft.com/en-us/azure/databricks/genie/trusted-assets),
  [set up / limits](https://docs.databricks.com/aws/en/genie/set-up),
  [tune quality](https://docs.databricks.com/aws/en/genie/tune-quality))
  - Borrowable: ship **certified Fabric queries** (the throttling funnel, top-CU-by-user, refresh-failure
    timeline) as trusted assets/UC functions so the common investigations are deterministic, and reserve
    free-form text-to-SQL/KQL for the long tail.

**Deeper Databricks build patterns (our actual stack):**
- **Author as `mlflow.pyfunc.ResponsesAgent`** (2025 recommendation, superseding `ChatAgent`/`ChatModel`).
  Implement `predict()` / `predict_stream()`; it's framework-agnostic, so you **wrap a raw Anthropic tool-loop**
  and emit `ResponsesAgentResponse` / streaming `ResponsesAgentStreamEvent` items. Built on the OpenAI
  Responses schema, it can **return multiple output messages including intermediate tool-calling messages** —
  i.e. the full auditable reasoning trace — and gives AI Playground / Agent Evaluation / Monitoring / Review
  App + auto MLflow tracing "for free."
  ([author agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent),
  [MLflow ResponsesAgent](https://mlflow.org/docs/latest/genai/flavors/responses-agent-intro/))
- **Tools as governed UC functions + MCP.** Two flavors: **Unity Catalog SQL/Python functions** (governed,
  parameterized — the read-only "trusted-asset"-style queries against telemetry) and local Python tools; plus
  **Databricks-managed and custom MCP servers** (a Genie space or UC functions can be exposed *as* MCP),
  matching our read-only-MCP-tools design.
  ([agent tools / MCP](https://docs.databricks.com/aws/en/generative-ai/mcp/))
- **Genie programmatic embedding.** The **Conversation API**
  (`POST /api/2.0/genie/spaces/{id}/start-conversation`, `.../messages`) drives a Genie space from an external
  app and returns generated SQL + result attachments, so a curated Genie space can be embedded as a *text-to-SQL
  tool* inside the larger agent (the `GenieAgent` pattern). Genie runs under the **querying user's UC
  permissions** (RLS/CLS honored). ([Genie Conversation API](https://docs.databricks.com/api/workspace/genie))
- **Evaluation-driven development with MLflow 3 judges.** `mlflow.genai.evaluate()` + scorers; built-in judges
  include **Correctness, Groundedness (hallucination/faithfulness), RelevanceToQuery, Safety, Retrieval
  Sufficiency**, plus **guideline-based judges** (natural-language pass/fail, e.g. *"the answer must cite a
  telemetry source and a timepoint," "must state the retention window," "must not assert beyond the data"*) and
  custom `@scorer` functions. Build eval sets from real incident traces; judges run offline and as online
  production monitors; align judges to human feedback.
  ([LLM judge reference](https://docs.databricks.com/aws/en/generative-ai/agent-evaluation/llm-judge-reference),
  [custom judges](https://docs.databricks.com/aws/en/generative-ai/agent-evaluation/custom-judge))
- **Deploy.** `log_model` (Models-from-Code) → register to **Unity Catalog** → `agents.deploy(...)` provisions a
  **Model Serving** endpoint + Review App + payload-logging tables. Front-end as a **Databricks App** (serverless,
  workspace OAuth, app service principal with least-privilege UC grants) that either calls the serving endpoint
  or self-hosts the loop. Microsoft/Databricks document "author an AI agent and deploy it on Databricks Apps"
  with a chat-UI scaffold. ([deploy agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/deploy-agent),
  [Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/))
- **Agent Bricks "Multi-Agent Supervisor"** (beta) — an orchestrator routing to specialist agents + Genie
  spaces, with **auto-generated eval sets and LLM judges**. If we later split into telemetry-query /
  attribution / hypothesis sub-agents, this is the native pattern; the auto-eval bootstrap is notable.
  ([Agent Bricks](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/))
- **Reference code:** the **Databricks GenAI Cookbook** (`databricks/genai-cookbook`, ai-cookbook.io) codifies
  the tool-calling-agent + evaluation-driven-development loop. ([cookbook](https://ai-cookbook.io/))

### 3.4 Microsoft Copilot for Power BI / Fabric — what it does NOT do (the gap we fill)
Copilot in Fabric/Power BI is oriented to *authoring* (build reports/measures, summarize a model, NL Q&A
grounded on a semantic model) and Fabric **data agents** answer questions over curated data. It is **not** a
capacity root-cause investigator and does not do cross-source "who blew up my capacity three weeks ago"
attribution. That diagnostic, multi-source, retention-aware investigation is precisely our niche.

---

## Part 4 — Text-to-SQL / Text-to-KQL: keeping query generation accurate AND read-only

Our investigator stands or falls on generating *correct, read-only* queries over Delta and KQL. The 2025–26
literature is blunt about why naive text-to-SQL fails and what fixes it.

**Proven failure modes & fixes**
([Omni: why text-to-SQL fails](https://omni.co/blog/why-text-to-sql-fails),
[dbt semantic-layer benchmark 2026](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026),
[Google Cloud: six failures](https://medium.com/google-cloud/the-six-failures-of-text-to-sql-and-how-to-fix-them-with-agents-ef5fd2b74b68),
[ReFoRCE agent paper](https://arxiv.org/pdf/2502.00675),
[LinkAlign schema linking](https://aclanthology.org/2025.emnlp-main.51.pdf)):
- **Confident silent failure:** LLMs hit only **~30–36% accuracy on complex schemas (~1000 columns / 54
  tables)** and return *plausible-but-wrong* SQL. A **semantic layer turns wrong answers into error
  messages** instead of bad numbers — essential for capacity audits that admins will act on.
- **Schema linking is the bottleneck** at scale → retrieve the relevant tables/columns (and example *values*)
  rather than dumping the schema (mirrors Honeycomb's >5000-field problem).
- **Self-correction / execution-repair loops:** run the query, feed the DB error back, let the agent fix it
  (ReFoRCE-style) — but cap iterations to control latency/cost.
- **Business-term ambiguity** ("revenue", or for us "usage" vs. "CU(s)" vs. "duration") is a *definition*
  problem solved by curated metrics, not by the model.
- **Read-only is non-negotiable and must be enforced outside the prompt:** "hallucinated UPDATE/DELETE could
  corrupt data… most prototypes fail compliance review without *enforced* read-only execution." → Enforce via
  a read-only service principal / scoped credential + statement allow-listing, not "please don't write" in the
  system prompt. (Matches Honeycomb's "disconnect the LLM from the database write path.")
- **Feedback loop:** capture user corrections into the semantic layer + golden-query examples.

**Text-to-KQL specifically** (for Workspace Monitoring / Log Analytics): Microsoft Security Copilot / Sentinel
NL→KQL and ADX "Kusto Copilot" exist and work for assisted authoring, with the same caveat — accuracy is
strongest when grounded on a known schema and curated examples; treat generated KQL as a draft to validate
and execute read-only, never as authoritative without execution.

---

## Part 5 — FinOps / cost-spike attribution: the "who/what caused the spike" pattern

This category is the cost-domain twin of our capacity-attribution job and has a clean, proven flow.

**AWS Cost Anomaly Detection — anomaly → ranked multi-root-cause → trace the actor**
([AWS enhanced RCA](https://aws.amazon.com/blogs/aws-cloud-financial-management/faster-anomaly-resolution-with-enhanced-root-cause-analysis-in-aws-cost-anomaly-detection/),
[product](https://aws.amazon.com/aws-cost-management/aws-cost-anomaly-detection/)):
- Detect the anomaly, then **identify up to 10 root causes** by analyzing every combination of
  service × account × region × usage-type, with **a dollar amount attributed to each** and ranked by
  contribution: *"RDS read replica in us-east-1 in account production-012345 caused 87% of the spike."*
- **Then trace the actor:** "use **CloudTrail** to trace the user or process that launched it… distinguish an
  automated CI/CD deployment from a manual console change."
- **Borrowable, almost 1:1:** our flow is *detect overloaded timepoint → rank contributing
  workspace/item/operation with **CU share %** → attribute to the **user/operation** via Activity Events /
  audit log → distinguish scheduled refresh (background) from interactive user action.* Always present a
  **ranked contributor list with magnitude**, not a single guess.

**Third-party (Vantage, CloudZero, Finout, Kubecost/OpenCost)**
([Vantage anomaly tools](https://www.vantage.sh/blog/cloud-cost-anomaly-detection)):
- Drill to **resource + tag level**, push the specific resource into the alert, and notify the right owner.
- Borrowable: **attribution should land on an owner/identity**, and the agent's output should be routable to
  that owner (capacity admin / workspace owner), echoing Datadog's HITL triage actions.

---

## Part 6 — Synthesis: top proven patterns to ADOPT

1. **Two-layer architecture (deterministic core + LLM reasoning).** Encode the Fabric throttling/overload RCA
   funnel and the top-CU-contributor attribution **deterministically** (k8sgpt/RCACopilot/Dynatrace pattern);
   use the LLM to plan, narrate, and handle the long tail. The LLM never invents the numbers.
2. **Hypothesis-driven, hypothesis-*scoped* querying.** Form root-cause hypotheses, then run **targeted**
   read-only queries to confirm/refute each (Bits AI SRE). **Do not** fan out and dump all telemetry — that
   demonstrably degrades quality (Datadog's documented 12-call mistake).
3. **Sub-hypothesis tree with backtracking.** Decompose ("interactive rejection" → which workspace → which
   item → which operation → which user/refresh), digging where evidence supports, backtracking where it
   doesn't (Bits AI SRE).
4. **Show your work = the trust mechanism.** Every conclusion carries the **source query + evidence** and a
   step-by-step trace (Datadog Agent Trace, Grafana "hypotheses with source queries," MLflow Tracing — which
   we get natively).
5. **Ranked contributors with magnitude, not a lone verdict.** Present *N* candidate causes with **CU share %
   / dollar-equivalent** and **confidence**, and the identity/owner to route to (AWS Cost RCA + Vantage).
6. **Multi-source fallback that is explicit about provenance & retention.** Capacity Metrics → Workspace
   Monitoring (KQL) → Activity Events (≤30 d) → FUAM/Delta history / unified audit log (90 d–years). Always
   state which source answered and its freshness/retention caveat. Build this as named "toolsets/runbooks"
   (HolmesGPT).
7. **Curated query/semantic layer + few-shot.** Ship **certified queries / UC functions** (Genie trusted
   assets) for common investigations; do **schema-linking/retrieval** for the long tail; few-shot examples
   beat clever prompting (Honeycomb). Capture user corrections back into the layer.
8. **Enforce read-only OUTSIDE the prompt.** Read-only SP/OBO, scoped credentials, statement allow-listing,
   non-destructive outputs, audit-log every tool call (HolmesGPT/k8sgpt/text-to-SQL guardrails). Mask
   tenant/user identifiers if any data leaves the boundary (k8sgpt anonymization).
9. **Human-in-the-loop for action; investigation is autonomous, remediation is not.** Output conclusions +
   suggested next steps + one-click routing to the owner; humans decide (Bits AI SRE; whole SRE-copilot
   category).
10. **Evaluate against labeled real incidents with an LLM judge, from day one.** Collect/label past capacity
    incidents (Datadog, RCACopilot's 0.766-on-a-year-of-incidents); use Databricks **Agent Evaluation** + SME-
    calibrated judges; keep a **mock harness** for deterministic tests (HolmesGPT). Classify into **root-cause
    categories** so accuracy is measurable.
11. **Context-window budgeting on tool outputs.** Cap per-tool context share and stream/limit large
    log/metrics responses (HolmesGPT `TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT`, `TOOL_MEMORY_LIMIT_MB`); Fabric
    activity/metrics responses are large.
12. **Author on the native stack to get observability + eval + governance free.** Wrap the Anthropic tool-loop
    in MLflow `ResponsesAgent` (returns intermediate tool messages = auditable trace), expose telemetry as
    governed read-only **UC functions / MCP** (optionally a curated **Genie space** as a text-to-SQL tool),
    govern reads via **Unity Catalog + on-behalf-of-user** auth (preserves RLS/CLS), and run the front-end as a
    **Databricks App** over a Model Serving endpoint. Encode **groundedness + correctness + guideline judges**
    via `mlflow.genai.evaluate()` from day one.

## Part 7 — Anti-patterns to AVOID

- **Telemetry dump / max tool fan-out.** Pulling every page/source into context up front — Datadog explicitly
  rebuilt to kill this.
- **Deep LLM→LLM chaining.** Multiplies error and latency for no accuracy gain (Honeycomb).
- **Free-form text-to-SQL/KQL against a raw, large schema with no semantic layer.** ~30–36% accuracy and
  *confident* wrong answers (dbt/Omni benchmarks).
- **Trusting the prompt for read-only / injection safety.** Prompt injection is unsolved; enforce read-only at
  the credential/execution layer (Honeycomb).
- **Single-verdict, no-evidence output.** Admins won't trust (and can't audit) a bare answer; always show
  ranked causes + evidence + confidence + provenance.
- **Letting the LLM compute capacity semantics** (smoothing, throttling stages, CU math). Pre-compute
  deterministically and feed as grounded context (Dynatrace causal→generative split).
- **Ignoring retention.** Promising "months-back" attribution while silently relying on the 30-day Activity
  Events API — design the audit-log/FUAM fallback and surface the caveat.
- **Auto-remediation.** Out of scope and trust-destroying for a read-only auditor; keep humans in the loop.

---

### Source index (primary)
Datadog Bits AI SRE — [intro](https://www.datadoghq.com/blog/bits-ai-sre/),
[how we built it](https://www.datadoghq.com/blog/building-bits-ai-sre/),
[deeper reasoning](https://www.datadoghq.com/blog/bits-ai-sre-deeper-reasoning/),
[press release](https://www.datadoghq.com/about/latest-news/press-releases/datadog-launches-bits-ai-sre-agent-to-resolve-incidents-faster/) ·
Dynatrace — [Davis CoPilot](https://www.dynatrace.com/news/blog/hypermodal-ai-dynatrace-expands-davis-ai-with-davis-copilot/),
[RCA blog](https://www.dynatrace.com/news/blog/transform-your-operations-with-davis-ai-root-cause-analysis/),
[RCA concepts](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/concepts) ·
Honeycomb — [Query Assistant](https://www.honeycomb.io/blog/introducing-query-assistant),
[Hard Stuff blog](https://www.honeycomb.io/blog/hard-stuff-nobody-talks-about-llm) ·
Grafana — [Sift](https://grafana.com/docs/grafana-cloud/machine-learning/sift/),
[Assistant GA](https://grafana.com/blog/grafana-assistant-ga-assistant-investigations-preview/),
[LLM agent](https://grafana.com/blog/2025/05/07/llm-grafana-assistant/),
[3.5× faster](https://grafana.com/blog/2025/11/17/a-tale-of-two-incident-responses-how-our-ai-assist-helped-us-find-the-cause-3-5x-faster/) ·
HolmesGPT — [GitHub](https://github.com/HolmesGPT/holmesgpt),
[CNCF](https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/),
[DeepWiki](https://deepwiki.com/robusta-dev/holmesgpt),
[custom toolsets](https://docs.robusta.dev/improve_holmes_docs/configuration/holmesgpt/custom_toolsets.html) ·
k8sgpt — [GitHub](https://github.com/k8sgpt-ai/k8sgpt) ·
RCA research — [RCACopilot MSR](https://www.microsoft.com/en-us/research/publication/automatic-root-cause-analysis-via-large-language-models-for-cloud-incidents/),
[arXiv](https://arxiv.org/pdf/2305.15778),
[RCAgent](https://arxiv.org/html/arXiv:2310.16340),
[LLM agents for RCA](https://dl.acm.org/doi/pdf/10.1145/3663529.3663841) ·
Databricks — [Agent Framework + Eval](https://www.databricks.com/blog/announcing-mosaic-ai-agent-framework-and-agent-evaluation),
[author agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent),
[Genie trusted assets](https://learn.microsoft.com/en-us/azure/databricks/genie/trusted-assets),
[Genie setup/limits](https://docs.databricks.com/aws/en/genie/set-up) ·
Fabric — [throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling),
[troubleshoot throttling](https://learn.microsoft.com/en-us/fabric/enterprise/capacity-planning-troubleshoot-throttling),
[timepoint page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-page),
[Workspace Monitoring](https://learn.microsoft.com/en-us/fabric/fundamentals/workspace-monitoring-overview),
[FUAM](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Architecture.md),
[activity log retention](https://learn.microsoft.com/en-us/power-bi/guidance/admin-activity-log),
[audit/Purview](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-admin-auditing) ·
Text-to-SQL — [Omni](https://omni.co/blog/why-text-to-sql-fails),
[dbt 2026 benchmark](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026),
[Google six failures](https://medium.com/google-cloud/the-six-failures-of-text-to-sql-and-how-to-fix-them-with-agents-ef5fd2b74b68),
[ReFoRCE](https://arxiv.org/pdf/2502.00675),
[LinkAlign](https://aclanthology.org/2025.emnlp-main.51.pdf) ·
FinOps — [AWS Cost RCA](https://aws.amazon.com/blogs/aws-cloud-financial-management/faster-anomaly-resolution-with-enhanced-root-cause-analysis-in-aws-cost-anomaly-detection/),
[Vantage](https://www.vantage.sh/blog/cloud-cost-anomaly-detection) ·
Anthropic — [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents),
[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

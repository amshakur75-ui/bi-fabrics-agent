# 09 — Proven Agent-Design Patterns vs. Our Raw-Loop (bi-fabrics-audit-agent)

> Scope: assess the established **agent-design patterns** against THIS use case — a **read-only** Microsoft Fabric / Power BI **capacity *investigator***. The proposed brain is an authored **MLflow `ResponsesAgent`** running a **raw Anthropic Messages tool-loop** that calls read-only MCP tools + deterministic coded "playbook" tools, forms **ranked hypotheses + assumptions + confidence**, and **falls back across telemetry sources**. Constraints that drive every verdict below: **reliability is paramount** ("no room for error"), **cost-sensitivity is first-class** (the audited capacity may be small/trial), and **read-only is absolute**. Currency: 2024–2026 papers + practitioner guidance.
>
> **Headline (TL;DR).** The **raw tool-loop is the correct base** — it *is* the canonical "agent" in Anthropic's taxonomy, and our investigation is genuinely open-ended (you don't know which telemetry source will answer, so you can't pre-write the path). Do **not** rewrite it as plan-and-execute or a big state machine. Instead **fold three proven patterns into the loop**: (1) **text-to-SQL/KQL reliability** — schema-grounding + pre-execution validation + retry-on-error (this is the single highest-leverage reliability win, because our tools emit DAX/KQL/SQL against Fabric telemetry); (2) **confidence calibration + abstention** — make "insufficient evidence → abstain / lower confidence" a first-class verdict, which the agent's hypothesis-ranking already gestures at; (3) a **bounded evaluator/self-critique gate** (one cheap pass, capped) on the *final* verdict only — Reflexion's idea, minus its multi-trial cost. ReAct is already what we do. Plan-and-execute, self-consistency (N-sampling), full Reflexion trial loops, and a hand-built RCA framework are **overkill / cost-hostile** here. Wrap the whole loop in the deterministic-workflow shell from doc 05 (a *workflow* triggers the *agent*).

---

## 0. Framing: which of these are workflows, which are agents (Anthropic's line)

Anthropic's *Building Effective Agents* draws the load-bearing distinction the rest of this doc hangs on:

- **Workflows** are "systems where LLMs and tools are orchestrated through **predefined code paths**."
- **Agents** are "systems where LLMs **dynamically direct their own processes and tool usage**, maintaining control over how they accomplish tasks." Agents "begin with user direction, then plan and operate independently … They operate in loops, using tools based on environmental feedback."
- The foundational principle is explicit: **"find the simplest solution possible, and only increasing complexity when needed."** Workflows give "predictability and consistency for well-defined tasks"; agents suit "flexibility and model-driven decision-making" at scale. ([anthropic.com/research/building-effective-agents](https://www.anthropic.com/research/building-effective-agents); engineering mirror [anthropic.com/engineering/building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents))

**Why our raw loop is an *agent*, not a *workflow*:** a capacity investigation is not a well-defined task with a knowable control flow. The agent doesn't know up front whether the answer lives in Capacity Metrics, Activity Events, Log Analytics, or a DAX query plan — it must decide what to pull next *based on what the last pull showed*, and fall back when a source is empty. That is precisely the condition Anthropic says calls for an agent (model-driven decision-making) over a workflow (predefined path). So the design question is **not** "agent vs workflow" wholesale — it's "**which workflow-shaped reliability scaffolding do we wrap around / fold into the agent loop?**" That is exactly what Anthropic recommends: use the simplest composable pattern that works, and reserve autonomy for the genuinely open part. ([building-effective-agents](https://www.anthropic.com/research/building-effective-agents))

The five named **workflow patterns** — **prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer** — are the menu we draw the fold-ins from. ([building-effective-agents](https://www.anthropic.com/research/building-effective-agents); independent pattern map [agentpatterns.ai](https://www.agentpatterns.ai/agent-design/anthropic-effective-agents-framework/); [cloudflare/agents anthropic-patterns](https://github.com/cloudflare/agents/blob/main/guides/anthropic-patterns/README.md))

---

## 1. ReAct (Reason + Act) — *this is already our base; keep it*

**What it is.** Yao et al. (2022): "explore the use of LLMs to generate both **reasoning traces and task-specific actions in an interleaved manner**." The thought→action→observation loop lets the model "induce, track, and update action plans as well as handle exceptions," while actions "interface with external sources to gather additional information." Crucially for us: **"ReAct overcomes issues of hallucination and error propagation prevalent in chain-of-thought reasoning by interacting with a simple Wikipedia API"** — i.e. grounding each step in a real tool observation is itself an anti-hallucination mechanism. Reported gains: **+34% absolute** success on ALFWorld, **+10%** on WebShop vs. imitation/RL baselines. ([arxiv 2210.03629](https://arxiv.org/abs/2210.03629); [react-lm.github.io](https://react-lm.github.io/))

**How it applies.** Our raw Anthropic Messages tool-loop *is* ReAct in modern dress: the model emits a thought + a `tool_use` block (read-only MCP tool or deterministic playbook tool), receives a `tool_result`, and re-reasons. Native tool-calling has largely replaced the original brittle "Thought:/Action:" text parsing — the loop is now structured JSON, which is more reliable. ([medium: tool-calling agent vs ReAct](https://medium.com/@dzianisv/vibe-engineering-langchains-tool-calling-agent-vs-react-agent-and-modern-llm-agent-architectures-bdd480347692))

**Verdict: KEEP — it's the foundation, not an add-on.** The grounding-against-real-telemetry property is exactly why ReAct beats free-form CoT for an investigator: every claim is anchored to a tool observation. Do not replace it. The improvements below are *refinements of the act step and the stop step*, not a different base.

---

## 2. Plan-and-Execute / Planner-Executor — *overkill as the base; borrow only "re-plan gate" thinking*

**What it is.** A planner LLM writes a multi-step plan up front; an executor runs steps (often with a cheaper model) without re-consulting the planner each step; an optional re-planner revises. Practitioner-reported wins vs. ReAct: **lower latency and cost** ("executing multi-step workflows without consulting the larger agent after each action"; "use smaller, domain-specific models for sub-tasks, … heftier models saved for (re-)planning"). Best for "complex, multi-step tasks … with dependencies between steps." ([dev.to ReAct vs Plan-and-Execute](https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9); [dev.to three patterns 2026](https://dev.to/gabrielanhaia/react-plan-and-execute-or-reflection-the-three-agent-patterns-every-engineer-needs-in-2026-355p))

**The failure mode that disqualifies it as our base.** "A **brittle plan** can occur when the planner commits to a plan before seeing any tool output, so if step 2 returns something the planner did not anticipate, step 3 is already written and wrong." The standard fix is a **re-plan gate** (revise after K steps or on low-confidence output) — which BabyAGI and LangChain's Plan-and-Execute implement. ([dev.to comparison](https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9)) An investigation is the *paradigm* of "step N's result rewrites the plan" — empty Activity Events → pivot to Log Analytics → pivot to a DAX trace. A pre-committed plan would be wrong constantly and force near-continuous re-planning, erasing the cost advantage.

**How a *little* of it applies.** Keep the planning **inside** the ReAct loop as a lightweight, revisable "current hypothesis list + next-checks" the model maintains in its scratchpad (the doc-spec'd ranked hypotheses) — not as a separate frozen plan artifact. If you ever want the cost win, apply it *narrowly*: use a cheaper model for individual deterministic-tool result summarization, reserving the expensive reasoner for hypothesis updates (this is the "small model for sub-tasks" lever, scoped to a single tool result).

**Verdict: OVERKILL as the architecture; cost lever worth noting.** Do not adopt planner-executor as the top-level shape. The re-plan-gate *instinct* (don't over-commit; re-rank hypotheses each observation) is already native to ReAct.

---

## 3. Reflexion / self-critique / evaluator-optimizer — *adopt a BOUNDED single-pass critique on the final verdict only*

**What it is.** Reflexion (Shinn et al., NeurIPS 2023) reinforces an agent "not by updating weights, but instead through **linguistic feedback**": an **Actor** generates, an **Evaluator** scores, and a **Self-Reflection** model writes verbal feedback into an **episodic memory buffer** to improve the *next trial*. It is explicitly a **multi-trial** paradigm — re-attempt the task, carrying reflections forward. ([arxiv 2303.11366](https://arxiv.org/abs/2303.11366); [neurips 2023 poster](https://neurips.cc/virtual/2023/poster/70114); [promptingguide.ai/techniques/reflexion](https://www.promptingguide.ai/techniques/reflexion)) Anthropic's **evaluator-optimizer** workflow is the single-loop cousin: "one LLM call generates a response while another provides evaluation and feedback in a loop." ([building-effective-agents](https://www.anthropic.com/research/building-effective-agents))

**How it applies — and where to cap it.** Full Reflexion (repeat the whole investigation across trials, accumulating reflections) is **cost-hostile** for a cost-sensitive watchdog and risks redundant telemetry pulls. But a **single, bounded evaluator pass over the *final* verdict** is high-value and cheap: before emitting, a critic checks "is every ranked hypothesis backed by a cited tool observation? are confidence levels consistent with evidence density? did we abstain where data was sparse? any unsupported root-cause leap?" If it fails, allow **at most one** corrective revision (mirroring evaluator-optimizer's single loop, *not* Reflexion's open-ended trials). This directly serves "no room for error" by catching the classic agent failure of asserting a root cause the telemetry doesn't support.

**Verdict: ADOPT — but as a capped evaluator-optimizer gate (≤1 revision) on the final output, not multi-trial Reflexion.** Pair it with confidence/abstention (§7) since the critic's main job is to police "claim vs. evidence." This is one of the **3 to adopt**.

---

## 4. Agentic RAG — *mostly N/A; the "agentic" half is just ReAct over telemetry*

**What it is.** Agentic RAG replaces the fixed "retrieve-then-generate" pipeline with a loop where "the LLM acts as an orchestrator, deciding which actions to perform," iteratively querying sources and refining (Self-RAG, PlanRAG, Search-o1). Reported multi-hop gain in one survey: static RAG 34% → agentic 89%. ([arxiv 2506.10408 Reasoning RAG survey](https://arxiv.org/abs/2506.10408); [letsdatascience: self-correcting retrieval](https://letsdatascience.com/blog/agentic-rag-self-correcting-retrieval); experimental comparison [arxiv 2601.07711](https://arxiv.org/html/2601.07711v1))

**How it applies.** Our agent doesn't retrieve from a vector store over documents — it queries **structured telemetry** (Capacity Metrics, Activity Events, Log Analytics/KQL, DAX). The *agentic* property ("decide which source to hit next, fall back if empty, refine the query") is exactly our multi-source fallback — and we already get it from the ReAct loop. There is a **narrow** RAG use: grounding the *fix/coaching* advice and threshold semantics in a small, curated **knowledge base** of Fabric capacity guidance (SKU limits, throttling rules, refresh best practices) so the model cites docs rather than recalling them. The codebase already has a **KB-fallback reasoner** path (`reasoner_claude.py` → KB fallback on any API/parse error) — that is the right place for a tiny, static, well-curated KB, not a heavy retrieval stack.

**Verdict: OVERKILL as a subsystem; the pattern's value is already captured by ReAct + the existing KB fallback.** Don't build a retrieval pipeline. Optionally tighten the curated KB used for fix-advice grounding.

---

## 5. Text-to-SQL / KQL reliability (schema-grounding, validation, retry-on-error) — *ADOPT; highest-leverage reliability win*

**Why it's the top win.** Our deterministic "playbook" tools and the model's ad-hoc queries emit **DAX / KQL / SQL** against Fabric telemetry. The literature is blunt about where these fail: "**The most important Text-to-SQL failures are often semantic: hallucinated columns, ambiguous references, wrong joins, missing tenant filters, unsupported functions**" — and, critically, these "may **execute successfully but fail to faithfully capture intent**," unlike syntax errors which surface on their own. A query that runs but silently filters the wrong workspace is the worst case for "no room for error." ([arxiv text-to-SQL semantic validation 2510.14296 schema linking](https://arxiv.org/pdf/2510.14296); [dpriver: SQL semantic validation for LLM queries](https://www.dpriver.com/blog/sql-semantic-validation-for-llm-generated-queries/))

**The proven recipe to fold in:**
1. **Schema-grounding / schema-linking.** Put the relevant telemetry table/column schema (and the handful of legal join keys / required filters like CapacityId, time window) **in front of the model** before it writes a query, rather than relying on recall. Bidirectional/context-aware schema linking is the current reliability frontier. ([arxiv 2510.14296](https://arxiv.org/pdf/2510.14296))
2. **Pre-execution validation.** "A reliable pre-execution workflow should **parse generated SQL, bind it to catalog metadata, validate its semantic meaning, return structured feedback**, and only then move to … execution." For us this is a **deterministic, coded validator** (not an LLM): parse the DAX/KQL, check tables/columns exist, required filters present, read-only (no write/DDL) — fully compatible with the deterministic-playbook design. ([dpriver](https://www.dpriver.com/blog/sql-semantic-validation-for-llm-generated-queries/))
3. **Retry-on-error with structured feedback.** On validation/execution failure, feed the **structured error back into the loop** for one or two bounded self-corrections (RetrySQL / feedback-correction agents). This is just a specialized `tool_result` in the ReAct loop, so it costs us no new architecture. ([sciencedirect Intelli-Dispatch-SQL agent](https://www.sciencedirect.com/science/article/pii/S2666546825001235))
4. **Adaptive abstention on queries** (ties to §7): if the validator can't bind the query confidently, abstain / ask for a narrower question rather than run a guess. ([arxiv 2501.10858 Reliable Text-to-SQL with Adaptive Abstention](https://arxiv.org/pdf/2501.10858))

**Verdict: ADOPT — schema-grounding + deterministic pre-execution validation + bounded retry.** This is the **single most reliability-positive change**, it's cheap (the validator is code, not tokens), it directly enforces read-only, and it slots straight into the existing deterministic-tool layer. One of the **3 to adopt**.

---

## 6. Root-Cause-Analysis (RCA) agent designs — *borrow the framing, don't build the framework*

**What the field shows (2025–2026).** RCA agents have "evolved from shallow, single-hop explanations to **multi-step causal reasoning over logs and symptoms**," with the best systems doing "systematic reasoning that **deduces causes rather than guessing**" (e.g. OpenRCA's multi-step causal queries; Flow-of-Action's SOP-enhanced multi-agent RCA at WWW 2025; RCAEval / Cloud-OpsBench benchmarks). But the **cautionary** findings are the more important ones for us: agents are **fragile at tool-calling** ("SRE-Agent+CT invoked code tools for only 46% of runs in which code analysis was required … resulting in low RCA accuracy and inconsistent execution trajectories"), and a 2026 study is titled, pointedly, *"Stalled, Biased, and Confused: Uncovering Reasoning Failures in LLMs for Cloud-Based RCA."* ([arxiv 2403.04123 LLM agents for RCA](https://arxiv.org/pdf/2403.04123); [clickhouse: can LLMs replace on-call SREs](https://clickhouse.com/blog/llm-observability-challenge); [arxiv 2601.22208 Stalled, Biased, Confused](https://arxiv.org/html/2601.22208v1); [arxiv 2603.00468 Cloud-OpsBench](https://arxiv.org/pdf/2603.00468))

**How it applies.** The *valuable* RCA idea is the discipline: **enumerate candidate causes, gather evidence per candidate, rank, and only assert a cause backed by evidence** — exactly the doc-spec'd "ranked hypotheses + assumptions + confidence." The right way to encode this is **structured output** (a fixed hypothesis schema: `{cause, supporting_observations[], confidence, assumptions[]}`) plus a couple of **deterministic "playbook" probes** that map a symptom to the specific telemetry to pull (the SOP idea, but coded — i.e. a *workflow* fragment, not an LLM sub-agent). The literature's warnings argue **against** a heavyweight multi-agent RCA framework: more agents = more tool-call fragility and cost.

**Verdict: ADOPT the framing (structured ranked-hypothesis schema + a few coded symptom→telemetry playbooks); REJECT building a multi-agent RCA system.** The framing is one of the **3 to adopt** (it's the connective tissue between §3 critique, §5 query reliability, and §7 confidence). The framework is overkill and the evidence says multi-agent RCA *hurts* reliability/cost at our scale.

---

## 7. Confidence calibration + abstention on sparse data — *ADOPT; core to "no room for error"*

**What it is.** The 2025 TACL survey *"Know Your Limits: A Survey of Abstention in LLMs"* organizes the field into five mechanisms: **reflective prompting, uncertainty quantification, selective prediction/abstention, retrieval-based verification, confidence calibration** — note these overlap with §3 (critique), §5 (validation), §4 (grounding). The central problem for us: **"LLM token-level probabilities correlate weakly with semantic correctness,"** so naïve "logprob = confidence" is unreliable; consistency-based signals work better for black-box models. Selective prediction is the explicit "trade accuracy against coverage by **rejecting uncertain inputs**." ([github LLM-Honesty-Survey / TACL 2025](https://github.com/SihengLi99/LLM-Honesty-Survey); [arxiv 2407.16221 Do LLMs Know When to NOT Answer](https://arxiv.org/pdf/2407.16221); [arxiv 2311.09677 R-Tuning "I Don't Know"](https://arxiv.org/pdf/2311.09677); production view [zylos.ai LLM calibration in production agents](https://zylos.ai/research/2026-04-18-llm-calibration-uncertainty-production-agents))

**How it applies (this is mission-critical for a watchdog).** A capacity audit frequently runs on **sparse/partial telemetry** (trial SKU, missing Activity Events, no Log Analytics). The agent must be able to say **"insufficient evidence — abstain / low confidence"** rather than fabricate a confident root cause that triggers a false alert or, worse, a wrong recommendation. Concretely:
- Make **abstention a first-class verdict** alongside the ranked hypotheses (the schema in §6 already carries `confidence`; add an explicit `verdict ∈ {finding, watch, insufficient_evidence}`).
- Derive confidence from **evidence density and source corroboration** (how many independent telemetry sources agree), not from token logprobs — aligned with the survey's "consistency-based / corroboration" guidance and our multi-source fallback design. ([TACL survey](https://github.com/SihengLi99/LLM-Honesty-Survey))
- **Calibrate thresholds** so the watchdog only alerts above a confidence bar; below it, log a "watch" item, not an alert (controls false-positive cost and alert fatigue). The bounded critic in §3 enforces that stated confidence matches evidence.

**Verdict: ADOPT — abstention as a first-class verdict + evidence-density confidence + alert thresholding.** This is the third of the **3 to adopt**, and arguably the one most directly serving "no room for error."

---

## 8. Self-consistency / verification (N-sampling, majority vote) — *mostly OVERKILL; use cheap deterministic verification instead*

**What it is.** Self-consistency samples **multiple reasoning paths** and takes the **majority answer**; it reliably beats single-shot CoT on arithmetic/commonsense/multi-hop and "outperforms multi-agent debate" in some 2024 work. But: **"SC incurs significant computational costs at inference time due to its requirement for multiple sampling iterations"** (N× the tokens). ([arxiv 2505.09031 CoT+RAG+SC+self-verification](https://arxiv.org/abs/2505.09031); [arxiv 2408.17017 Reasoning-Aware Self-Consistency](https://arxiv.org/pdf/2408.17017))

**How it applies.** N-way sampling of an entire investigation is **directly at odds with cost-sensitivity** (each sample re-runs telemetry tool calls). And majority-vote assumes a single discrete answer; our output is a structured set of ranked hypotheses, where "the majority" is ill-defined. The *cheaper substitute* that buys most of the reliability: our **deterministic verification already grounds answers** — the query validator (§5), multi-source corroboration (§7), and the single critic pass (§3) collectively play self-consistency's role without N× cost. If a *specific* high-stakes scalar must be nailed (e.g. "is peak CU% truly over 100?"), recompute it **deterministically in code** rather than sampling the LLM. Reserve any LLM self-consistency for a single, narrow, high-impact judgment — and even then prefer recompute-in-code.

**Verdict: OVERKILL as a general mechanism (cost-hostile); replace with deterministic verification + corroboration.** Do not N-sample investigations.

---

## 9. "Investigation as a DAG of sub-questions" — *adopt LIGHTLY, as model-maintained structure, not a built executor*

**What it is.** Decomposition methods — **least-to-most** (decompose into ordered sub-problems, solve sequentially building on prior answers) and **decomposed prompting** (modular sub-tasks, sequential *or parallel*) — improve complex/compositional reasoning and interpretability. Graph/Tree-of-Thought generalize this to non-linear structures. ([arxiv 2205.10625 Least-to-Most](https://arxiv.org/pdf/2205.10625); [prompthub least-to-most guide](https://www.prompthub.us/blog/least-to-most-prompting-guide); [arxiv 2401.14295 Chains/Trees/Graphs of Thoughts](https://arxiv.org/pdf/2401.14295))

**How it applies.** A capacity investigation *is* naturally a DAG of sub-questions ("is there a hot capacity?" → "which item drives it?" → "who is the top user?" → "is it interactive or background?"). The benefit is **interpretability and coverage** (don't skip a branch). But building a real DAG **executor/scheduler** is a step toward orchestrator-workers / plan-and-execute — and §2 already argued that frozen structure is wrong for an investigation. The pragmatic capture: have the model **maintain the sub-question tree in its scratchpad / structured state** (which sub-questions are open, answered, blocked-by-missing-data) as part of the ReAct loop — interpretability and completeness without a bespoke scheduler. The existing **30% concentration alert** (User → Item → Owner) is literally a small fixed DAG and is a fine candidate to encode as a **deterministic playbook** (a workflow fragment), since its structure *is* known in advance.

**Verdict: ADOPT LIGHTLY as model-maintained structured state; encode only the KNOWN sub-DAGs (e.g. the 30% drill-down) as deterministic playbooks. Do NOT build a general DAG executor.** Folds into the §6 framing rather than being a separate adoption.

---

## 10. Deterministic-workflow-with-LLM-steps (Anthropic workflows vs agents) — *ADOPT as the outer shell (already in doc 05)*

**What it is.** Anthropic's whole point: prefer **composable workflow patterns** (predefined code paths with LLM steps) and reserve **autonomous agents** for the genuinely open part — "**find the simplest solution possible, and only increasing complexity when needed.**" ([building-effective-agents](https://www.anthropic.com/research/building-effective-agents))

**How it applies — the synthesis.** The right overall shape is a **thin deterministic workflow that wraps an agentic core**:
- **Outer (workflow / deterministic):** the doc-05 watchdog shell — scheduled Lakeflow Job → collect raw telemetry deterministically → **invoke the agent** for the open-ended reasoning → deterministic post-steps (validate findings schema, threshold confidence, MERGE to Delta, alert). This is "routing/chaining" in Anthropic terms, in code.
- **Inner (agent / ReAct loop):** the raw Anthropic tool-loop doing hypothesis formation, multi-source fallback, query-with-validation (§5), maintaining the sub-question state (§9), ending with a bounded critic (§3) and a calibrated abstention-aware verdict (§7).

This keeps autonomy scoped to where it earns its keep (the investigation) and everything else deterministic, cheap, and auditable — which is the cost-and-reliability sweet spot for this agent.

**Verdict: ADOPT as the framing for the whole system.** The deterministic shell is the doc-05 design; this doc's job is to specify the agentic core inside it.

---

## 11. Scorecard & final recommendations

| Pattern | Verdict for THIS use case | Worth the complexity? |
|---|---|---|
| **ReAct** (§1) | **KEEP — it's the base.** Grounding each step in a tool observation is itself anti-hallucination. | n/a (already there) |
| **Text-to-SQL/KQL reliability** (§5) | **ADOPT** — schema-grounding + deterministic pre-exec validation + bounded retry. | **Yes — highest-leverage; validator is cheap code.** |
| **Confidence calibration + abstention** (§7) | **ADOPT** — abstention as a first-class verdict; evidence-density confidence; alert thresholds. | **Yes — directly serves "no room for error" + curbs false alerts.** |
| **Bounded evaluator/critique** (§3) | **ADOPT** — single capped pass (≤1 revision) on the *final* verdict (evaluator-optimizer, not multi-trial Reflexion). | **Yes — cheap, catches claim-without-evidence.** |
| **RCA framing** (§6) | Adopt the *framing* (ranked-hypothesis schema + a few coded playbooks). | Framing yes; multi-agent RCA **no**. |
| **Investigation-as-DAG** (§9) | Adopt *lightly* (model-maintained sub-question state; encode only known sub-DAGs). | Light yes; DAG executor **no**. |
| **Deterministic shell** (§10) | **ADOPT** as outer wrapper (doc-05). | Yes — cost/audit win. |
| **Plan-and-Execute** (§2) | **OVERKILL** as base (brittle plans in an investigation); note the cheap-model sub-task cost lever. | No. |
| **Full Reflexion (multi-trial)** (§3) | **OVERKILL** — re-running investigations is cost-hostile; use the capped single pass instead. | No. |
| **Agentic RAG subsystem** (§4) | **OVERKILL** — the agentic property is already ReAct; keep a tiny curated KB for fix-advice. | No. |
| **Self-consistency (N-sampling)** (§8) | **OVERKILL** — N× cost; replace with deterministic recompute + corroboration. | No. |

### The 3 patterns we SHOULD adopt
1. **Text-to-SQL/KQL reliability** — schema-grounding + deterministic pre-execution validation + retry-on-error. Biggest reliability win, cheap, enforces read-only.
2. **Confidence calibration + abstention** — make "insufficient evidence" a first-class verdict; confidence from evidence density/corroboration (not logprobs); threshold alerts.
3. **Bounded evaluator/self-critique gate** — one capped pass on the final verdict (evaluator-optimizer), policing claim-vs-evidence; *not* multi-trial Reflexion.
(Plus the connective **RCA ranked-hypothesis framing** and the **deterministic outer shell** from doc 05, which together host these three.)

### Overkill / reject
Plan-and-execute as the base; full multi-trial Reflexion; a heavyweight agentic-RAG retrieval subsystem; self-consistency N-sampling; a multi-agent RCA framework; a general DAG executor.

### Is the raw loop still the right base?
**Yes.** The investigation is genuinely open-ended (unknown-until-you-look telemetry, multi-source fallback) — the exact condition Anthropic reserves for *agents* over *workflows*. A raw ReAct tool-loop is the simplest thing that handles it, and "find the simplest solution possible" is the governing principle. **Do not** switch to plan-and-execute (brittle plans) or a large LLM-driven state machine (premature complexity, more tool-call fragility per the RCA literature). The correct move is to **keep the raw loop and harden it** with the three fold-ins above, all wrapped in the deterministic doc-05 shell. A *small* deterministic state machine is appropriate only for the **outer** shell and for **known** sub-DAGs (e.g. the 30% concentration drill-down) — not for the investigation itself.

---

## Sources
- Anthropic, *Building Effective AI Agents* — workflows vs agents, five patterns, "simplest solution possible", ACI/tool design: [anthropic.com/research/building-effective-agents](https://www.anthropic.com/research/building-effective-agents) · engineering mirror [anthropic.com/engineering/building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents) · independent map [agentpatterns.ai](https://www.agentpatterns.ai/agent-design/anthropic-effective-agents-framework/) · [cloudflare/agents anthropic-patterns](https://github.com/cloudflare/agents/blob/main/guides/anthropic-patterns/README.md)
- ReAct — Yao et al. 2022: [arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629) · [react-lm.github.io](https://react-lm.github.io/)
- Reflexion — Shinn et al., NeurIPS 2023: [arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366) · [neurips.cc poster](https://neurips.cc/virtual/2023/poster/70114) · [promptingguide.ai/techniques/reflexion](https://www.promptingguide.ai/techniques/reflexion)
- Plan-and-Execute vs ReAct: [dev.to/jamesli](https://dev.to/jamesli/react-vs-plan-and-execute-a-practical-comparison-of-llm-agent-patterns-4gh9) · [dev.to three patterns 2026](https://dev.to/gabrielanhaia/react-plan-and-execute-or-reflection-the-three-agent-patterns-every-engineer-needs-in-2026-355p) · tool-calling vs ReAct [medium/dzianisv](https://medium.com/@dzianisv/vibe-engineering-langchains-tool-calling-agent-vs-react-agent-and-modern-llm-agent-architectures-bdd480347692)
- Text-to-SQL reliability: schema linking [arxiv 2510.14296](https://arxiv.org/pdf/2510.14296) · semantic validation [dpriver.com](https://www.dpriver.com/blog/sql-semantic-validation-for-llm-generated-queries/) · adaptive abstention [arxiv 2501.10858](https://arxiv.org/pdf/2501.10858) · agent for reliable text-to-SQL [sciencedirect S2666546825001235](https://www.sciencedirect.com/science/article/pii/S2666546825001235)
- RCA agents: [arxiv 2403.04123](https://arxiv.org/pdf/2403.04123) · [clickhouse LLM observability challenge](https://clickhouse.com/blog/llm-observability-challenge) · [arxiv 2601.22208 Stalled, Biased, Confused](https://arxiv.org/html/2601.22208v1) · [arxiv 2603.00468 Cloud-OpsBench](https://arxiv.org/pdf/2603.00468)
- Self-consistency / verification: [arxiv 2505.09031](https://arxiv.org/abs/2505.09031) · [arxiv 2408.17017 Reasoning-Aware SC](https://arxiv.org/pdf/2408.17017)
- Confidence / abstention: TACL 2025 honesty/abstention survey [github SihengLi99/LLM-Honesty-Survey](https://github.com/SihengLi99/LLM-Honesty-Survey) · [arxiv 2407.16221](https://arxiv.org/pdf/2407.16221) · [arxiv 2311.09677 R-Tuning](https://arxiv.org/pdf/2311.09677) · production [zylos.ai](https://zylos.ai/research/2026-04-18-llm-calibration-uncertainty-production-agents)
- Agentic RAG: [arxiv 2506.10408 Reasoning RAG survey](https://arxiv.org/abs/2506.10408) · [letsdatascience self-correcting retrieval](https://letsdatascience.com/blog/agentic-rag-self-correcting-retrieval) · [arxiv 2601.07711 is agentic RAG worth it](https://arxiv.org/html/2601.07711v1)
- Decomposition / DAG: least-to-most [arxiv 2205.10625](https://arxiv.org/pdf/2205.10625) · [prompthub guide](https://www.prompthub.us/blog/least-to-most-prompting-guide) · Chains/Trees/Graphs of Thoughts [arxiv 2401.14295](https://arxiv.org/pdf/2401.14295)

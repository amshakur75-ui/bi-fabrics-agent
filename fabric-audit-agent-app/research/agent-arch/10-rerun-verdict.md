# Rerun verdict — gaps, prior art, proven methods (2026-06-29)

**Question:** re-check the recommended agent architecture for gaps/weaknesses, survey how others built similar agents, and confirm there's no better approach.

**Verdict: the architecture holds, and it is now validated — not a bet.** ~12 production systems independently converged on the *same* shape we designed. No better fundamental approach surfaced. The rerun produced **one architectural refinement**, a **sharper security posture**, and a **list of proven improvements**.

## The convergent pattern (proof we're right)
Datadog **Bits AI SRE** (GA, 2,000+ envs), **Cleric**, **Resolve.ai**, Microsoft **Azure SRE Agent**, **Grafana** (Sift + Assistant Investigations), **New Relic** (Autopilot/SRE Agent), **k8sgpt**, **Honeycomb**, **incident.io**, **Rootly**, **Parity**, and the cloud **FinOps cost investigators** (AWS AI-Powered Cost Investigations, Azure/GCP) all use:
- a **hypothesis-driven investigation loop** (plan → gather *targeted* evidence → confirm/rule-out hypotheses → structured report) — exactly our raw-loop/ReAct base;
- **evidence-cited conclusions** (every claim links to the exact query/data) — the universal #1 anti-hallucination lever;
- a first-class **"inconclusive"** verdict + confidence;
- **read-only investigate + propose; actions human-gated**;
- **memory / similar-incident recall** ("we saw this last month, here's what we concluded").

## The one refinement: detectors are the source of truth, the LLM only explains
k8sgpt ("analyze deterministically, *then* explain"), Grafana (Sift checks feed the LLM), and Dynatrace ("causal AI grounds generative AI") all put **deterministic detection first** and use the LLM only to correlate/hypothesize/narrate over *confirmed* findings. This bounds hallucination to wording, not diagnosis. **We already have the deterministic detectors** (capacity/throttle/concentration/model/refresh/security/cost) — so the principle is: the LLM never decides *whether* there's a problem; it explains and correlates what the detectors found. This is a strong validation of the existing architecture + a clear guardrail.

## Sharpened must-fixes
1. **"Read-only" does NOT prevent data exfiltration** (the sharpest finding). Exfiltration needs only an *outbound channel* — not a write tool: markdown-image/data-in-URL, links, tool-call params (Willison's "lethal trifecta"; EchoLeak CVE-2025-32711, **NVD 7.5 HIGH** — the widely-quoted "9.3" is the discoverer's rating, not NVD's). **Mitigate:** no outbound/URL-fetch tools; **sanitize/disable auto-rendered markdown links + images in Teams output**; egress allow-list + network isolation; treat untrusted telemetry text as data not instructions (spotlighting/delimiting drops injection success >50%→<2%).
2. **Overgeneralization on sparse data is empirically severe** — LLMs overgeneralize in 26–73% of summaries, "be accurate" prompting does NOT fix it, newer models are worse (Peters & Chin-Yee 2025). The "100%-of-one-workspace" bug *is* this failure. **Mitigate:** structurally require the agent to state **coverage/denominator/sample-size + assumptions + calibrated confidence**; make "insufficient evidence" a first-class output; add a groundedness/critic pass (every claim must trace to a tool observation).
3. **Enforce read-only structurally, never by prompt** (every text-to-SQL source: prompt guardrails were "bypassed within minutes"). Read-only identity/role + parse-time allowlist (single SELECT only) + ideally a capability/typed-query model + resource/row caps + audit log.
4. **If the agent generates KQL:** schema-grounding + curated **verified example queries** + **value dictionaries** (real literals) + parse→execute→repair loop with a **hard retry cap**; pre-flatten nested telemetry into typed views; the dangerous failure is *silent-wrong* (parses but wrong scope/join). A **semantic/metric layer** (define CU%/throttle/etc. once) takes accuracy ~40%→90%+ and fails *loudly* (out-of-scope) instead of returning a plausible wrong number.

## Proven improvements to adopt
- **Hypothesis tree** with explicit validated/invalidated/**inconclusive** labels (Datadog/Azure/Grafana/Parity); show ruled-out paths.
- **Cite the exact telemetry** behind every claim.
- **Similar-finding recall** from the Delta findings-history (the memory payoff; cheap, high-trust, read-only).
- A **`bits.md`-style org-context file** (naming, scope rules, *known noise / false positives*) — cheap grounding win (Datadog).
- **Eval harness early, with injected noise** + golden labeled investigations + LLM-judge (position/verbosity/self-pref-bias-controlled) + **groundedness** + **trajectory** scoring; allow multiple-correct; thumbs feedback → new examples (Datadog/Cleric/LinkedIn/Genie). Clean fixtures overstated quality ~11%.
- **Cost guardrails**: targeted per-hypothesis queries (not pull-all — Datadog's token-blowup lesson), hard retry caps, deep-investigate only on real triggers, pay-per-token + scale-to-zero, budget caps (~$25–50/investigation at Datadog scale).
- **Provider/model abstraction** (`ModelBase`/`ModelLarge`-style) + stream + MCP, as Grafana's LLM app does — keeps us off a single model.
- **Human-in-the-loop as first-class**: stream progress, let the human inject hints mid-run; frame the agent as "a junior engineer presenting findings for review" (Grafana/Cleric).

## Net
No change to the core decision (authored ResponsesAgent + raw ReAct loop on a Databricks App, MCP tools, Delta memory, OBO read-only, watchdog = scheduled SP job). The rerun **hardens** it: detectors-ground-the-LLM, structural read-only + anti-exfiltration, forced coverage/abstention, and an eval harness — all drawn from systems already proven in production.

## HolmesGPT — the closest open-source analog (port these specifics)
HolmesGPT (Robusta; CNCF Sandbox; Microsoft co-maintained; read-only, RBAC-respecting RCA agent — source readable) is the most directly applicable reference. Confirms our shape and gives implementation-grade detail to fold into the spec:
- **YAML "toolset" abstraction, one per data source**, where each tool's rich natural-language `description` (exact returned fields, time-window param, gotchas) *is* the intelligence. Map to: `capacity-metrics`, `workspace-monitoring`, `log-analytics`, `fabric-admin`, `semantic-model` read-only toolsets; expose projection/aggregation tools so only needed columns enter context.
- **Runbooks/Skills mattered MORE than model choice** (4.6 vs 3.6 quality; tool calls 16→2; ~40% of known patterns auto-resolve). `SKILL.md` = Goal → Workflow → Synthesize → Remediation, matched by description, fetched early, **with tool-scoping metadata** (which queries are in/out of scope). → author capacity runbooks: throttling/overload, background-op buildup, noisy-neighbor item, refresh failures, autoscale/surge-protection.
- **Hard step budget + force-answer on the last step** (strip tools so the model must conclude); parallel independent tool calls; **TodoWrite plan-first**; an **identical-tool-call dedup safeguard** ("already ran this exact query — move on"), which is *sound precisely because the agent is read-only*.
- **3-layer context bounding** (telemetry is huge): per-tool `llm_summarize` transformer above an `input_threshold` (≤50%, keep aggregates/outliers, grep-ready); **spill oversized result to disk + leave a pointer**; conversation **compaction** near the window limit; plus a `MAX_GRAPH_POINTS`-style downsample (~300 points) and always inject current time + force a bounded window.
- **Calibrated-confidence output + a mandatory Final-Review self-critique** that re-reads the question and traces every claim back to tool evidence, downgrading unverifiable claims to "likely/possible"; numbered competing hypotheses; exact names (capacity/workspace/item/operationId/timestamp). Plus **error-semantics anti-hallucination rules** adapted to Fabric (a throttled/429 response *confirms* throttling; never invent a CU value you didn't read; never claim an item is absent just because it's not in a listing; report name-mismatches, never silently merge).
- **Dynamic capability advertisement** — tell the model at runtime which toolsets are enabled/failed and *why*, so it says "I can't see Workspace Monitoring — enable it here" instead of hallucinating. (Directly addresses our pending Workspace-Monitoring dependency + the BLIND-vs-EMPTY must-fix.)
- **Read-only is the load-bearing invariant; any action is a separate opt-in behind an approval gate.** Cost reference: ~$0.04/investigation, ~$12/mo with a capable model. Failure lessons: small/self-hosted models break tool-calling; runbooks prevent step-budget blowouts; add workload/capacity-level **dedup fingerprinting** to avoid investigation storms; consider **name/ID anonymization** before sending tenant identifiers to the LLM (a privacy gap critics flagged in Holmes — worth closing for an enterprise BI tenant, and it dovetails with the spotlighting/exfil mitigations).

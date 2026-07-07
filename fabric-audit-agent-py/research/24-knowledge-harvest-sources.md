# Knowledge Harvest Sources — skills/content to absorb (researched 2026-07-06)

Feeds the Phase-4 verified-query library, runbooks, system prompt, and (P5) BPA-style detector
rules. Absorption = adapt into OUR files; nothing executed. Re-verify every adapted query against
live schema before it enters the "verified" library. License discipline per source, below.

## ⭐ The machine-readable prize: Tabular Editor BPA rules (MIT copies)

BPA rules = JSON anti-pattern detectors (ID, Category, Severity, Scope, boolean Expression,
Description, optional FixExpression). Feeds: (a) P5 model-internals detector rules near-verbatim,
(b) rule Descriptions → system-prompt anti-pattern knowledge NOW, (c) rule names → query-library
question templates ("which models violate X").

- **USE (MIT):** `microsoft/Analysis-Services` → `BestPracticeRules/BPARules.json`
  (https://github.com/microsoft/Analysis-Services/tree/master/BestPracticeRules)
- **USE (MIT, easiest to transform):** `microsoft/semantic-link-labs` →
  `src/sempy_labs/_model_bpa_rules.py` (~60+ rules as a DataFrame w/ doc URLs)
- **DO NOT COPY (unlicensed):** `TabularEditor/BestPracticeRules` — freshness cross-reference only.

## MIT sources (adapt text/code directly, with attribution)
1. **`microsoft/semantic-link-labs`** — VertiPaq Analyzer in Python, Direct Lake guardrail
   checks, Model-Optimization + BPA notebooks → detector logic, model-optimization runbook,
   guardrail thresholds. (Absorb read logic only; it also has write APIs — never call.)
2. **`microsoft/PowerBI-LogAnalytics-Template-Reports`** — official .pbit templates over
   `PowerBIDatasetsWorkspace`: Microsoft's own KQL/M for query-performance, refresh, and user
   activity → the highest-authority verified-query-library seed. Extract + adapt the KQL.
3. **`github/awesome-copilot`** — SKILL.md skills: `powerbi-modeling`,
   `power-bi-model-design-review` (a review-discipline prompt), `power-bi-report-design-consultation`
   → absorb near-verbatim into skills/system prompt.

## Microsoft Learn (CC-BY-4.0 — paraphrase + attribute)
4. **Capacity troubleshooting trio** (step-by-step incident runbooks — map 1:1 onto ours):
   capacity-planning-troubleshoot-consumption / -throttling / -errors. Pair with: throttling
   (10s/60s/24h stages), optimize-capacity, plan-capacity, capacity-planning-overview,
   semantic-model-operations (the `SemanticModelLogs` schema reference).
   → Upgrade our 3 runbooks + write a **"capacity semantics" knowledge file** (smoothing,
   burndown, throttling stages, autoscale, the 86,400 CU-seconds/day arithmetic).

## Cite + paraphrase only (blogs; re-verify all KQL before "verified" status)
5. **Chris Webb (crossjoin.co.uk)** — deepest public KQL corpus on PBI telemetry (Log Analytics
   series; Workspace-Monitoring alerts w/ KQL querysets) → query library + throttle runbook.
6. **SQLBI** — VertiPaq Analyzer docs, performance hub, DAX Query Plans whitepaper → the FE/SE
   bottleneck methodology for the system prompt's "how to reason about a slow query" section.
   (Books/courses are paid — do not absorb.)
7. **Phil Seamark (dax.tips)** — refresh visualization/decomposition (sub-operations,
   parallelism, overlap) → refresh-collision runbook core logic.
8. **Community WM/KQL bundle** — fabric.guru (SemanticModelLogs KQL), daxnoob (CU-spike
   attribution = our noisy-neighbor case), FourMoo query-usage series, Data Mozart
   bursting/smoothing explainer → query library + noisy-neighbor runbook + capacity-semantics.

## License traps (explicit)
- **GPL-3.0 — structure reference ONLY, never copy text:** `data-goblin/power-bi-agentic-development`
  (best SKILL.md-shape reference for organizing our skills), `danmeissner/KQL-for-PowerBI-Workspace-Logs`.
- **Unlicensed:** `TabularEditor/BestPracticeRules` (use the MIT copies above).

## What feeds what (summary)
| Target | Sources |
|---|---|
| Verified-query library (P4) | #2 templates, #5, #8 (re-verified), BPA rule names |
| Runbooks upgrade (P4) | #4 trio, #7, #8 |
| Capacity-semantics knowledge file (P4, new) | #4 + Data Mozart |
| System prompt (P4) | BPA descriptions, #3 review skill, #6 methodology |
| Model-internals detector rules (P5) | BPA JSON (MIT), #1 VertiPaq logic |

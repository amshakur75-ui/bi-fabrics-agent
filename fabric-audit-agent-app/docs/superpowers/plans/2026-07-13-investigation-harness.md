# Implementation Plan: Investigation Harness — v2
**v2 (opus plan review REVISE → addressed):** grounded-facts re-baselined vs HEAD (diagnose enum + `run_kql engine="la"` ALREADY exist on main — A2 rescoped to the normalize/teaching-error remainder, since shipped; C2 rescoped to verify-existing, LA-twin templates fold into A3 — NO new app-side LA POST, the one-firewall chokepoint stands); gate language downgraded to "LLM-cited, eval-enforced" + gate outputs REQUIRED as tool-result fields (new A1b); B2 ordering resolved: PRE-CALL deterministic keyword classifier sets the budget (boundary cases pinned; "is capacity healthy?" = lookup); B1 gets an explicit precedence rule (investigation→narrate, lookup→lean) + marker-collision check; D2 narration quality = live-verify only; trail surfaced in PLAIN LANGUAGE (no tool names/inputs); C1 trimmed to refresh_schedule + list_datasets (dataflows/capacity_state → v2); "tool count 18" = MCP set only; tier-ladder prompt wording says read/query only, never act-in-Fabric.

**Spec:** `docs/superpowers/specs/2026-07-13-investigation-harness-design.md` (v2: +LA Tier 3, +investigative thinking)
**Branch:** `feat/investigation-harness` (off `main` `73a1c1a`)
**Method:** superpowers SDD, TDD, per-task review; opus plan review before build. Offline/deterministic tests; live verify with the user at the end. Read-only absolute throughout.

## Overview
Turn the deployed agent into a gated, hypothesis-driven investigator: 9-step loop with STOP-gates,
engineer-style visible deduction, tiered data escalation (MCP tools → firewalled KQL → direct
Fabric/PBI REST + Azure Log Analytics), reachable diagnosis engine, investigation query templates,
literal-fix conclusions. Reuse the verified-working 70% (patterns/spike/timeline/verdict/concentration/KB).

## Grounded facts (code-verified 2026-07-13)
- `diagnose` handler `tools.py:1218` reads `inp.get("symptom")`; engine accepts exactly `throttle|refresh|slowness` (`investigation/diagnose.py:276-280`); free text → `{"error": "unknown symptom..."}`.
- `capacity_patterns`, `investigate_capacity_spike` (abstain/confidence/evidence), `user_timeline`, `run_kql`+firewall (`query/kql_guard`, `validate_adhoc_kql`), `query_library` (18th tool), verdict/concentration/CPU-proxy labels: all work.
- `whats_changed` needs history; prod path `/tmp` resets per redeploy — result already carries an honest note.
- **CORRECTED (review):** `run_kql` is already multi-engine — `engine="capacity"` (Kusto) AND `engine="la"` (`build_log_analytics_query` → `FABRIC_LA_WORKSPACE_ID`), both behind `validate_adhoc_kql` + the `[adhoc-kql]` audit line (tools.py:1498-1576). Direct firewalled LA querying EXISTS on main; 13 `la` templates already ship.
- Agent loop: `agent_server/agent.py::_run_tool_loop`, `max_steps=6`, freestyle; `_SYSTEM` duplicated (parity test enforces byte-identical); progress phrases map keyed by tool name.
- Suite baselines: package **1100**, agent app **82** (+49 subtests), evals 2/2 + 19/19.

## Phase A — package: gates, diagnose reachability, investigation templates
**A1 — `investigation/gates.py` (pure) + tests.** `evaluate_gates(payload)`-style pure helpers the tools/harness cite:
`throttle_claim_gate(evidence)` (throttle signal fired in-window ≠ CU%>100 — two claims, two gates),
`concentration_gate(share_pct)` (>30%, result labeled CPU-proxy), `null_data_gate(rows)` (empty/failed
source → INCONCLUSIVE never HEALTHY), `verdict_gates(findings, history)` (SIZE-UP: persistent throttle +
distributed + non-recovering; OPTIMIZE: named fixable + healthy headroom; else INCONCLUSIVE),
`TRUE_CU_PER_USER_BLOCKED` constant + explainer string (permanent; Metrics-app-only, SP-blocked).
ACs: each claim class blocked without its signal; null-data returns INCONCLUSIVE; gates pure/deterministic; docstrings cite the verified Microsoft-doc basis. Scope M. **SHIPPED (17 tests).**
**A1b — gates surfaced as tool-result FIELDS (review fix).** Gates are LLM-cited, eval-enforced discipline — NOT hard loop enforcement; citations must therefore reference real payload values: attach gate outputs to the relevant tool payloads (`diagnose` and the audit/verdict evidence carry a `gates` field). ACs: gate fields present; spec wording downgraded from "can't be talked around" to "cited from payload fields, eval-enforced". Scope S.
**A2 — `diagnose` reachability remainder (RESCOPED — enum+base description already on main). SHIPPED:** `_normalize_symptom` synonym mapping + teaching error (accepted values + guidance, no collector work on bad input) + funnel-stage description line; 6 diagnose tests green.
**A3 — investigation query templates + tests.** Add to `query_library.json` (verified, firewall-passing):
`throttle-attribution-join` (ExecutionMetrics `capacityThrottlingMs>0` join QueryEnd/CommandEnd on `XmlaRequestId`), `recurrence-daily-bins` (CpuTimeMs by user/item binned by day, `{window}`), `top-users-per-item` (share ranking, proxy-labeled), `refresh-window-contention` (CommandEnd durations by hour). Descriptions name WM (`SemanticModelLogs`/`ItemName`) vs LA (`PowerBIDatasetsWorkspace`/`ArtifactName`) column twins — verify names vs Microsoft docs before landing. ACs: every template passes `validate_adhoc_kql`; library coverage eval green; tool count stays 18. Scope M.
**A4 — tool descriptions teach the funnel.** Audit the 18 descriptions: each investigation tool names its funnel stage (confirm→attribute→who→why→recurrence) so the model picks tools like a practitioner. AC: schema-mirror updated; no behavioral change. Scope S.

## Phase B — agent app: the harness (prompt + loop)
**B1 — INVESTIGATION playbook in `_SYSTEM` (both copies, byte-identical) + tests.** New section encoding:
classify R/P → hypothesize (falsifiable) → name the gate → gather cheapest tier → reflect/STOP (gate fails → "ruled out", never reframe) → differential (5 standard competitors; never single-signal blame) → escalate tiers only when the lead demands → verify (every claim cites a tool-result field; confidence CONFIRMED/LIKELY/INCONCLUSIVE tied to gate outcome — maps onto existing validated/likely/inconclusive language) → conclude (root cause + literal KB fix + who acts) — **narrated with engineer-style visible deduction** (what I wondered, what I suspected, why I checked X next, what this rules out, what I now understand) for investigations — with an EXPLICIT precedence rule (review fix): investigation-classified → narrate the chase; lookup → the existing LEAN default wins; neither overrides any honesty rule. Tier-ladder wording says read/query only — never "act in Fabric". Marker-collision check vs the 8 parity-locked honesty markers before landing. Tier ladder named: MCP tools → firewalled KQL/templates → direct REST/LA → FUAM/VertiPaq (human/gated). True-CU-per-user permanently blocked, direct-the-admin wording. ACs: parity test green; new rule-lock tests (gate discipline, differential, null-data≠healthy, investigative-narration markers); all honesty markers preserved. Scope M/L (prompt-only, but load-bearing).
**B2 — dynamic step budget + trail + tests.** `_run_tool_loop`: investigation-classified requests get a higher budget (6→12); classification = **PRE-CALL deterministic keyword heuristic** (review fix — resolves the ordering: budget must exist before the first model call): investigation verbs/symptoms (investigate, why, root cause, what caused, diagnose, spike, recurring, has this happened, who is driving, dig into) → budget 12; else 6. Boundary pinned in tests: "is capacity healthy?" = lookup(6); "why did the capacity throttle yesterday?" = investigation(12). Trail: assembled from `trajectory` but **translated to PLAIN LANGUAGE** via the progress-phrase map (no tool names/inputs — review fix) into `custom_outputs.trail`; surfaced only on request. ACs: budget honored both ways; trail plain-language; forced-answer step still fires; failure-isolated. Scope M.

## Phase C — agent app: Tier 3 direct expansion
**C1 — `fabric_direct.py` new GET endpoints + tests (TRIMMED per review).** Add: `fabric_refresh_schedule`
(refresh schedule — contention analysis) + `fabric_list_datasets`. (dataflows/capacity_state → v2.)
Same pattern: GET-only, param-quoted, scrubbed, inert-unless-configured; descriptions follow A4 funnel style.
Note: these grow the DIRECT allowlist — "tool count stays 18" refers to the MCP set only.
(Admin activity-events endpoint: registered-DISABLED → Phase-7 admin-consent gate, mirroring outbound.) ACs: GET-only test extended; each endpoint schema'd; inert test still green. Scope M.
**C2 — RESCOPED (review): verify-existing, build NOTHING.** `run_kql engine="la"` already exists behind the one firewall+audit chokepoint — no new app-side LA POST is permitted (a second ad-hoc surface outside the chokepoint is exactly the slippery slope the invariant forbids). LA-twin investigation templates land in A3 (column names verified vs Microsoft docs there). AC: an explicit chokepoint-pin test asserting engine="la" input passes through `validate_adhoc_kql`. Scope XS.

## Phase D — remediation + evals
**D1 — KB literal-fix pass.** Extend `kb/*.py` so every investigation root cause maps to a NAMED fix + owner + source link (research-verified: stagger refreshes, incremental refresh, aggregations, bidirectional-filter removal, high-cardinality/auto-date-time trims, DirectQuery guidance, SKU sizing language). AC: KB-consistency test; no placeholder text reachable (extend the user-ranking regression pattern). Scope M.
**D2 — investigation eval cases.** Extend `eval/agent_cases.json` + scorer usage: (1) reactive gate-FAILS case → answer must say ruled out / not keep digging; (2) differential trap (item expensive for everyone; one user merely heaviest) → must not single-signal blame; (3) null-data case → INCONCLUSIVE not healthy; (4) recurrence question; (5) full funnel → named fix present. Scripted clients per existing golden-case pattern. Narration QUALITY is live-verify-only (review note) — evals check structural markers (ruled out / inconclusive / named fix), not prose. AC: eval suite green incl. new cases. Scope M.

## Phase E — deploy + live verify (shared infra — coordinate with user)
MCP app redeploy (package changes: A1–A4, C2) + agent app redeploy (B, C1). Bump versions (lockstep marker). Live verification session with the user: "what spiked yesterday?", "has this happened before?", "who's driving it?", "optimize or size up?" — check gates/differentials in the trail, literal fix in the conclusion, investigative narration quality.

## Checkpoints
- After A: package suite green (1100+new); firewall templates verified; diagnose enum in schema-mirror.
- After B: agent-app suite green (82+new); parity green; prompt honesty markers intact.
- After C: GET-only + firewall + inert proofs; tool count 18.
- After D: evals green incl. 5 new investigation cases.
- Final: opus adversarial whole-branch review → SHIP → merge → Phase E with user.

## Global constraints (verbatim into implementer+reviewer prompts)
Read-only ABSOLUTE (GET-only allowlists; LA query API is the sole read-POST exception, allowlisted exactly).
Never label proxy as authoritative (CPU-proxy labels; true-CU gate permanently blocked). Grounding bar
unchanged (firewall on ALL ad-hoc KQL; gates deterministic; claims cite tool-result fields). No secrets/real
IDs (public repo). camelCase data keys / snake_case ids; nullish-not-falsy; stdlib-only core; py≥3.10
(app ≥3.11). Keep suites green (1100 pkg / 82 app). Tool count stays 18. Schema names (`ItemName` vs
`ArtifactName`, `intendedUsage`, threshold fields) verified vs Microsoft docs before hard-coding.
Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
A1 gates ─┐
A2 diagnose ─┤→ B1 playbook → B2 loop ─┐
A3 templates ─┤                         ├→ D2 evals → E deploy+verify
A4 descriptions ┘        C1 direct, C2 LA ┘   D1 KB (parallel with C)
```

## Risks
| Risk | Mitigation |
|---|---|
| Prompt playbook bloats/conflicts with lean-voice rules | Investigations get depth; lookups stay lean — explicit carve-out; parity + rule-lock tests |
| LA-direct duplicates/bypasses the KQL firewall | Build C2 MCP-side (`run_kql engine=la`) behind the ONE firewall+audit chokepoint |
| Gates too rigid → agent can't handle novel questions | Gates constrain CLAIMS, not exploration; freestyle exploration stays, only conclusions are gated |
| Schema-name drift (ItemName/ArtifactName/threshold fields) | Verify vs Microsoft docs before hard-coding (A3/C2 AC) |
| Step-budget increase raises cost/latency on simple questions | Budget keyed to investigation classification; simple Q&A stays at 6 |
| Background-agent watchdog kills reviewers | Opus plan review dispatched time-boxed; self coverage/tech-accuracy pass meanwhile (pattern from Phase 6) |

## Open questions
- None blocking. (B2 classification heuristic and C2 engine-param shape decided at build with tests.)

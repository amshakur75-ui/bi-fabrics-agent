# Investigation Harness — Design Spec

**Date:** 2026-07-13 · **Status:** design, pre-plan · **Branch:** `feat/investigation-harness`
**Inputs:** (a) deep-research report (13 findings, all 3-0 verified vs primary Microsoft docs, run `wf_2bd7708f-99f`);
(b) user-supplied practitioner write-up (schema/gate detail — flagged items to verify before coding);
(c) **code-verified baseline** (smoke-tested 2026-07-13, below). User decisions: hybrid architecture (Option C);
data path = **both, tiered, with direct Fabric/Power BI access emphasized**; read-only stays absolute.

## Purpose
Upgrade the deployed agent from a thin tool-caller into a **gated, hypothesis-driven investigator**: it
classifies the question, forms falsifiable hypotheses, names the evidence gate that would confirm/kill each,
gathers the cheapest sufficient evidence, STOPS when a gate fails, runs differentials before blaming,
escalates data paths when a lead demands it, ties confidence to which gate passed, produces the literal fix,
and can show its investigation trail.

## Code-verified baseline (what EXISTS vs what actually WORKS today)
Smoke-tested via `create_tool_definitions()` exactly as the agent calls them:

| Capability | State | Evidence |
|---|---|---|
| `capacity_patterns` (recurring patterns) | **WORKS** | real narratives ("~5 users → 85% CU spike driven by Finance") |
| `investigate_capacity_spike` | **WORKS** | returns `abstained/confidence/coverage/evidence/result` |
| `user_timeline`, `spike_history`, `run_kql`, `query_library` | **WORKS** | verified earlier + live in prod |
| `diagnose` (causal-chain engine, `chain`+`rootCause`) | **EXISTS, BRITTLE** | accepts ONLY `throttle\|refresh\|slowness`; natural language → `{"error": "unknown symptom"}` — the deployed agent can't use its own best engine |
| `whats_changed` (recurrence) | **EXISTS, DATA-STARVED** | needs run history; prod history path is `/tmp` (resets each redeploy) → "no history" |
| `verdict.py` (healthy/optimize/size-up/unknown), concentration + user-concentration detectors, CPU-proxy honesty labels, validated/likely/inconclusive + ABSTAIN prompt rules | **WORK** | in the audit path today |
| Gated FUNNEL orchestration in the agent loop | **MISSING** | `agent_server/agent.py::_run_tool_loop` is freestyle tool-calling, max_steps=6, no hypothesis/gate/differential discipline |

**Conclusion:** ~70% of the *mechanics* exist; the missing 30% is (1) the reasoning harness that orchestrates
them, (2) formal STOP-gates, (3) interface fixes so the existing engines are actually reachable, (4) deeper
data paths for leads the tools don't cover.

## Verified domain foundation (encode, don't invent)
From the 3-0-verified research: Microsoft's **two distinct funnels** — REACTIVE throttling funnel
(CU%>100 in-window? → did a throttle signal actually fire (Interactive Delay / Interactive Rejection /
Background Rejection vs 100% line) and coincide? → Timepoint drill → workspace/item/op/user; **hard stops**
at each gate: "capacity never goes over 100% → End of analysis") and PROACTIVE consumption funnel (top
CU items 14d → when peaks → correlate CU vs operations AND user count). Key differential: spike+many
users = interactive; peak+few users = background/refresh. Lead-chain: CU → operation → item/model → user.
Escalation ladder: Metrics-app-equivalent (fast, 14d) → FUAM (long history) → VertiPaq (model root cause)
→ activity logs/semantic-link-labs (who). Throttle bands 10min/60min/24h; `capacityThrottlingMs > 0`
isolates throttled ops in Workspace Monitoring.

## Design — five components

### 1. The gated investigation loop (agent app — the core build)
Replace the freestyle loop with a **9-step disciplined loop**, implemented as (a) a structured
INVESTIGATION playbook section in the system prompt (both copies, parity-tested) + (b) code-side support
in `_run_tool_loop`:
CLASSIFY (Reactive vs Proactive — state it) → HYPOTHESIZE (one falsifiable sentence) → NAME THE GATE
(the specific data condition that confirms/kills it) → GATHER (cheapest sufficient source) → REFLECT
(gate failed → branch DEAD, say "ruled out", never reframe; passed → follow lead-chain one step) →
DIFFERENTIAL (rule out the standard competitor before blaming: single-item vs distributed; single-user vs
distributed; time-pattern vs chronic; interactive vs background; changed-at-a-date vs gradual) → ESCALATE
(only when the lead demands the next tier) → VERIFY (every claim traces to a tool-result field; else
downgrade/omit) → CONCLUDE (finding + root cause + the LITERAL fix + confidence) with an on-request TRAIL
(leads followed / ruled out / open questions).
Code-side: dynamic step budget (investigations get a higher `max_steps` than simple lookups — e.g. 6 →
12 when classified as an investigation), and the trail assembled from the existing `trajectory` +
per-step gate annotations.

### 2. STOP-gates (deterministic, LLM-can't-override)
New `investigation/gates.py` in the package (where detectors/evidence already live), surfaced through
tool results so the harness cites them:
- **Throttling-claim gate:** "throttling occurred" only if a throttle signal field actually fired in-window
  (from capacity events / `capacityThrottlingMs>0` / throttleMinutes evidence). CU%>100 alone ≠ throttling
  (smoothing) — these are two different claims with two different gates.
- **Concentration gate:** >30% share computed from data, labeled **CPU-proxy, not billed CU** (exists —
  formalize as gate).
- **True-CU-per-user: PERMANENTLY BLOCKED** (Metrics-app-only, SP-blocked). The agent may point the admin
  to the Timepoint Item Detail page; it may never state a per-user billed-CU figure.
- **Null-data gate:** empty/failed source → verdict INCONCLUSIVE ("data unavailable"), never HEALTHY
  ("no problems found"). Different statements.
- **Verdict gates:** SIZE-UP requires persistent throttle + distributed load + non-recovering debt;
  OPTIMIZE requires a named fixable item/user + healthy headroom otherwise; neither → INCONCLUSIVE.
Confidence tied to gates: CONFIRMED = gate fired with a cited value; LIKELY = consistent but gate
uncheckable; INCONCLUSIVE = can't distinguish / source blocked.

### 3. Make the existing engines reachable (hardening — "exists → works")
- **`diagnose`:** schema declares `symptom` as an **enum** (`throttle|refresh|slowness`) so the model
  can't pass free text; description teaches the mapping (slow reports→slowness, failed/late refresh→refresh,
  rejected/delayed ops→throttle). Unknown input returns the accepted values instead of a bare error.
- **`whats_changed`/recurrence:** near-term — interactive runs keep appending to the app-local history
  (works within a deployment); document the reset-on-redeploy limit honestly in the tool result. Durable
  history rides the scheduled-Job serverless rework (existing deferred item), and FUAM (B3) is the true
  long-history source — explicitly deferred to its own gated task.
- Audit each investigation tool's description so the model knows WHEN each fits the funnel steps
  (descriptions name the funnel stage they serve).

### 4. Data paths — BOTH, tiered; direct access is the freedom layer (user decision)
The escalation policy the harness follows (cheapest-sufficient first; each tier only when the lead
demands):
- **Tier 1 — MCP tools** (fast, firewalled, evidence-shaped): audit, patterns, spike, timeline, diagnose,
  whats_changed. The gates live here.
- **Tier 2 — freeform-but-firewalled KQL** (`run_kql` + `query_library`): leads the canned tools don't
  cover — e.g. the `XmlaRequestId → capacityThrottlingMs` join, `EventText` DAX-pattern inspection,
  day/week binned recurrence KQL. **New: promote verified investigation queries into `query_library`**
  (throttle-join, recurrence-binning, top-user-per-item templates) so Tier 2 is trained, not improvised.
- **Tier 3 — direct Fabric/Power BI REST** (`fabric_direct.py`, extend): read-only GET allowlist grows —
  dataset refresh **schedule** (contention analysis), datasets/dataflows in workspace, capacity state,
  (gated) admin activity events for tenant-wide "who" corroboration. Model chooses tools-vs-direct per
  task; direct is how it "works in Fabric/Power BI itself."
- **Tier 4 — FUAM + VertiPaq** (gated/deferred): FUAM lakehouse = long history + item→owner
  (`configuredBy`) — existing B3/3-C gate; VertiPaq `.vpax` = human-in-the-loop (agent analyzes a provided
  file; generating one live is a Phase-7+ notebook-runner question).
Schema names from the practitioner write-up (`ItemName` vs `ArtifactName`, `intendedUsage`,
capacity-events threshold fields, SP-blocked Metrics app) are **verified against Microsoft docs during
implementation before any gate hard-codes them**.

### 5. Remediation engine (literal fixes)
Extend the existing KB (`kb/*.py` playbooks, ~per-type rootCause/fixes/owner) so every confirmed root
cause maps to a **named** fix (the column/measure/schedule/SKU — not "consider reviewing your model"),
including the research-verified fixes (stagger refreshes out of peak, incremental refresh, aggregations,
avoid bidirectional filters, high-cardinality/auto-date-time trims, DirectQuery guidance) with source
links. The conclusion step always pairs root cause → its KB fix → who should act (author vs capacity
admin vs Power BI admin).

## Invariants (unchanged, enforced by construction)
Read-only ABSOLUTE (investigate + recommend; never execute; direct access is GET-only allowlist).
Never label proxy as authoritative (CPU-proxy labels stay; true-CU gate permanently blocked). Grounding
bar stays (every claim cites a tool-result field; gates can't be talked around). No secrets/real IDs in
the public repo.

## Testing / "actually works" verification (user requirement)
- **Unit:** gates (each claim class blocked without its signal; null-data → INCONCLUSIVE); diagnose enum
  (free text impossible, mapping documented); new query_library templates pass the firewall; extended
  fabric_direct endpoints GET-only + inert-unless-configured.
- **Harness evals:** extend `eval-agent` golden cases with investigation scenarios — a reactive case whose
  gate FAILS (must say "ruled out", not keep digging), a differential case (item expensive for everyone vs
  one user — must not single-signal-blame), a null-data case (must be INCONCLUSIVE not HEALTHY), a
  recurrence case, and a full funnel reaching a named fix.
- **Live verify (with user):** re-ask the deployed agent the real questions ("what spiked yesterday?",
  "has this happened before?", "who's driving it?", "optimize or size up?") and check the trail shows
  gates/differentials and the conclusion carries a literal fix.

## Explicitly NOT pursued (v1)
Any write/mutating action (absolute). True-CU-per-user automation (SP-blocked — permanent human step).
FUAM ingestion (its own gated task, B3). Live `.vpax` generation (Phase-7+ notebook runner). ML anomaly
detection (roadmap non-goal). Rebuilding the funnel mechanics that already work (patterns/spike/timeline/
verdict/concentration/KB — reuse, orchestrate, harden).

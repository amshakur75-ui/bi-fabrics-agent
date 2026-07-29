# Implementation Plan: bi-fabrics-audit-agent — Path to Completion

## Overview

Take the agent from "core gaps documented, formulas validated" to a fully hardened, self-improving,
autonomous capacity audit agent with a secondary NL-query/chart-generation skill — then, only once
all of that is stable, add Entra Agent ID as a governance layer on top. Ten phases, each an
independent sub-project with its own spec/plan/checkpoint. Do not start Phase N+1 implementation
until Phase N's checkpoint passes.

**Source of truth for what's broken:** `GAPS-AND-ISSUES.md` (52 tracked items, all cross-referenced
below). **Source of truth for what's still unvalidated but non-blocking:**
`UNVALIDATED-FORMULAS-AND-MEASURES.md`. Do not re-investigate anything in that second file as part
of this plan — it's explicitly parked.

## Architecture Decisions

- **C2 (REOPENED) is fixed first, before anything else.** Every SP1–SP7 prompt fix already written
  in the canonical file has zero effect on production until `agent_server/agent.py` imports from it
  instead of running its own diverged copy. Fixing this is what makes Phase 1's other fixes actually
  matter.
- **"Self-improvement" means growing context/memory, not self-modifying code or prompt.** The agent
  never edits its own logic autonomously. **Simplified 2026-07-29 (over-engineering correction):**
  Phase 6 is just querying the last N rows from Phase 5's `audit_findings` table for the relevant
  capacity/user/item and handing them to the agent as context before an investigation — no new
  table, no promotion/confidence/decay machinery. See "Over-Engineering Check" below for why the
  more elaborate version was cut.
- **The 5-minute autonomous cycle is a cheap deterministic gate check, not a full LLM investigation,
  every tick.** Full agentic investigation only fires when a gate in `gates.py` actually trips. This
  matches the system prompt's existing "auto in autonomous/alerting mode" language and keeps cost
  sane at 288 ticks/day.
- **True CU vs. proxy CU is a hard architectural boundary, not a detail.** Capacity-level CU
  (Layer A) is fully validated and true — safe for charts and NL answers with no special caveat.
  Per-user/per-operation attribution (Layer B, `CpuTimeMs`) remains a proxy until N13's
  `XmlaRequestId`↔`capacityThrottlingMs` join is independently verified — and true per-user *billed*
  CU stays permanently inaccessible by design (`gates.py`'s permanent block). This boundary must be
  enforced in every new surface added in Phases 7–8, not just the existing text responses.
- **NL-to-DAX targets the org's real business semantic models, never the Capacity Metrics app.**
  That model is confirmed protected (Section 6/D1) — building toward querying it via NL would be
  building toward something that structurally cannot work.
- **The MCP-serving package (`fabric_audit_agent`) is tools-only. The app is the real agent.**
  REVISED 2026-07-29: standard MCP architecture puts orchestration (system prompt, tool-calling
  loop, decision logic) in the client/host, not the server. This project had it backwards — the
  package owned `agent/system_prompt.py` and `agent/loop.py`, and the app grew its own stale,
  diverged copy of both (the entire root cause of C2/N15). The fix inverts this: the app
  (`agent_server/agent.py` or a clean split of files within the app) becomes the canonical, rich
  home of the prompt and loop — and eventually the Phase 9 autonomous-polling driver too. The
  package keeps tools, collectors, detectors, gates, and the Phase 2 grounding schema; it owns no
  prompt and no loop. Migration direction matters mechanically: the package's `system_prompt.py`
  currently holds the up-to-date SP1–7 fixes, the app's copy is stale — so the move is "package's
  good content into the app, then delete the package's `agent/` subfolder," not the reverse.
- **All memory tables are Delta. No Lakebase.** SUPERSEDED 2026-07-29 — see "Over-Engineering
  Check" below. An earlier pass recommended splitting two tables onto Lakebase for low-latency
  point-lookups; on reflection this was disproportionate to this project's actual traffic volume.
- **Entra Agent ID is deliberately last.** It's a governance layer on top of a working agent, not a
  prerequisite for the agent to function. Fabric's own current docs don't yet show confirmed native
  support for it, so pursuing it earlier would risk blocking real progress on an unconfirmed
  integration.

## Phase List

### Phase 1 — Fix confirmed code gaps + land the system prompt fixes (Foundation)
Everything in GAPS-AND-ISSUES.md Sections 1–3 with a proven fix and no open research dependency.
Detailed task breakdown: see `tasks/todo.md` Phase 1 — this is the only phase broken down to full
task-level detail right now, since it's ready to execute immediately with zero new research needed.

### Phase 2 — Build the grounding schema (B3, N14, N20)
Write `kb/metric_definitions.py` with every verified formula, source, metric type, and smoothing
window from Section 12. Add the `MetricValue` runtime dataclass (N14) so every number the agent
emits carries its provenance structurally, not by convention. Document the three throttle
smoothing windows (N20) and the `DOMINANT_ITEM_SHARE_PCT` verdict logic (N9) here. This is the
single "everything the agent refers to" reference the rest of the project builds on.

### Phase 3 — Hallucination guardrails + eval suite
Close N11 (ad-hoc KQL bypassing gates) and N12 (query provenance) so every path through the agent
is gated, not just the structured collectors. Write EV1 (6 cases) and EV3 (4 cases), run EV2
(`mine_evals.py` against real logs). Run the Section 14 stress-test bank end to end at least once.
This phase is the actual "0 errors" mechanism — gates + evals, not hope.

### Phase 4 — General hardening pass
B2 (blank ExecutingUser fallback), N4 (verify the 3 `# VERIFY AT DEPLOY` integration points), a
pass through Section 8 (UX1–4, decide yes/no explicitly rather than silently dropping), D3/D4
housekeeping, N2/E3/E4, plus lightweight CI (a single automated test run on every change — not a
heavy multi-stage pipeline) and a short ADR for the Task 1/2 architecture pivot. Lower individual
risk, but this is the "make sure everything works perfectly" pass — skipping it quietly is how
small things resurface later as production surprises.

### Phase 5 — Databricks memory (already designed in Section 7)
Build the four Unity Catalog Delta tables (`run_history`, `capacity_reporting`, `audit_findings`,
`concentration_alerts`) exactly as specified: no partitioning, liquid clustering, 90-day
time-travel retention via `ALTER TABLE`. **All four stay on Delta** (see Over-Engineering Check —
the earlier Lakebase split for two of them didn't hold up). This is also the storage layer Phase 6
reads from directly.

### Phase 6 — Growing context / "self-improvement" (memory gets richer over time)
**Simplified 2026-07-29.** Before starting an investigation, query the last N rows from Phase 5's
`audit_findings` table for the relevant capacity/user/item and hand them to the agent as labeled
context (e.g. "3 prior findings for this capacity in the last 30 days: ..."). No new table, no
promotion threshold, no confidence scoring, no decay logic — the existing table and a simple
recency-ordered query are enough to deliver "context grows over time." If raw recent-findings
context turns out too noisy in practice once this is running for real, a more structured
promoted-pattern layer can be built then, with real evidence it's needed — not before.

### Phase 7 — NL-to-query skill (KQL / SQL / DAX against business semantic models)
A secondary skill, explicitly bounded away from the agent's main monitoring scope. Three query
targets: KQL (already partially exists via `run_kql`, needs gating per Phase 3), SQL (new), DAX
against the org's actual business semantic models (new, and explicitly NOT the Capacity Metrics
app). Needs its own safety design: read-only enforcement, a row/complexity ceiling, validation
before execution — same discipline as `kql_guard.escape_entity`, extended to the new query types.
Needs an explicit security-review checkpoint before shipping, given it's the project's largest new
attack surface. Worth a quick look at Databricks Genie's Managed MCP Server as an alternative to
building this from scratch, if the target semantic models can be reached through Unity Catalog.
**Needs its own brainstorming pass** before task breakdown — this is a full subsystem, not a
same-day extension of Phase 1's fixes.

### Phase 8 — Chart/graph generation
Depends on Phase 7 (needs a query result to chart) and must enforce the true-CU/proxy boundary
from the Architecture Decisions above — a chart is a new place to accidentally blend populations
the text responses already know not to blend. **Needs its own brainstorming pass**, likely
touching `design-system`/`ui-ux-pro-max`/`visualize` tooling depending on where charts render
(inline chat vs. a generated artifact).

### Phase 9 — Autonomous 5-minute polling
**First task: locate and audit the existing Claude-Code-built implementation** — do not assume
this needs building from scratch; confirm what exists, whether it correctly implements
cheap-gate-check-then-escalate (per the Architecture Decision above), and what's actually missing
before writing anything new. Runs the specific, prioritized gate checks documented in
`tasks/todo.md`'s Phase 9 (30% concentration and >100% throttling as primary triggers). Add job
self-observability and basic cost/latency/error-rate tracking (a stopped or silently-degrading job
is its own risk) alongside whatever gaps the audit finds.

### Phase 10 — Entra Agent ID
Deliberately last. Prerequisite: an admin-provisioned agent identity + blueprint (external to this
codebase). On this end: stand up the sidecar, swap the agent's token-acquisition code to call it,
re-grant the new identity the same Fabric access the current SP has. See the Entra Agent ID
discussion earlier in this project's history for the full breakdown.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| C2 not actually fixed (repeat of the original "marked FIXED, wasn't" failure) | High — silently negates all of Phase 1's prompt work again | Add a regression check: confirm `agent_server/agent.py` has no local `_SYSTEM`/`_run_tool_loop` definitions after the fix, not just that it "should" import correctly |
| Phase 6/7/8 scope creep into the main monitoring path | Medium — these are explicitly secondary skills | Keep them behind clear tool/skill boundaries; the system prompt's core investigation funnel should never depend on NL-query or chart tools being available |
| True-CU/proxy boundary erodes under chart or NL-query pressure ("just show me total CU by user") | High — this is the project's most safety-relevant finding | Any new surface that could display a per-user or per-operation CU figure inherits the existing proxy-caveat rule by construction, not by remembering to add it each time |
| 5-min autonomous job silently stops | Medium — defeats the point of autonomy | Job self-observability is explicit in Phase 9, not an afterthought |
| Entra Agent ID pursued before Fabric confirms support | Wasted effort | Sequenced last; re-check Fabric's own docs before starting Phase 10 |
| Over-planning delays actual implementation | Medium — diminishing returns on more planning rounds | See Over-Engineering Check below; this plan is now considered stress-tested enough to start Phase 1 |

## Open Questions

- Phase 7: which specific business semantic models are in scope for NL-to-DAX — all of them the
  org has, or a curated subset to start? Also worth resolving: does Genie's Managed MCP Server
  make sense as the implementation instead of custom SQL/DAX generation?
- Phase 9: what does the existing Claude-Code-built autonomous polling code actually do today —
  unknown until audited.

## Over-Engineering Check (2026-07-29) — two things cut after re-examining actual scale

**1. The Lakebase split (previously recommended, now reverted).** Databricks' own justification for
Lakebase is thousands of low-latency point-lookups *per second* from a production app. This agent's
actual volume — one autonomous check every 5 minutes, plus modest interactive traffic from an
internal tool — comes nowhere near that threshold. An LLM call in this agent already takes several
seconds; a Delta point-lookup on a small, liquid-clustered table adding a fraction of a second
would not be perceptible against that. Splitting storage across two systems, adding a CDC sync
pipeline, and creating a new retention-policy problem was complexity that wasn't earning its keep
at this scale. **Reverted: all memory tables stay on Delta.**

**2. A separate `learned_patterns` table with promotion/confidence/decay logic (previously
designed for Phase 6, now cut for v1).** What was actually asked for — "context should grow over
time" — is fully satisfied by querying recent rows from the `audit_findings` table Phase 5 already
builds. A whole second table with a promotion threshold, a confidence-scoring formula, and a decay
rule is real design and implementation weight for a benefit that hasn't been shown necessary yet.
**Simplified: Phase 6 is a query against existing data, not a new subsystem.** If raw
recent-findings context proves too noisy in practice once this is actually running, the more
structured version can be built then, against real evidence — not speculatively now.

**What this changes:** Phase 5 no longer has an open retention-mechanism question (everything's
Delta, the existing 90-day `ALTER TABLE` pattern applies uniformly). Phase 6 no longer needs its
own brainstorming pass before task breakdown — "query recent `audit_findings`, inject as context"
is simple enough to go straight to a task list once Phase 5 exists. The Stress-Test Round 3 findings
about decay/demotion and pre-fix-data contamination are now moot for the promotion logic (it doesn't
exist), though the pre-fix-data concern still applies in simplified form: don't start feeding
`audit_findings` back as context until Phase 1–4 are confirmed stable, so early noisy findings
don't get surfaced as if they were trustworthy history.

**This plan has now been stress-tested four times** (initial scenario walkthrough, memory-layer
architecture review, operational-gaps pass, and this over-engineering check). Diminishing returns
are setting in — this is a reasonable point to move into Phase 1 implementation rather than
continue adding planning rounds.

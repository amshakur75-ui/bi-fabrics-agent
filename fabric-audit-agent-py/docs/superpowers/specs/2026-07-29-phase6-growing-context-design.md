# Design: Phase 6 — Growing Context / "Self-Improvement"

**Date:** 2026-07-29 (original), simplified 2026-07-29 (over-engineering correction)
**Depends on:** Phase 5 (Databricks memory tables, all Delta)
**Scoped per project owner's clarification:** this is NOT self-modifying code or prompt. The
agent never edits its own logic. This is a knowledge layer that accumulates over time so the
agent understands recurring tenant-specific patterns more easily on each subsequent
investigation — "more of a conscious than a regular app," in the owner's words, achieved by
feeding it richer context, not by changing what it is.

## Revision note

The original version of this spec designed a full `learned_patterns` table with a promotion
threshold, confidence scoring, and decay logic — and, in an earlier draft, put that table on
Lakebase for low-latency lookups. A later stress-test pass cut both: the traffic volume this
agent actually has (roughly one autonomous check every 5 minutes, plus modest interactive use)
doesn't come close to justifying either the storage split or the added table/scoring machinery.
What follows is the simplified design that actually ships for v1.

## Purpose

Every investigation this agent runs today starts cold — it has no memory of the fact that
`sumentu.giri`'s SCM/HR refreshes happen every night, that `EventStream`/`Activator` are known
system noise, or that a given capacity throttled three times this month under the same shape of
load. Phase 6 gives it that memory, so recurring patterns get recognized instead of re-diagnosed
from scratch every single time.

## The Design (Simplified)

**No new table. No promotion logic. No confidence scoring. No decay logic.**

Before starting an investigation on a given capacity/item/user, query the last N rows from
Phase 5's `audit_findings` table for that same scope, ordered by recency. Hand them to the agent
as labeled context:

> *"3 prior findings for this capacity in the last 30 days: [date] — throttling confirmed,
> Interactive Delay 126%. [date] — same pattern, cadence flagged for `sumentu.giri`'s nightly
> SCM/HR refresh. [date] — concentration alert, `Ent-Reporting-Sales`, 34% share."*

That's the entire mechanism. The agent still investigates fresh each time — this is context, not
a shortcut that skips verification. It just starts with real prior history instead of zero
information.

## Why This Is Enough for v1

What was actually asked for — *"context should grow over time"* — is fully satisfied by this. The
`audit_findings` table already accumulates a permanent record of every investigation (that's its
whole purpose in Phase 5). Querying it by recency for the current scope is a simple, cheap
operation with no new infrastructure. A promoted-pattern layer with confidence scoring would only
earn its complexity if raw recent-findings context turns out too noisy in practice — e.g., if the
agent starts citing irrelevant or contradictory history, or if the volume of findings per
capacity grows large enough that recency alone stops being a good filter. Build that version then,
against real evidence, not now.

## Data Flow

```
Investigation starts on capacity/item/user X
   → query: SELECT * FROM audit_findings WHERE scope matches X ORDER BY timestamp DESC LIMIT N
   → inject results as labeled context (dated, plain language)
   → proceed with normal investigation (SP-rule-governed, still evidence-gated)
   → investigation completes → write new finding to audit_findings (Phase 5, unchanged)
```

## Error Handling

A missing or empty result from `audit_findings` must never block an investigation — this is
enrichment, not a dependency. If the query returns nothing (no prior history) or fails, the agent
proceeds exactly as it does today.

## Guardrail

Prior findings are context, never a conclusion. The agent must still gather fresh evidence for
the current investigation — citing "this happened before" is not a substitute for confirming the
current instance independently. This should be an explicit system prompt rule once this phase is
implemented (a new SP item).

## Prerequisite

Don't start feeding `audit_findings` back as context until Phase 1–4 are confirmed live and
stable. If early findings were generated while the agent still had bugs (the OB1–OB7 class of
mistakes), surfacing them as trustworthy prior history would compound the problem rather than help.

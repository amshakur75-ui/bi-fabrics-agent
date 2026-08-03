# Design: Phase 7 — Natural-Language-to-Query Skill (KQL / SQL / DAX)

**Date:** 2026-07-29
**Scoped per project owner's clarification:** a secondary skill alongside the agent's main
monitoring scope — NOT a replacement for the structured investigation pipeline. DAX targets the
org's real business semantic models only, **never** the Capacity Metrics app (confirmed protected,
Section 6/D1 — building toward querying it via NL would be building toward something that
structurally cannot work).

## Purpose

Let a user ask a plain-language question about anything in the tenant's actual data — a report's
numbers, a dataset's structure, a table in a lakehouse — and get an answer, without needing to
know KQL, SQL, or DAX themselves. This sits alongside, not inside, the capacity/throttling
investigation funnel.

## Scope Decision: Which Targets, and How Access Grows

Per the owner: *"it will have access to everything at the end."* The correct design consequence of
that is **not** a hardcoded list of approved models/lakehouses — it's making the query layer
generic, so that whatever the agent identity/SP already has read access to via ordinary Fabric
permissions is exactly what's queryable. Access expands the normal way (granting the identity
Viewer/Contributor on more workspaces), not by changing code. Rollout can still be staged in
practice (test against 1–2 known models first) — that's an operational choice, not a code
constraint.

**Three query surfaces:**
- **KQL** — already partially exists (`run_kql`). Needs to be routed through Phase 3's gating
  (N11) rather than continuing to bypass it.
- **SQL** — new. Targets Fabric Lakehouse/Warehouse SQL endpoints. Fabric doesn't support SQL
  authentication directly (confirmed via current Fabric docs) — the same Entra ID service
  principal already used elsewhere authenticates here too, no new credential type needed.
- **DAX** — new. Targets the org's actual business semantic models (real Power BI reports/datasets
  this org owns), reached the same way any of this project's DAX Studio sessions worked earlier —
  standard Fabric/Power BI service principal access to a model the org actually controls, which is
  a completely different access story from the locked-down Capacity Metrics app.

## Three Approaches Considered for the Generation Pipeline

**A — Direct generate-and-execute.** LLM generates the query from the NL question, executes it
immediately. Fastest to build, but the highest-risk option — an ungoverned query straight to
execution.

**B — Generate → validate → execute, single pass.** LLM generates a candidate query; a
deterministic validation layer checks it (read-only statement only, no destructive keywords, a
row/complexity ceiling) before execution; reject and re-prompt on failure.

**C — Generate → validate → dry-run/cost-estimate → execute.** Same as B, plus an explicit
dry-run or cost-estimate step before running the real query (where the underlying engine supports
it), surfaced to the user for very expensive-looking queries before committing to them.

**Recommendation: B, with C's dry-run behavior added later if real usage shows it's needed.** B
matches the discipline already established elsewhere in this codebase (`kql_guard.escape_entity`)
— validate before execute, don't trust generated input blindly — without over-building a
cost-estimation pipeline before there's evidence it's needed.

## Architecture

**Validation layer (extend the existing `kql_guard` pattern to SQL and DAX):**
- Read-only enforcement: reject anything that isn't a `SELECT`/`EVALUATE`-shaped query. No
  `INSERT`/`UPDATE`/`DELETE`/`DROP`/DDL of any kind, ever — this agent is read-only by design at
  every layer, not just its stated intent.
- Row/complexity ceiling: cap result size (mirrors the Execute Queries REST API's own real limits
  — up to 100k rows / 1M values / 15MB, worth reusing as the practical ceiling rather than
  inventing a new one).
- Timeout enforcement on every query, consistent with the existing tool-loop's request timeout
  pattern (`agent_server/agent.py`'s `_post()` already does this for the model call itself —
  extend the same discipline to query execution).
- Entity escaping: extend `kql_guard.escape_entity`'s approach to SQL identifiers and DAX table/
  column references, so a user-influenced NL question can't smuggle in a malformed or malicious
  identifier.

**Generation flow:**
```
NL question
   → classify target (KQL / SQL / DAX) based on what the question is actually asking about
   → generate candidate query
   → validate (read-only check, row/complexity ceiling, entity escaping)
       → fail: re-prompt once with the validation error as feedback, then abstain if it fails again
       → pass: execute
   → return result + the exact query verbatim (ties directly to existing SP7 — never paraphrase)
```

**Metadata grounding:** before generating a DAX query against an unfamiliar semantic model, the
agent should first read that model's actual schema (table/column/measure names) rather than
guessing field names from the question alone — this avoids a whole class of "confidently wrong
column name" failures. Same principle for SQL against an unfamiliar lakehouse table.

## Error Handling

- A validation failure is not a silent retry-forever loop — one re-prompt attempt, then abstain
  and say what's missing (consistent with the existing ABSTAIN rule already in the system prompt).
- A query that times out or exceeds the row ceiling should say so plainly, not silently truncate
  and present partial results as complete (this directly echoes the existing "ZERO ROWS = REPORT
  ZERO, NEVER FABRICATE" discipline — the same honesty standard applies to "TOO MANY ROWS = SAY SO,
  NEVER SILENTLY TRUNCATE").

## Guardrail Tie-In

This is the one place in the whole project where a genuinely new attack surface opens (an LLM
turning arbitrary user text into an executable query). The validation layer above is not optional
polish — it's the actual safety mechanism, and it needs its own dedicated test coverage before this
phase is considered done, not just folded into the general eval suite.

## Open Item for Implementation

Exact classification logic for "which target does this question need" (KQL vs. SQL vs. DAX) needs
a first pass at implementation time — likely a simple keyword/intent classifier at first (similar
in spirit to `agent_server/agent.py`'s existing `_step_budget()` classifier, though N22 already
flags that pattern as fragile — worth designing this one more robustly from the start rather than
repeating that mistake).

# PASTE THIS PROMPT INTO CLAUDE CODE — full autonomous execution

You are executing the master integration plan for the bi-fabrics-audit-agent, start to
finish, autonomously. Work from `C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent\`.

## Your three governing documents — read ALL THREE in full before touching any code
1. `fabric-audit-agent-app\tasks\master-integration-plan.md` — THE build order. Phases 0–7.
2. `fabric-audit-agent-app\tasks\tightening.md` — Parts 0–26, the evidence base and the
   STANDING RULE. The plan references Parts by ID; when it does, go read that Part.
3. `GAPS-AND-ISSUES.md` (locate it: try `fabric-audit-agent-py\GAPS-AND-ISSUES.md`, else
   search the repo root) — the ~43-item ledger you must reconcile in Phase 0.1 and update
   in Phase 7.3.

## Autonomy contract — these rules are absolute
- Do NOT ask the user anything. For every decision point, make the best logical decision
  yourself based on how the system actually works — resolve it by reading the relevant code,
  its callers, and its tests, then choose the option that (a) preserves every existing
  caller's contract, (b) matches the plan's stated intent, and (c) is reversible. Record the
  decision and its reasoning in a running `tasks/EXECUTION-LOG.md` as you go. If two options
  are genuinely equal, pick the one with the smaller blast radius.
- Do NOT stop until Phase 7.4 is complete. No pausing to report and wait. No "shall I
  continue?". You report by APPENDING to EXECUTION-LOG.md and continuing.
- Work the phases strictly in order 0 → 7. Within a phase, work items in numeric order
  unless a dependency forces otherwise (record any reorder + why in the log).

## The loop you run for EVERY numbered item, no exceptions
1. RE-READ the item in master-integration-plan.md and any tightening.md Part it cites.
2. BEFORE-REVIEW (STANDING RULE): read the target file in full; grep every caller, importer,
   and consumer of what you're changing (function name, import path, dict keys, return
   shape); read the NEIGHBORING functions in the same file (the ones above and below what
   you touch — they share assumptions); identify every other place in the codebase that
   implements similar logic and ask "does this same bug/pattern exist there?"; list every
   test touching the function AND its callers. Write this blast-radius list into
   EXECUTION-LOG.md before editing.
3. IMPLEMENT the change. New modules get docstrings stating their contract and their
   source (which plugin file / which plan item).
4. TEST: write/extend tests for the change (including the ported invariant tests where the
   plan names them), then run the FULL suite — not just the touched file's tests.
5. AFTER-REVIEW: manually re-read every caller from step 2 and confirm it still receives
   the shape/behavior it expects — do not trust green tests alone. If the change fixed a
   pattern-class bug, grep the whole codebase for the same pattern and fix or log every
   other instance found.
6. LOG: append to EXECUTION-LOG.md — item id, what changed, callers reviewed by name,
   sibling patterns checked and result, test counts before/after, decisions made and why.
7. CHECK the item off in master-integration-plan.md, then loop to the next item.

## Phase gates — self-verification loop
At the END of each phase, before starting the next: re-read the entire phase's items and
verify each is genuinely done (not just marked); run the full test suite and compare to the
Phase-0 baseline; re-read the "COMPLETENESS CROSS-CHECK" section of the plan and confirm
nothing that phase owns was dropped. If anything fails this gate, LOOP BACK and fix it
before proceeding. This loop-back is mandatory — never carry a known failure forward.

## Hard constraints you must never violate
- Everything stays read-only toward Fabric/Power BI/Azure. No write scopes, no mutations.
- `agent_server/agent.py` (async) and `agent_server/loop.py` (sync) are twins — any loop
  change lands in BOTH, structurally identically (dedup, budget nudge, wrap_untrusted,
  and the three new hooks).
- The system prompt is single-sourced in `agent_server/system_prompt.py` (ADR-001). Never
  create a second copy anywhere.
- `query/kql_guard.py`'s existing function signatures do not change — new audit rules go
  in the new `query/kql_audit_rules.py`.
- Do NOT rerun the plugin's .cjs build scripts — their inputs aren't available; the
  pre-built JSON/catalog outputs in the extracted data are authoritative.
- The per-user proxy caveat (`is_proxy`) belongs ONLY on Log Analytics CpuTimeMs-derived
  data — never on capacity event stream data.
- N1: do not "fix" the WM eventDepth withholding (it is deliberate).
- Do not attempt Graph API or M365 MCP for HR data (confirmed dead: AADSTS65002 / HTTP 406).
- No user-local-disk writes for exports — server-side artifacts + download links only.
- Refuse nothing in the plan and add nothing beyond it except: bugs you discover during
  before/after review get fixed if small (log them) or logged as new GAPS entries if large.

## Environment notes
- Windows filesystem; use the tools that operate on the user's real filesystem. Full test
  runs: use the repo's existing test invocation (find it in CI config / pyproject / package
  scripts — do not guess a command that isn't configured).
- If a live-verification step (Phase 7.2) needs a deployed app you cannot reach, execute
  everything you CAN (unit/integration/local), and write the exact remaining live steps as
  a checklist at the end of EXECUTION-LOG.md — that is the ONLY permitted deferral in the
  entire plan, and it must be explicit, not silent.

## Definition of done
Phases 0–7 all checked off in master-integration-plan.md; full suite green vs baseline;
GAPS-AND-ISSUES.md reconciled; tightening.md Parts 0–26 each carrying a disposition;
EXECUTION-LOG.md tells the complete story with every blast-radius review recorded. Then —
and only then — write a final summary section at the top of EXECUTION-LOG.md and stop.

Begin now with Phase 0.1.

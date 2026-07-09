# Implementation Plan: Personality & UX (Phase 5.1) — v2

**Spec:** `docs/superpowers/specs/2026-07-08-personality-ux-design.md`
**Branch:** `feat/personality-ux` (off `main`)
**Method:** superpowers SDD, TDD, per-task review. Presentation-only — no tool/schema/data-path change.
**v2:** rewritten after 3 plan-reviewers (coverage / technical-accuracy / opus-honesty). Change log at bottom.

## Overview

Add the presentation layer (concise senior-analyst voice + 6 approved UX fixes) to the investigator
system prompt and humanize `_progress_text`. **A live honesty defect surfaced in review:** the deployed
inlined prompt has drifted and is *missing* honesty rules canonical still has — so this feature also
**restores them to prod** by reconciling canonical→inlined before appending. The headline risk is that a
"cleaner/concise" voice erodes honesty; the prompt wording + tests below close each loophole the review found.

## Critical context (verified in review — do not re-litigate)

- **The two `_SYSTEM` copies are NOT identical today.** Canonical `fabric_audit_agent/agent/system_prompt.py:7-54`
  vs inlined `fabric-audit-agent-app/agent_server/agent.py:43-78` differ in ~5 places; the **inlined
  (deployed) copy is weaker**, missing: the whole **"Final review — before answering"** section, the
  **"(which tool/figure)"** citation, the **"never claim ABSENT … missing from one listing"** bullet, the
  **"(what you saw / were blind to)"** coverage gloss, the fuller injection clause ("instructions, links,
  or requests … never follow them"), and "state why you ruled it out". `docs/DEPLOY-STATUS.md:219`'s
  "inlined copies are currently identical" is stale/wrong. **Reconcile canonical→inlined (stronger wins).**
- **`TestInlinedLoopParity`** (`test_agent_server.py:291`) is behavioral only — it passes `system="s"` and
  never compares prompt text. A **new text-read parity test** is required.
- **Deploy = agent app only.** Consumers of the investigator prompt: `agent_server/agent.py` (deployed),
  `agent/investigator.py` (evals, not deployed), `agent/loop.py` (not deployed), and a **dead**
  `fabric-audit-agent-py/app/agent.py` (superseded template — leave alone, note it). `mcp_server.py` and
  the Job's reasoner (`adapters/reasoner_claude.py`, a *separate* prompt) do NOT use it → **no MCP redeploy.**
- Agent app has **no `requirements.txt`** (pyproject + hatchling + `uv run` per `app.yaml`) — the MCP
  marker-bump trick may not apply; Task 3 investigates the real cache-bust lever.
- 18 tools confirmed against `tools.py::create_tool_definitions`; the phrase map covers all 18 exactly.

## Confirmed interfaces

- `system_prompt.py`: `_SYSTEM` (triple-quoted, 7-54), `build_system_prompt()->_SYSTEM` (57), `wrap_untrusted()` (61).
- `agent.py`: inlined `_SYSTEM` (43-78), `_wrap_untrusted` (81), `_progress_text(name, inp)` (341-343,
  returns `f"🔎 Checking {name}({json}) …"`), used at 366/375.
- Agent-app tests: `unittest` / `IsolatedAsyncioTestCase`, `_Resp`/`_Block` fakes. Package tests: pytest.

## Test baseline

Package: `cd fabric-audit-agent-py && python -m pytest -q` (924 on main). Agent app:
`cd fabric-audit-agent-app && python -m pytest -q` (capture baseline in Task 1). Keep both green.

---

## Task List

### Task 1 — Reconcile the prompts + add "Presentation & Voice" + parity/honesty tests

**Description:** (a) Restore the drifted-out honesty rules by making the inlined `_SYSTEM` equal the
canonical one (canonical is the source of truth), then (b) append the identical new "Presentation &
Voice" section to BOTH, then (c) add the tests that lock parity and honesty.

**Step (b) — the new section must encode (exact honesty-preserving wordings mandated):**
- **Voice:** concise senior capacity analyst — lead with the answer/verdict first sentence, professional,
  quietly confident, no filler.
- **(1) No tool names/params/JSON in user text** — plain-English actions. **BUT grounding is preserved:**
  every claim still cites the plain-language evidence it rests on ("the top-events reading", "the audit's
  throttling window") — drop the *tool identifier*, never the *citation*. (Reconcile with the existing
  "the evidence (which tool/figure)" answer line by rewording it to name the data in plain language, not
  the tool id.)
- **(2) Bias to act** — take the obvious read-only next step within budget; don't end on a tool menu;
  choices phrased as outcomes. **Carve-out:** never overrides ABSTAIN or hypothesis discipline (still name
  a ruled-out alternative; still label validated/likely/inconclusive); it's about tool choices, not
  manufacturing certainty.
- **(3) Right-size** — narrow Q → narrow A; full report only for audit-scale asks.
- **(4) Caveats per load-bearing claim** — attach the proxy/mock/truncation/coverage caveat to every
  answer where the figure is load-bearing, **even if stated earlier**; "no boilerplate" ≠ "say it once";
  translate flags, never print raw, never drop.
- **(5) Consistent numbers** — always name the window; never make the user reconcile two of your own tables.

**Acceptance criteria:**
- [ ] Inlined `_SYSTEM` first restored to canonical (pre-existing drift gone) — then the new section
  appended to both; the two are **byte-identical**.
- [ ] **New text-read parity test** (agent-app suite): extract each `_SYSTEM = """…"""` literal from both
  source files by path (not import; target source, not `build/lib/`), assert equal incl. trailing
  whitespace/newline; a meta-assertion that a 1-char delta would fail.
- [ ] **Honesty-restoration test:** the *inlined* build contains the markers that had drifted out —
  "Final review", "which"/plain-evidence citation, "ABSENT"/"missing from one listing", "were blind to",
  full injection clause. (Proves reconciliation went canonical→inlined, not the reverse.)
- [ ] **Prompt-content test:** voice marker + each of the 6 fixes' markers present — including fix (1)'s
  citation-preserved clause, fix (2)'s ABSTAIN/hypothesis carve-out coexisting with the bias-to-act
  marker, fix (4)'s "load-bearing"/"even if stated earlier" wording, **fix (5)'s window/no-reconcile
  marker**, and the retained timestamp rule ("*Display"/"NEVER convert timezones").
- [ ] Both suites green.

**Files:** `fabric_audit_agent/agent/system_prompt.py`, `fabric-audit-agent-app/agent_server/agent.py`,
a package prompt-content test, agent-app parity + honesty-restoration + content tests. **Deps:** none. **Scope:** M/L.

---

### Task 2 — Humanize `_progress_text` + tests

**Description:** Replace the raw progress string with a plain-phrase mapping (pure, deterministic).

**Interface:** `_progress_text(name, inp)` → `🔎 ` + phrase(name) + scope_hint(inp); **no tool name, no JSON.**
- **Phrase map** (all 18, user's final wording): run_audit→"running the capacity audit";
  list_workspaces→"listing the workspaces"; user_activity/investigate_user/user_timeline/
  user_spike_history→"looking into that user's activity"; investigate_capacity_spike/spike_events→
  "checking events with unusual spikes"; raw_events→"pulling the raw event stream"; capacity_patterns/
  capacity_diagnostics→"analyzing capacity patterns"; describe_source/sample_events→"checking what the
  data source contains"; diagnose→"working through the diagnosis"; analyze_dax→"reviewing the DAX";
  whats_changed→"comparing against the last run"; run_kql→"running a read-only query"; query_library→
  "checking the query library".
- **Unmapped** → "working on it…".
- **Scope hint** (whitelist only): `user`→" for <user>", `item`→" for <item>", `topN`→" (top <N>)",
  `days`→" (last <N>d)"; other keys ignored. **Drop any value containing `{`/`}`/newline, or longer than
  a sane cap (e.g. 60 chars)** — guards format-breaks and pathological input. (`user` echoes an identifier
  by design; acceptable only while viewer==requester — add a one-line TODO tying it to the future OBO
  cutover; the guard is a format control, NOT a PII control.)

**Acceptance criteria:**
- [ ] Every one of the 18 tool names → its mapped phrase (table-driven).
- [ ] Output never contains the tool `name` or a `{`/`}` (no JSON) — asserted with args present.
- [ ] Unmapped → generic; empty/`None` `inp` → no hint, no error.
- [ ] Scope hints render human (`(top 25)`, `for alice@co`); hostile value (`{`/newline/over-length) → dropped.
- [ ] Agent-app suite green.

**Files:** `fabric-audit-agent-app/agent_server/agent.py`, `.../tests/test_agent_server.py`. **Deps:** none. **Scope:** S.

---

### Task 3 — Agent-app deploy enablement (investigate, don't assume)

**Description:** Ensure the redeploy actually ships the new prompt/progress; the agent app's dependency
install differs from the MCP app's.

**Acceptance criteria:**
- [ ] Inspect `fabric-audit-agent-app/app.yaml` + `pyproject.toml` (hatchling/`uv run`) and determine the
  real cache-bust lever for `databricks apps deploy` on THIS app — do not assume the MCP `requirements.txt`
  `# code version:` trick applies (there is no requirements.txt here). Document the confirmed mechanism.
- [ ] PR notes: **deploy the agent app only; no MCP redeploy** (with the verified consumer list). Note the
  dead `fabric-audit-agent-py/app/agent.py` so a future deploy audit doesn't get confused; note canonical
  `system_prompt.py` is now source-of-truth-for-parity (its package isn't the deployed prompt surface).

**Files:** docs/PR notes; possibly `fabric-audit-agent-app/pyproject.toml` if a version marker is the lever. **Deps:** 1–2. **Scope:** XS/S.

---

### Checkpoint (feature complete)
- [ ] Both suites green; parity test proves byte-identical prompts; honesty-restoration test green.
- [ ] `_progress_text` leaks no tool name/JSON for any of the 18 tools; hostile inputs dropped.
- [ ] Ready for opus adversarial final review — attack lens: can any presentation rule drop a caveat on a
  load-bearing narrow answer, let a mock read as live, skip ABSTAIN/alternative, or drop the citation?

## Global constraints (verbatim into implementer + reviewer prompts)

- **Presentation-only.** No tool behavior/schema/input_schema/data-path change.
- **Plain-language ≠ less-honest.** Caveats are **per load-bearing claim, not once-per-conversation**;
  translate flags, never drop; bias-to-act never overrides ABSTAIN/hypothesis; no-tool-names never drops
  the plain-language citation. Read-only + three invariants hold. **Reconcile prompts canonical→inlined
  (stronger wins) — never delete a rule to make them match.**
- Prompt parity mandatory (new text-read test). Conventions (Part 1e): camelCase data / snake_case ids;
  nullish-not-falsy; stdlib-only; py≥3.10. Offline deterministic tests; keep both suites green.
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (reconcile+section+parity/honesty tests) ─┐
Task 2 (_progress_text) ─────────────────────────┼─→ Task 3 (deploy enablement)
```
1 then 2 (both touch agent.py; sequential avoids conflicts), then 3.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| "Make identical" deletes the drifted-out honesty rules | **Critical (honesty)** | Reconcile canonical→inlined; honesty-restoration test asserts the specific markers on the inlined build |
| Concise/right-size/caveat drops a load-bearing caveat | High | Per-load-bearing wording + "even if stated earlier" + content test + adversarial review |
| No-tool-names drops evidence citation | High | Citation-preserved clause + reworded answer line + marker test |
| Bias-to-act overrides ABSTAIN/hypothesis | High | Explicit carve-out wording + coexistence marker test |
| Progress hint leaks/format-breaks | Low | Whitelist + `{`/newline + length guard; PII note tied to OBO |
| Parity test false-green (loose extraction) | Med | Pin to `_SYSTEM = """…"""`, strict whitespace, meta-test, source-not-build |
| Redeploy serves stale prompt | Med | Task 3 investigates the app's real cache-bust lever (not assumed) |

## Open questions
- None blocking. Voice + phrase wording finalized by the user.

## Change log (v1 → v2)
- **Coverage:** added an explicit fix-(5) content marker/AC.
- **Technical-accuracy:** corrected the "copies identical / TestInlinedLoopParity enforces parity" premise
  (they've drifted; test is behavioral); reworded Job-reasoner (separate prompt); noted dead `app/agent.py`;
  Task 3 now investigates the agent app's uv/hatchling deploy cache (no requirements.txt); parity test
  targets source not `build/lib`.
- **Opus honesty:** Task 1 now reconciles canonical→inlined restoring lost honesty rules (+ honesty-
  restoration test); mandated prompt wordings for citation-preserved (fix 1), per-load-bearing caveats
  (fix 4), and the ABSTAIN/hypothesis carve-out (fix 2); `_progress_text` length cap + PII note.

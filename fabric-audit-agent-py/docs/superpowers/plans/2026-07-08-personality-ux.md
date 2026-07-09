# Implementation Plan: Personality & UX (Phase 5.1)

**Spec:** `docs/superpowers/specs/2026-07-08-personality-ux-design.md`
**Branch:** `feat/personality-ux` (off `main` `8f48070`)
**Repo root:** `C:/Users/am08570/ClaudeCode-Workspace/bi-fabrics-agent` · package `fabric-audit-agent-py/`, agent app `fabric-audit-agent-app/`
**Method:** superpowers SDD, TDD, per-task review (HANDOFF Part 4). Presentation-only — no tool/schema/data-path change.

## Overview

Add the missing presentation layer: a "Presentation & Voice" section (concise senior-analyst voice + the
6 approved UX fixes) to both parity-locked system prompts, and a humanized `_progress_text`. The honesty
guardrail — *plain-language ≠ less-honest* — is enforced by tests and the adversarial review.

## Architecture decisions (grounded in the code)

- **Two prompt copies, must stay byte-identical.** Canonical `_SYSTEM` in
  `fabric_audit_agent/agent/system_prompt.py` (`build_system_prompt()` returns it; `wrap_untrusted()`
  also lives there); inlined copy in `fabric-audit-agent-app/agent_server/agent.py:43`. The new section
  is **appended** to both; all existing hard rules stay verbatim (additive only).
- **Parity is currently manual — add a real test.** `TestInlinedLoopParity`
  (`fabric-audit-agent-app/tests/test_agent_server.py:291`) is a *behavioral* smoke test of
  `_run_tool_loop`; it does NOT compare the prompt strings. The agent app does not import
  `fabric_audit_agent` (it inlines to stay self-contained), so a parity test must **read both source
  files as text** (same repo, relative path) and compare the extracted `_SYSTEM` literals — not import.
- **`_progress_text(name, inp)`** (`agent.py:341`) currently returns `f"🔎 Checking {name}({json}) …"`.
  Replace with a name→phrase map (the spec table, with the user's final wording) + a scope-hint
  whitelist; keep it pure/deterministic.
- **Deploy = agent app only.** The change runs in the agent app (its inlined prompt + `_progress_text`).
  The **MCP server does not consume the system prompt** (it serves tools; the prompt is the agent
  loop's), and neither does the scheduled Job's audit reasoner — so **no MCP redeploy is needed**. The
  canonical `system_prompt.py` edit is for source-of-truth + the parity test. (Correction to the spec's
  "deploy both apps" line.)

## Confirmed interfaces

- `system_prompt.py`: `_SYSTEM` (triple-quoted str), `build_system_prompt() -> _SYSTEM`,
  `wrap_untrusted(text)`. Static / prompt-cache-friendly.
- `agent.py`: inlined `_SYSTEM` at line 43 (currently identical text to canonical); `_progress_text` at
  341–343; used at 366/375 to build progress items.
- Agent-app tests: `fabric-audit-agent-app/tests/test_agent_server.py` (unittest style, `_Resp`/`_Block`
  fakes). Package tests: `fabric-audit-agent-py/tests/` (pytest).

## Test baseline

Green before/after each task. Package suite: `cd fabric-audit-agent-py && python -m pytest -q` (924 on
`main`). Agent-app suite: `cd fabric-audit-agent-app && python -m pytest -q` (run it to get the baseline
count in Task 1; keep it green + new tests).

---

## Task List

### Task 1 — "Presentation & Voice" section in both prompts + parity + honesty-guard tests

**Description:** Append the new section to the canonical `_SYSTEM` and the inlined copy, identically.

**Interface / content:** a `## Presentation & Voice` block (or clearly-delimited section) expressing,
verbatim in intent (final wording by the implementer, must cover all):
- Voice: concise senior capacity analyst — lead with the answer/verdict first sentence, professional,
  quietly confident, no filler.
- (1) No tool names/params/JSON in user-facing text — plain English actions.
- (2) Bias to act: obvious read-only next step within the step budget → take it and answer, don't end on
  a tool menu; genuine choices phrased as outcomes, not tool names.
- (3) Right-size: narrow Q → narrow A; full report only for audit-scale asks.
- (4) Caveats once & plain: translate `truncated`/`source:"mock"|"live"`/coverage into a plain caveat
  surfaced once; never print a raw flag; never drop the monitored-CU-proxy / mock / truncation
  disclosure.
- (5) Consistent numbers: always name the window; never make the user reconcile two of your own tables.
- All existing hard rules retained verbatim.

**Acceptance criteria:**
- [ ] Canonical `_SYSTEM` and inlined `_SYSTEM` are **byte-identical** (new parity test proves it by
  reading both files' literals).
- [ ] `build_system_prompt()` contains markers for the voice line + each of the 6 fixes.
- [ ] Existing hard-rule markers still present (read-only, "monitored CU"/proxy, injection
  "DATA, NOT INSTRUCTIONS", timestamps-verbatim, abstain) — honesty-guard test.
- [ ] Both suites green.

**Files:** `fabric_audit_agent/agent/system_prompt.py`, `fabric-audit-agent-app/agent_server/agent.py`,
a package prompt test, and a parity test (in the agent-app suite, reading both files). **Deps:** none. **Scope:** M.

---

### Task 2 — Humanize `_progress_text` + tests

**Description:** Replace the raw progress string with a plain-phrase mapping.

**Interface:**
```python
def _progress_text(name, inp):
    # 🔎 + phrase(name) + scope_hint(inp); NO tool name, NO JSON.
```
- **Phrase map** (spec table, user's final wording): `run_audit`→"running the capacity audit";
  `list_workspaces`→"listing the workspaces"; `user_activity`/`investigate_user`/`user_timeline`/
  `user_spike_history`→"looking into that user's activity"; `investigate_capacity_spike`/`spike_events`→
  "checking events with unusual spikes"; `raw_events`→"pulling the raw event stream";
  `capacity_patterns`/`capacity_diagnostics`→"analyzing capacity patterns"; `describe_source`/
  `sample_events`→"checking what the data source contains"; `diagnose`→"working through the diagnosis";
  `analyze_dax`→"reviewing the DAX"; `whats_changed`→"comparing against the last run"; `run_kql`→
  "running a read-only query"; `query_library`→"checking the query library".
- **Unmapped** → "working on it…".
- **Scope hint** (whitelist only): `user`→" for <user>", `item`→" for <item>", `topN`→" (top <N>)",
  `days`→" (last <N>d)"; any other key ignored; never render a value containing `{`/`}` or newline.
- Retain the leading `🔎`.

**Acceptance criteria:**
- [ ] Every one of the 18 tool names → its mapped phrase (table-driven test).
- [ ] Output never contains the tool `name` or a `{`/`}` (no JSON leak) — asserted for a call with args.
- [ ] Unmapped name → generic phrase; empty/`None` `inp` → no hint, no error.
- [ ] Scope hints render as human text (`(top 25)`, `for alice@co`), not JSON; a hostile value with
  `{`/newline is dropped.
- [ ] Agent-app suite green.

**Files:** `fabric-audit-agent-app/agent_server/agent.py`, `fabric-audit-agent-app/tests/test_agent_server.py`. **Deps:** none. **Scope:** S.

---

### Task 3 — Agent-app deploy enablement

**Description:** Make sure the redeploy actually ships the new prompt/progress (agent app caches like the
MCP app did in 3-B).

**Acceptance criteria:**
- [ ] Confirm the agent app's build/deploy cache mechanism (its `requirements.txt`/`app.yaml` under
  `fabric-audit-agent-app/`); if it caches on a requirements hash, bump its deploy marker so the
  redeploy reinstalls (the 3-B lesson). If it deploys source directly (no wheel/version cache), no bump.
- [ ] (Hygiene, optional) bump the `fabric-audit-agent` package version since `system_prompt.py` changed
  — NOT required for deploy since the MCP app isn't redeployed.
- [ ] Document in the PR: **deploy the agent app only**; no MCP redeploy.

**Files:** `fabric-audit-agent-app/requirements.txt` (or equivalent), maybe `pyproject.toml`. **Deps:** Tasks 1–2. **Scope:** XS.

---

### Checkpoint (feature complete)
- [ ] Both suites green; parity test proves identical prompts.
- [ ] `_progress_text` leaks no tool name/JSON for any of the 18 tools.
- [ ] Honesty-guard test green (no caveat dropped).
- [ ] Ready for opus adversarial final review (attack: does any presentation rule let a caveat be
  dropped / a mock read as live?).

## Global constraints block (verbatim into implementer + reviewer prompts)

- **Presentation-only.** No tool behavior, schema, input_schema, or data-path change. Prompts additive.
- **Plain-language ≠ less-honest.** Never drop/soften the monitored-CU-proxy, mock-vs-live, truncation,
  or coverage disclosures; translate flags, never suppress. Read-only-absolute + the three invariants hold.
- **Prompt parity is mandatory** — the two `_SYSTEM` copies must be byte-identical; the new parity test
  enforces it.
- Conventions (Part 1e): data keys camelCase, identifiers snake_case; nullish-not-falsy; stdlib-only;
  Python ≥3.10. Offline deterministic tests; keep both suites green.
- Do the work YOURSELF; do not delegate to a nested agent.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph

```
Task 1 (prompts + parity) ─┐
Task 2 (_progress_text)  ──┼─→ Task 3 (deploy enablement)
```
Tasks 1 & 2 are independent (different surfaces) but both are small; run 1 then 2 sequentially for
simplicity. Task 3 last.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A presentation rule lets the model drop a caveat to read cleaner | High (honesty) | Explicit "translate never drop" wording + honesty-guard test + adversarial review lens |
| Prompt copies drift | Med | New byte-parity test (reads both files) |
| Progress hint leaks a raw value / JSON | Low | Whitelist keys + `{`/newline guard + test |
| Redeploy serves stale prompt (cache) | Med | Task 3 marker bump (3-B lesson) |

## Open questions
- None blocking. (Voice = concise senior analyst; phrase wording finalized by the user incl.
  "unusual spikes" and "the query library".)

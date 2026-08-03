# Implementation Plan: Conversation-Logging Seam (Phase 5.4a) — v2

**Spec:** `docs/superpowers/specs/2026-07-09-conversation-logging-design.md`
**Branch:** `feat/conversation-logging` (off `main` `bead87b`)
**Method:** superpowers SDD, TDD, per-task review. Observability-only in the AGENT APP; no data-path/answer change.
**v2:** after 2 plan-reviewers (technical-accuracy + opus PII/honesty). Change log at bottom.

## Overview

Emit one privacy-minded `[conversation]` audit line per turn from the agent app's shared `_run`, capturing
the mineable eval signals. The scrub is **deliberately more aggressive than `redact.py`** (free text, not
KQL) — it adds JWT + connection-string shapes. Miner deferred. Agent-app only.

## Architecture decisions (grounded + review-adjusted)
- **Capture point:** `agent_server/agent.py::_run(request, on_tool=...)` (line 360) — the single path
  `invoke_handler` (line 371) and `stream_handler` (line 452) both call. Emit after `_run_tool_loop`
  returns; `_run`'s return value unchanged.
- **Question:** from `_messages_from_request(request)` (agent.py:**335**; returns
  `list[{"role","content":str}]`, content already flattened). `_run` currently calls it inline as a kwarg
  — bind it to a local `messages = _messages_from_request(request)`, pass that to the loop AND use it for
  the question: `question = next((m["content"] for m in reversed(messages) if m.get("role")=="user"), "")`.
  Then **scrub THEN truncate** (cap 500).
- **toolsCalled = names only:** `[t["tool"] for t in trajectory]`. A trajectory entry is
  `{"tool", "input"}` (agent.py:180) — `input` holds PII args; NEVER serialize the entry.
- **Scrub (inline, more aggressive than redact.py):** (a) redact allowlist (`sig=`/`bearer`/allowlisted
  `key=value`/URL creds) + (b) JWT shape (`eyJ...\.…\.…`) + (c) connection-string
  (`(?i)(accountkey|sharedaccesskey|password)\s*=`). Self-contained (no package import, per 5.1).
- **`abstainedHint`** (renamed from `abstained`): coarse regex on answer text; an author-guessed
  approximation labeled a hint (the miner/human refines to `expectAbstain`).
- **`ts`:** ISO-UTC timestamp for miner ordering/dedup.
- **Failure isolation:** wrap ONLY the emit (not the loop/return) in try/except; on error skip + log at
  most `type(exc).__name__`, NEVER `str(exc)`/the question.
- **Deploy:** agent-app only; bump `fabric-audit-agent-app/pyproject.toml` 0.1.1 → 0.1.2.

## Confirmed interfaces (verified by tech-accuracy review)
- `_run(request, on_tool=None)` @ agent.py:360; shared by both handlers. `_run_tool_loop` returns
  `{"text","trajectory","toolResults","stoppedReason"}` (agent.py:162-163, 185-186).
- `trajectory` entry = `{"tool": name, "input": args}` (agent.py:180). `_messages_from_request` @ agent.py:335.
- Agent-app tests: unittest; `_Block`/`_Resp` fakes (test_agent_server.py:280/285); `_load_agent_module` stubs mlflow/databricks_mcp; drive `_run`/`stream_handler`, assert via capsys. No network.
- `fabric-audit-agent-app/pyproject.toml` version = 0.1.1. Agent app does NOT import `fabric_audit_agent`.

## Test baseline
Agent-app: `cd fabric-audit-agent-app && python -m pytest -q` (52). Package: 992 (untouched). Keep both green.

---

## Task 1 — `[conversation]` capture + aggressive inline scrub + tests + deploy bump

**Changes:** add `_scrub_secrets(text)` + `_conversation_audit_log(question, trajectory, text)` to
`agent_server/agent.py`; bind `messages` local in `_run` and call the log after the loop (emit-only
try/except); bump the agent-app version.

**Acceptance criteria:**
- [ ] One `[conversation] {...}` line per `_run` turn: `tag`, `ts`(iso-utc), `question`(scrubbed+truncated), `toolsCalled`(names from trajectory), `toolCount`, `abstainedHint`(bool), `answerChars`(int) — capsys, fake client (no network); `_run` return value unchanged.
- [ ] **Scrub strength:** a question with `sig=abc` / `bearer x` / `client_secret=y` → masked; **`AccountKey=<b64>==` (connection string) → masked**; **a bare JWT `eyJ...eyJ...sig` → masked**; benign `foo=bar`/`Status=200` unchanged.
- [ ] **Scrub THEN truncate:** a secret placed just BEFORE the 500 cap is masked (not merely truncated away); an over-long question is truncated.
- [ ] **No arg/answer leak:** plant a recognizable PII value in a tool `input` (e.g. `{"user":"secret@corp.com"}`) → absent from the line (names-only extraction); the full answer text never appears (only `answerChars`).
- [ ] **`abstainedHint`:** an abstaining answer → true; a confident verdict answer → false; a no-tool answer → `toolsCalled:[]`, `toolCount:0`.
- [ ] **Failure isolation:** force `_conversation_audit_log` to raise → `_run` still returns the answer; the error path emits NOTHING containing the planted secret/question (assert absence).
- [ ] Existing agent-app tests pass; version bumped 0.1.1 → 0.1.2; both suites green.

**Files:** `fabric-audit-agent-app/agent_server/agent.py`, `.../pyproject.toml`, `.../tests/test_conversation_log.py` (new). **Deps:** none. **Scope:** M.

---

### Checkpoint
- [ ] Both suites green; the 6 signals emitted; connection-string + JWT + allowlist secrets scrubbed; no tool-arg/answer/PII leak; failure-isolated (no re-leak in except); `_run` unchanged; deploy bumped.
- [ ] Ready for opus final review — attack: any secret shape (PEM? unpadded base64url? secret under a novel key?) still leaking to the log; any arg/answer leak via trajectory; does the except re-leak; is `abstainedHint` honestly a hint.

## Global constraints (verbatim into implementer + reviewer prompts)
- Observability-only; agent-app only; NO data-path/answer/tool change; `_run` return unchanged. Read-only + three invariants hold.
- Anti-exfil on the new sink: scrub (allowlist + JWT + connection-string) THEN truncate the question; tool NAMES only (never the trajectory entry/args); answer LENGTH only. Names pass (5.2).
- Emit failure-isolated (only the emit); the except never logs `str(exc)` or the question.
- camelCase data keys (`toolsCalled`,`answerChars`,`abstainedHint`) / snake_case ids; nullish-not-falsy; stdlib-only (`re`,`json`,`datetime`); py≥3.11. Offline deterministic tests (fakes; no network). Keep both suites green.
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Connection-string / JWT secret leaks (redact.py gaps) | High | scrub adds accountkey/sharedaccesskey + JWT shapes; tests plant both |
| Tool args / full answer leak via trajectory | Med | names-only extraction; test plants PII in a tool input, asserts absent |
| except re-leaks the raw question | Med | except logs only type name; test asserts secret absent from error path |
| Emit breaks a conversation | Med | emit-only try/except (not over loop/return); test forces a raise |
| abstainedHint mistaken for a verdict | Low | `Hint` suffix + coarse; miner/human refines; labeled |
| Deploy serves stale (uv cache) | Low | version bump 0.1.2 |

## Open questions
- None blocking.

## Change log (v1 → v2)
- **Technical-accuracy:** cite `_messages_from_request` @ agent.py:335 (not 230-280); bind `messages` local in `_run`; trajectory entry `{"tool","input"}` @ agent.py:180 (names-only); abstain wording is an author guess not the prompt's literal vocabulary (soften); 0.1.1 + self-contained + unittest-fakes confirmed.
- **Opus PII/honesty:** scrub made MORE aggressive than redact.py — add JWT + connection-string shapes (H1/H2); except must not log `str(exc)`/question (H3); test must plant PII in a tool arg (M1); rename `abstained`→`abstainedHint` (M2); scrub-then-truncate ordered + tested (L1); add `ts` for dedup (L3); noted single-turn under-capture for the miner (L2).

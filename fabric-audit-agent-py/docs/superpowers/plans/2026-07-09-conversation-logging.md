# Implementation Plan: Conversation-Logging Seam (Phase 5.4a)

**Spec:** `docs/superpowers/specs/2026-07-09-conversation-logging-design.md`
**Branch:** `feat/conversation-logging` (off `main` `bead87b`)
**Method:** superpowers SDD, TDD, per-task review. Observability-only in the AGENT APP; no data-path/answer change.

## Overview

Emit one privacy-minded `[conversation]` audit line per turn from the agent app's shared `_run`, capturing
the mineable eval signals (scrubbed+truncated question · tool names · coarse abstain flag · answer length).
No miner (deferred). Agent-app only; deploy = agent app.

## Architecture decisions (grounded in the code)
- **Capture point:** `agent_server/agent.py::_run(request, on_tool=...)` (~line 360) — the single path
  both `invoke_handler` (370) and `stream_handler` (443) use. `_run_tool_loop` returns
  `{text, trajectory, toolResults, ...}`; `trajectory` holds the tool calls. Emit after it returns; return unchanged.
- **Signals only:** `toolsCalled` = tool NAMES from `trajectory` (no args); `answerChars` = `len(text)` (no
  full answer); `question` = last user message, `_scrub_secrets`'d + truncated (cap 500). `abstained` =
  coarse regex on `text` (abstain vocabulary), labeled a hint.
- **Inline `_scrub_secrets`** (~5 lines mirroring `query/redact.py`: SAS `sig=`, `bearer <tok>`,
  allowlisted secret `key=value`) — the agent app is self-contained (no `fabric_audit_agent` import; inlines
  prompt+loop per 5.1), so this small duplication matches the established pattern.
- **Failure isolation:** wrap the emit in try/except; any error → skip the line, never break the answer.
- **Deploy:** agent-app only; bump `fabric-audit-agent-app/pyproject.toml` 0.1.1 → 0.1.2 (uv/deploy cache-bust).

## Confirmed interfaces
- `_run` returns the loop result dict; `_run_tool_loop` returns `{"text","trajectory","toolResults",...}` (agent.py:162).
- Request message parsing exists (`_messages_from_request`/handlers ~230-280) — reuse it to get the last user question.
- `[adhoc-kql]`/`[identity]` audit-line pattern: `print("[tag] " + json.dumps(rec, ...))`.
- Agent-app tests: `fabric-audit-agent-app/tests/test_agent_server.py` (unittest, `_Resp`/`_Block` fakes).

## Test baseline
Agent-app: `cd fabric-audit-agent-app && python -m pytest -q` (52 on main). Package: 992 (untouched). Keep both green.

---

## Task 1 — `[conversation]` capture + inline scrub + tests + deploy bump

**Changes:** add `_scrub_secrets(text)` + `_conversation_audit_log(question, trajectory, text)` helpers to
`agent_server/agent.py`; call the log from `_run` after the loop returns (try/except-isolated); bump the
agent-app version.

**Acceptance criteria:**
- [ ] One `[conversation] {...}` line per `_run` turn with `tag`, `question`(scrubbed+truncated), `toolsCalled`(names from trajectory, no args), `toolCount`, `abstained`(bool), `answerChars`(int) — capsys, fake client (no network).
- [ ] Secrets scrubbed: a question with `sig=abc`/`bearer x`/`client_secret=y` → masked in the line; benign `foo=bar`/`Status=200` unchanged; over-long question truncated to the cap.
- [ ] No tool ARGS in the line; no full answer text (only `answerChars`).
- [ ] `abstained`: abstaining answer → true; verdict answer → false. No-tool answer → `toolsCalled:[]`.
- [ ] Failure isolation: force `_conversation_audit_log` to raise → `_run` still returns the answer (no crash; line skipped).
- [ ] `_run` return value UNCHANGED; existing agent-app tests pass; version bumped to 0.1.2.
- [ ] Both suites green.

**Files:** `fabric-audit-agent-app/agent_server/agent.py`, `.../pyproject.toml`, `.../tests/test_agent_server.py` (or new `tests/test_conversation_log.py`). **Deps:** none. **Scope:** S/M.

---

### Checkpoint
- [ ] Both suites green; `[conversation]` line emits the 4 signals; no args/full-answer/secret leak; failure-isolated; `_run` unchanged; deploy bump done.
- [ ] Ready for opus final review — attack: any PII/secret/tool-arg/answer leak into the line? does the emit ever break a conversation? is `abstained` honestly a hint not a false verdict?

## Global constraints (verbatim into implementer + reviewer prompts)
- Observability-only; agent-app only; NO data-path/answer/tool change; `_run` return value unchanged. Read-only + three invariants hold.
- Anti-exfil on the new sink: scrub secrets + truncate the question; log tool NAMES only + answer LENGTH only (no args, no full answer). Names pass (5.2 decision).
- Emit must be failure-isolated (try/except) — never break a user's answer.
- camelCase data keys (`toolsCalled`,`answerChars`) / snake_case ids; nullish-not-falsy; stdlib-only; py≥3.11 (agent app). Offline deterministic tests (fakes; no network). Keep both suites green.
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Secret/PII in the question leaks to the log | Med | inline scrub + truncate; test with planted secrets |
| Tool args / full answer leak | Med | log names + length only; test asserts absence |
| Emit breaks a conversation | Med | try/except isolation; test forces a raise |
| `abstained` mis-stated as a verdict | Low | coarse heuristic labeled a hint; miner/human refines |
| Deploy serves stale (uv cache) | Low | version bump 0.1.2 |

## Open questions
- None blocking. (Decision made: logging seam now, miner later.)

# Conversation-Logging Seam (eval-flywheel capture) — Design Spec

**Date:** 2026-07-09 · **Roadmap:** Phase 5, item 4a (eval flywheel — the *capture* half) · **Status:** design, pre-plan
**Decision (user, 2026-07-09):** build the logging seam NOW so real conversations accumulate; build the
logs→candidate-eval-cases **miner LATER** (a ~10-min pass once data exists — the 3-A pattern applied to quality).

## Purpose

The golden agent-eval suite (`eval/agent_cases.json`, 19 cases: `{name, messages, script, expectTool,
expectAbstain}`) is hand-authored. To grow it from real usage, we must first *capture* real
conversations in a mineable form — today nothing does. This adds one honest, privacy-minded
`[conversation]` audit line per turn recording exactly the signals a future miner needs to propose a
golden case: **the question, the tools called, and whether the agent abstained.** No miner is built yet.

## Invariants

Read-only absolute (this only observes + logs; it changes no tool, data path, or answer). **Anti-exfil
discipline (from Phase 5.2) applies to this new log sink:** the question is a free-text field that could
contain a pasted secret, so it is secret-scrubbed and length-capped before logging; tool **arguments**
and the **full answer** are NOT logged (only tool names + answer length), minimizing PII/secret surface.
Honesty: the line records what actually happened (tools actually called, answer actually produced),
never a guess.

## Design

**Where:** `fabric-audit-agent-app/agent_server/agent.py`, in `_run(request, on_tool=...)` (line ~360) —
the single path both `invoke_handler` and `stream_handler` share. After `_run_tool_loop` returns
`{text, trajectory, toolResults, ...}`, emit one line and return unchanged.

**The line** (mirrors the `[adhoc-kql]`/`[identity]` audit-line pattern; captured by Databricks App logging):
```
[conversation] {"tag":"conversation","question":<scrubbed+truncated>,"toolsCalled":[<names>],
                "toolCount":N,"abstained":<bool>,"answerChars":<int>}
```
- **`question`** — the last user message text, run through an inline `_scrub_secrets` and truncated to a
  cap (e.g. 500 chars). Needed because the eval case's `messages` is the question.
- **`toolsCalled`** — tool NAMES in call order, from `trajectory` (no arguments — args can carry
  user/PII and aren't needed for `expectTool`). `toolCount` = len.
- **`abstained`** — a COARSE heuristic on the answer text (e.g. matches "abstain/insufficient/can't/
  don't have/not able" — the prompt's abstain vocabulary). Labeled a hint: the future miner / human
  reviewer refines it into `expectAbstain`. (We log the signal, not a verdict.)
- **`answerChars`** — `len(text)` only; the full answer is NOT logged (PII/secret minimization).

**Inline `_scrub_secrets`** (self-contained, ~5 lines mirroring `query/redact.py`'s allowlist: SAS
`sig=`, `bearer <tok>`, allowlisted `key=value`). The agent app deliberately does not import the
`fabric_audit_agent` package (it inlines the prompt + loop, per Phase 5.1), so this small duplication is
consistent with that established pattern — noted, not a new coupling. It is a FORMAT/secret control, not
a PII control (names pass, per the 5.2 decision).

**Failure isolation:** emitting the line must NEVER break a conversation — wrap it in try/except; on any
error, skip the line silently (a broken audit line must not fail the user's answer).

## Testing (TDD, offline, deterministic)

- `_run` emits exactly one `[conversation]` line per turn with the right shape (tag, toolsCalled names
  from the trajectory, toolCount, answerChars) — via capsys with a fake client/loop (no network).
- Question is scrubbed + truncated: a question containing `sig=abc` / `bearer x` / a `client_secret=` →
  masked in the line; an over-long question → truncated to the cap.
- Tool ARGS never appear in the line (only names); the full answer text never appears (only its length).
- `abstained` heuristic: an abstaining answer → `true`; a normal verdict answer → `false`.
- A no-tool direct answer → `toolsCalled: []`, `toolCount: 0`.
- Failure isolation: if scrubbing/serialization raises, the conversation still returns (no crash; line skipped).
- No behavior change: `_run`'s return value is unchanged; existing agent-app tests pass.
- Suites green (package + agent-app).

## Deploy

**Agent app only** (the conversation surface; same as Phase 5.1). Bump the agent-app version
(`fabric-audit-agent-app/pyproject.toml` 0.1.1 → 0.1.2) to bust the `uv`/deploy cache. No MCP/Job change.

## Explicitly NOT pursued — with reasons

- **The miner (logs → candidate eval cases)** — deferred by decision until real conversations exist
  (data-starved now, exactly as 3-A was); a later ~10-min pass. Tracked in the roadmap.
- **Logging tool arguments or the full answer** — PII/secret surface; not needed for `expectTool`/
  `expectAbstain`. Only names + answer length + the scrubbed question are captured.
- **Importing the Phase-5.2 egress gate into the agent app** — the app is self-contained (no package
  import); a minimal inline scrub matches the inlined-prompt/loop pattern. (If the app ever gains the
  package as a dep, switch to `apply_egress_controls`.)
- **A durable/queryable conversation store** — Phase 8 (durable memory); the stdout audit line is the
  interim capture, same as `[adhoc-kql]`.
- **Classifying a precise verdict/`expectAbstain`** — the miner/human does that; we log the raw signal.

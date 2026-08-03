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
[conversation] {"tag":"conversation","ts":<iso-utc>,"question":<scrubbed+truncated>,
                "toolsCalled":[<names>],"toolCount":N,"abstainedHint":<bool>,"answerChars":<int>}
```
- **`question`** — the last user message text (from `_messages_from_request`, agent.py:335; bind it to a
  local in `_run`), **scrubbed THEN truncated** to a cap (e.g. 500 chars). Order matters: a secret before
  the cap must be scrubbed; truncation is only a backstop for content past the cap.
- **`toolsCalled`** — tool NAMES only, extracted as `[t["tool"] for t in trajectory]` (a trajectory
  entry is `{"tool", "input"}` — agent.py:180; NEVER serialize the entry, `input` carries PII args).
- **`abstainedHint`** — a COARSE heuristic on the answer text (plausible abstain phrasing, e.g.
  "insufficient"/"cannot"/"can't"/"don't have"/"not able"). NOTE: this is an author-guessed approximation,
  NOT the prompt's literal vocabulary (the prompt says "insufficient"/"cannot" but the model answers in
  free prose), and the prompt's hypothesis language ("can't rule out X") can false-positive — hence the
  `Hint` suffix. The miner/human refines it into `expectAbstain`; we log a signal, never a verdict.
- **`answerChars`** — `len(text)` only; the full answer is NOT logged.
- **`ts`** — an ISO-UTC timestamp so the future miner can order/dedup interleaved concurrent-user lines.

**Inline `_scrub_secrets` — deliberately MORE aggressive than `query/redact.py`.** `redact.py`'s
allowlist is tight *because it also runs over KQL/URLs* (a blanket mask would corrupt `where Status=200`).
That constraint does NOT apply here — this runs over a free-text user *question*. So the scrub covers:
(a) the redact allowlist (`sig=`/`bearer <tok>`/allowlisted `key=value`/URL creds), PLUS (b) a **JWT
shape** (`eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` — catches a pasted bare token), PLUS (c)
**connection-string** secrets (`(?i)(accountkey|sharedaccesskey|password)\s*=` — the `\bkey=` allowlist
MISSES `AccountKey=`, the highest-realism leak when a user pastes a connection string). Self-contained
inline (the agent app doesn't import the package, per 5.1). FORMAT/secret control, not PII (names pass, 5.2).

**Failure isolation:** the emit must NEVER break a conversation — wrap ONLY the emit (after
`_run_tool_loop` returns; do NOT widen over the loop call or the `return`) in try/except. On error, skip
the line and log NOTHING containing the question — at most `type(exc).__name__` (never `str(exc)`, which
could echo the raw offending input back into the log).

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

# Eval Miner (eval-flywheel, mining half) — Design Spec

**Date:** 2026-07-09 · **Roadmap:** Phase 5, item 4b (eval flywheel — the *miner*) · **Status:** design, pre-plan
**Follows:** 5.4a conversation-logging (the `[conversation]` capture line, now deployed + accumulating).

## Purpose

Turn the `[conversation]` audit log (emitted by the deployed agent, 5.4a) into **candidate golden
agent-eval cases**, so the eval suite (`eval/agent_cases.json`) grows from real usage instead of only
hand-authored cases — the 3-A `mine-queries` pattern applied to eval quality. Offline CLI `mine-evals`.
Data-starved until conversations accumulate (capture shipped 2026-07-09); this builds the mechanism now.

## Invariants

Read-only absolute; offline; no data path touched. **Honesty-specific:** a mined skeleton is
INCOMPLETE by design — it carries the real `messages`/`expectTool`/`expectAbstain` a human can trust,
but NOT the `script` (tool-result replay fixtures) which was deliberately never logged (5.4a); the miner
must clearly mark the skeleton as needing a human-authored `script`, and must never emit a case that
would run as-is (see "preview-only").

## What the `[conversation]` line gives us (5.4a, live)
`{tag:"conversation", ts, question:<scrubbed+truncated>, toolsCalled:[names], toolCount, abstainedHint:bool, answerChars}`.
Maps to a golden case (`{name, messages, script, expectTool, expectAbstain}`) as: `question`→`messages`,
`toolsCalled`→`expectTool`, `abstainedHint`→`expectAbstain`. **`script` is NOT derivable** (not logged).

## Design (mirrors `query/mine.py` from 3-A)

**New pure/stdlib module `fabric_audit_agent/eval/mine_evals.py`:**
- `parse_conversation_lines(lines) -> list[dict]` — for each line containing `[conversation] `, json.loads
  the remainder; keep `tag=="conversation"` records; skip non-marker/malformed (never raise).
- `shape_key(question) -> str` — normalize for grouping: lowercase, collapse whitespace, strip trailing
  punctuation; placeholder bare numbers/quoted strings so "why did capacity spike at 3pm" and "...at 9am"
  group. Deterministic, pure.
- `rank_candidates(records, existing_cases, *, min_count=2, top_n=20) -> list[dict]` — group by
  `shape_key(question)`; **drop shapes already covered** by an existing `agent_cases.json` case (same
  `shape_key` on each case's user message); keep groups with `count >= min_count`; pick a representative
  question (most-frequent exact); aggregate `expectTool` = the most-common single tool in the group's
  `toolsCalled` (or `None` if the group never called a tool), `expectAbstain` = majority `abstainedHint`;
  sort (count desc, shapeKey asc); return top_n as `{question, expectTool, expectAbstain, hitCount, observedTools}`.
- `to_eval_skeletons(ranked) -> list[dict]` — project to a **skeleton** golden case:
  `{"name": <generated kebab>, "messages": [{"role":"user","content":<question>}], "expectTool": <tool|null>,
    "expectAbstain": <bool>, "script": "TODO: author replay fixtures — see existing cases",
    "_minedFrom": {"hitCount":N, "observedTools":[...]}}`. `name` = `mined-<slug-of-question>-<shorthash>`.

**CLI `mine-evals`** in `entrypoints.py` + `__main__.py`:
`python -m fabric_audit_agent mine-evals <logfile|-> [--min-count N] [--top N]`.
- **PREVIEW-ONLY (no `--write`).** Prints a ranked table (rank · hitCount · expectAbstain · expectTool ·
  one-line question) + a ready-to-paste skeleton JSON per candidate, each with the `script: "TODO…"`
  placeholder and a header line: *"skeletons — a human must author each `script` before adding to
  agent_cases.json."* No file is written: a mined case is incomplete (no `script`), and writing it into
  `agent_cases.json` would break the eval suite (a case with no script can't replay). This is the honest
  difference from 3-A's `--write` (whose templates were complete + runnable).
- `-` = stdin. Empty/below-threshold → a clean "no candidates" message.

## Testing (TDD, offline, deterministic)
- `parse_conversation_lines`: extracts conversation records; skips non-marker/malformed; tolerates a logger prefix before `[conversation] `.
- `shape_key`: questions differing only by number/time/quoted-value/whitespace/case group; genuinely different questions don't.
- `rank_candidates`: min_count/top_n honored; dedup vs an existing `agent_cases.json` case (same shape) works; `expectTool` = most-common tool; `expectAbstain` = majority; a no-tool group → `expectTool None`; deterministic order.
- `to_eval_skeletons`: skeleton has all keys, `script` is the TODO placeholder (NEVER a fabricated script), `name` is unique kebab; `_minedFrom` provenance present.
- CLI: preview writes nothing (assert no file mutation); "no candidates" path; `-` stdin; missing logfile → clean error string.
- Tool count unchanged (18); full suite green.

## Deploy
None — offline maintenance CLI (like `mine-queries`). Run by a human against captured agent-app logs; the
skeletons are reviewed, a `script` authored, then added to `agent_cases.json` via a normal PR.

## Explicitly NOT pursued — with reasons
- **`--write` into `agent_cases.json`** — a mined case lacks a `script` and would break the eval suite if
  run; skeletons are preview-only for human completion. (Differs from 3-A, whose templates were complete.)
- **Fabricating a `script`** — the tool-result fixtures were deliberately not logged (5.4a privacy); the
  miner must never invent them (would make an eval case that passes/fails on fake data — an honesty defect).
- **Auto-classifying a precise `expectAbstain`** — uses the coarse `abstainedHint` from 5.4a, labeled as
  a hint the human confirms.
- **Multi-turn cases** — 5.4a captures the last user message only; single-turn skeletons for v1.
- **A durable candidates store** — Phase 8; stdout preview is the interim, matching `mine-queries`.

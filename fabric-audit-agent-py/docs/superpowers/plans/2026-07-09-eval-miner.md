# Implementation Plan: Eval Miner (Phase 5.4b)

**Spec:** `docs/superpowers/specs/2026-07-09-eval-miner-design.md`
**Branch:** `feat/eval-miner` (off `main` `fd4e4fe`)
**Method:** superpowers SDD, TDD, per-task review. Offline; preview-only; no data-path change. Mirrors 3-A `mine-queries`.

## Overview
Offline `mine-evals` CLI: `[conversation]` logs (5.4a) → candidate golden agent-eval **skeletons**
(`messages`/`expectTool`/`expectAbstain`), preview-only, each with a `script: "TODO"` for human authoring.

## Architecture decisions (grounded)
- **New pure module `fabric_audit_agent/eval/mine_evals.py`** (stdlib only; mirrors `query/mine.py`).
- **Preview-only** — a mined case lacks a `script` (deliberately not logged, 5.4a); writing it into
  `agent_cases.json` would break the eval suite (the scorer replays the `script`). So skeletons go to
  stdout for human completion; the miner NEVER fabricates a `script`.
- **`expectTool` is a single string** (scorer does `case.get("expectTool") in tools_used`,
  score_investigations.py:76) — mine the most-common single tool per question group; `None` if no tool.
- **`expectAbstain`** from the majority `abstainedHint` (5.4a's coarse hint) — labeled, human-confirmed.
- **Dedup vs existing** `agent_cases.json` by applying `shape_key` to each case's user message.
- **Reuse the [conversation] format** (5.4a): `{tag,ts,question,toolsCalled,toolCount,abstainedHint,answerChars}`.

## Confirmed interfaces
- Golden case: `{name, messages:[{role,content}], script, expectTool:<str>, expectAbstain:<bool>}` (agent_cases.json; 19 cases). Scorer: `abstain_ok = result.abstained == expectAbstain`; `expectTool in tools_used` (score_investigations.py:33,72-76).
- `[conversation]` line: `print("[conversation] " + json.dumps({...}))` (agent_server/agent.py, 5.4a).
- CLI pattern: `entrypoints.py` `run_*_cli(rest, ...) -> str`; `__main__.py` dispatch on `cmd` (mirror `mine-queries`, run_mine_queries_cli).
- 3-A precedent: `query/mine.py` (parse/shape_key/rank/project) + `run_mine_queries_cli` — mirror its structure/tests.

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` → 992. Keep green + new tests.

---

## Task 1 — `mine_evals.py` (parse + shape_key + rank + skeletons) + tests
**Interface:** `parse_conversation_lines(lines)`, `shape_key(question)`, `rank_candidates(records, existing_cases, *, min_count=2, top_n=20)`, `to_eval_skeletons(ranked)` — as in the spec.
**Acceptance criteria:**
- [ ] `parse_conversation_lines`: extracts `tag=="conversation"` records; skips non-marker/malformed (no raise); logger-prefix-before-marker still extracted.
- [ ] `shape_key`: number/time/quoted-value/whitespace/case differences group; different questions don't.
- [ ] `rank_candidates`: min_count/top_n honored; dedup vs an existing agent_cases.json case (same shape) excluded; `expectTool` = most-common tool (None if no-tool group); `expectAbstain` = majority; deterministic order (count desc, shapeKey asc).
- [ ] `to_eval_skeletons`: keys `{name,messages,expectTool,expectAbstain,script,_minedFrom}`; `script` is the literal TODO placeholder (NEVER fabricated); `name` unique kebab; messages = `[{"role":"user","content":question}]`.
- [ ] Pure/deterministic; suite green.
**Files:** `fabric_audit_agent/eval/mine_evals.py`, `tests/test_mine_evals.py`. **Deps:** none. **Scope:** M.

## Task 2 — `mine-evals` CLI (preview-only) + tests
**Interface:** `run_mine_evals_cli(rest, base_dir=None, cases_path=None) -> str` in `entrypoints.py`; `mine-evals` dispatch in `__main__.py` (+ usage line). argparse: positional logfile (`-`=stdin), `--min-count`, `--top`. Reads existing cases from `agent_cases.json` (package-adjacent; `cases_path` override for tests). Prints ranked table + ready-to-paste skeleton JSON per candidate with the `script:"TODO"` placeholder and a "human must author each script" header. **Writes NOTHING.**
**Acceptance criteria:**
- [ ] Preview writes nothing (assert no file mutation); lists candidate(s) + skeletons with the TODO/header.
- [ ] `-` reads stdin; missing logfile → clean error string; empty/below-threshold → clean "no candidates".
- [ ] `python -m fabric_audit_agent mine-evals <file>` dispatches; tool count still 18.
- [ ] Suite green.
**Files:** `fabric_audit_agent/entrypoints.py`, `fabric_audit_agent/__main__.py`, tests. **Deps:** Task 1. **Scope:** S/M.

---

### Checkpoint
- [ ] Suite green; skeletons never carry a fabricated script; preview-only (no writes); tool count 18.
- [ ] Ready for opus final review — attack: could a skeleton ever look complete/runnable (fabricated script)? shape_key over/under-merge? any write to agent_cases.json? honesty of expectTool/expectAbstain framing.

## Global constraints (verbatim into implementer + reviewer prompts)
- Read-only absolute; offline; preview-only (NO --write, NO file mutation); NEVER fabricate a `script`.
- `expectTool` single string; `expectAbstain`/abstainedHint labeled a human-confirmed hint.
- camelCase data keys / snake_case ids; nullish-not-falsy; stdlib-only; py≥3.10. Offline deterministic tests. Keep suite green (992 + new). Tool count stays 18 (no MCP tool added).
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (mine_evals.py) → Task 2 (mine-evals CLI)
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Miner fabricates/implies a runnable script | High (honesty) | `script` is a literal TODO string; preview-only; test asserts placeholder |
| Skeleton written into the live suite breaks eval | Med | preview-only, no --write; test asserts no file mutation |
| shape_key over/under-merge | Med | number/time/quote placeholders + tests both directions |
| expectTool/expectAbstain overstated | Low | most-common/majority + labeled hint; human confirms |

## Open questions
- None blocking. (Preview-only chosen because a mined case can't be complete without a human-authored script.)

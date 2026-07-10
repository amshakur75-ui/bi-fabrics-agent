# Implementation Plan: Eval Miner (Phase 5.4b) — v2

**Spec:** `docs/superpowers/specs/2026-07-09-eval-miner-design.md`
**Branch:** `feat/eval-miner` (off `main` `fd4e4fe`)
**Method:** superpowers SDD, TDD, per-task review. Offline; preview-only; no data-path change. Mirrors 3-A `mine-queries`.
**v2:** after 3 plan-reviewers. Change log at bottom.

## Overview
Offline `mine-evals` CLI: `[conversation]` logs (5.4a) → candidate golden agent-eval **skeletons**, preview-only. Never fabricate a `script` (placeholder ERRORS if run — pinned by a test); `expectTool`/`expectAbstain` are labeled human-confirmed hints with the vote spread surfaced.

## Architecture decisions (grounded + review-corrected)
- **New pure module `fabric_audit_agent/eval/mine_evals.py`** (stdlib; mirrors `query/mine.py`).
- **Preview-only** — the scorer bracket-accesses `case["script"]` (KeyError if absent) and replays it; a script-less case breaks the suite. Skeletons → stdout for human completion; NEVER auto-write, NEVER fabricate a script.
- **Fail-loud placeholder:** `script` is a STRING → `_client_from_script` iterates it → `TypeError` if scored unedited. Pinned by a test (unedited skeleton fed to `score_agent_case` raises).
- **`expectTool`** = most-common single tool per group (scorer does `expectTool in tools_used`, single string); `None` for a no-tool group. **`expectAbstain`** = majority `abstainedHint` — NOT blank (scorer reads absent key as `bool(None)=False`, silently mis-labeling abstain-heavy cases). Surface `observedTools` + `abstainHintCounts` in `_minedFrom` so the human sees these are unverified.
- **Scorer caveat (for the preview header):** `score_agent_case` abstain (score_investigations.py:70-72) is a text heuristic — `expectAbstain:true` only passes if the human-authored script's final text contains one of `can't/cannot/insufficient/enable monitoring/abstain`.
- **`shape_key`** adds a time-token rule (`\d{1,2}(:\d{2})?\s*(am|pm)`→`<TIME>`) so "3pm↔9am" merge; negations survive (don't merge).
- **`to_eval_skeletons(ranked, existing_cases)`** dedups the generated `name` vs existing case names (lengthen hash on collision, per 3-A `to_library_entries`).

## Confirmed interfaces (verified by tech-accuracy)
- `[conversation]` line (agent_server/agent.py `_conversation_audit_log`): `[conversation] ` + json `{tag,ts,question,toolsCalled,toolCount,abstainedHint,answerChars}`. Miner parses text (no import).
- Scorer `score_agent_case` (eval/score_investigations.py:65-100): `client_factory=lambda c:_client_from_script(c["script"])` (bracket → KeyError if missing); abstain = 5-token text heuristic on the answer (:70-72); `expectTool in tools_used` (:76/:80). `agent_cases.json` @ eval/ (package-adjacent). NOT `score_investigation_case:33` (different schema).
- CLI mirror: `run_mine_queries_cli(rest, base_dir=None, library_path=None)` @ entrypoints.py:289; `__main__.py:66-67` dispatches `mine-queries`. 3-A module: `query/mine.py` (+ tests/test_mine.py). Tool count stays 18 (CLI only, no tools.py change).

## Test baseline
`cd fabric-audit-agent-py && python -m pytest -q` → 992. Keep green + new tests.

---

## Task 1 — `mine_evals.py` (parse + shape_key + rank + skeletons) + tests
**Interface:** `parse_conversation_lines`, `shape_key`, `rank_candidates(records, existing_cases, *, min_count=2, top_n=20)`, `to_eval_skeletons(ranked, existing_cases)` — as in the spec.
**Acceptance criteria:**
- [ ] `parse_conversation_lines`: extracts `tag=="conversation"`; skips non-marker/malformed (no raise); logger-prefix tolerated.
- [ ] `shape_key`: number / **time (3pm↔9am)** / quoted-value / whitespace / case MERGE; different questions AND **negation ("did spike" vs "did NOT spike") do NOT merge**.
- [ ] `rank_candidates`: min_count/top_n honored; **dedup vs an existing agent_cases.json case (same shape) excluded**; **representative = most-frequent exact**; `expectTool` = most-common (None for no-tool group); `expectAbstain` = majority; **`observedTools` + `abstainHintCounts` populated**; deterministic (count desc, shapeKey asc).
- [ ] `to_eval_skeletons`: all keys `{name,messages,expectTool,expectAbstain,script,_minedFrom}`; `script` = the ERROR-if-run placeholder (never fabricated); `name` unique kebab, **deduped vs existing_cases (forced collision → lengthened)**; `_minedFrom` carries hitCount/observedTools/abstainHintCounts.
- [ ] **Fail-loud pin:** a `to_eval_skeletons` output fed unedited to `score_agent_case` **raises** (cannot be scored). (Import the scorer in the test.)
- [ ] Pure/deterministic; suite green.
**Files:** `fabric_audit_agent/eval/mine_evals.py`, `tests/test_mine_evals.py`. **Deps:** none. **Scope:** M.

## Task 2 — `mine-evals` CLI (preview-only) + tests
**Interface:** `run_mine_evals_cli(rest, base_dir=None, cases_path=None) -> str` in `entrypoints.py`; `mine-evals` dispatch in `__main__.py` (+ usage line). argparse: positional logfile (`-`=stdin), `--min-count`, `--top`. Reads existing cases from `agent_cases.json` (package-adjacent; `cases_path` override for tests). Prints ranked table + skeleton JSON per candidate + the header (author-script / abstain-token / strip-_minedFrom / hints-unverified). **Writes NOTHING.**
**Acceptance criteria:**
- [ ] Preview writes nothing (assert no file mutation); lists candidate(s) + skeletons + the full header.
- [ ] `-` reads stdin; missing logfile → clean error string; empty/below-threshold → clean "no candidates".
- [ ] **`--min-count`/`--top` at the CLI level take effect** (a shape below the raised min-count is excluded).
- [ ] `python -m fabric_audit_agent mine-evals <file>` dispatches; tool count still 18.
- [ ] Suite green.
**Files:** `fabric_audit_agent/entrypoints.py`, `fabric_audit_agent/__main__.py`, tests. **Deps:** Task 1. **Scope:** S/M.

---

### Checkpoint
- [ ] Suite green; the fail-loud pin passes (unedited skeleton unscoreable); preview-only (no writes); tool count 18.
- [ ] Ready for opus final review — attack: any skeleton that could silently pass/fail the suite; shape_key over/under-merge; honesty of expectTool/expectAbstain framing + the abstain-token caveat.

## Global constraints (verbatim into implementer + reviewer prompts)
- Read-only, offline, preview-only (NO --write, NO file mutation); NEVER fabricate a `script` (placeholder ERRORS if run).
- `expectTool` single string; `expectAbstain` = majority hint (never blank) with `abstainHintCounts` surfaced; labeled unverified. Header states the scorer's 5-token abstain requirement.
- camelCase data keys / snake_case ids; nullish-not-falsy; stdlib-only; py≥3.10. Offline deterministic tests. Keep suite green (992 + new). Tool count 18.
- Do the work YOURSELF; no nested agent. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Dependency graph
```
Task 1 (mine_evals.py) → Task 2 (mine-evals CLI)
```

## Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| A mined skeleton silently passes/fails the suite | High (eval integrity) | script placeholder ERRORS if run + a test pinning "unedited → scorer raises"; preview-only |
| Blank expectAbstain silently = false (abstain-heavy suite) | Med | emit majority guess + abstainHintCounts; never blank |
| shape_key over/under-merge | Med | time-token + number/quote rules; negation must-not-merge test; both-direction tests |
| Generated name collides with an existing case | Low | to_eval_skeletons dedups vs existing_cases (lengthen hash) |
| Human forgets to strip _minedFrom / author script | Low | preview header instructs; _minedFrom is non-schema (scorer ignores) |

## Open questions
- None blocking.

## Change log (v1 → v2)
- **Tech-accuracy:** fixed scorer to `score_agent_case` (5-token text-heuristic abstain; `script` bracket-access → KeyError); added the abstain-token human-authoring caveat to the header.
- **Opus:** fail-loud pin test (unedited skeleton → scorer raises); expectAbstain = majority (never blank) + `abstainHintCounts`; shape_key time-token + negation test; `to_eval_skeletons(ranked, existing_cases)` name-dedup; strip-`_minedFrom` header line.
- **Coverage:** explicit ACs for representative-selection + `observedTools`; CLI-level `--min-count`/`--top` test.

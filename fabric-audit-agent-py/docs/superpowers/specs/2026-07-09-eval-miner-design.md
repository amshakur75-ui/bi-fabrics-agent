# Eval Miner (eval-flywheel, mining half) — Design Spec — v2

**Date:** 2026-07-09 · **Roadmap:** Phase 5, item 4b · **Status:** design, pre-plan
**Follows:** 5.4a conversation-logging (the `[conversation]` capture line, deployed + accumulating).
**v2:** after 3 plan-reviewers (coverage / technical-accuracy / opus eval-integrity). Change log at bottom.

## Purpose

Turn the `[conversation]` audit log (5.4a) into **candidate golden agent-eval SKELETONS**, so the eval
suite (`eval/agent_cases.json`) grows from real usage — the 3-A `mine-queries` pattern applied to eval
quality. Offline CLI `mine-evals`, **preview-only**. Data-starved until conversations accumulate.

## Invariants + eval-integrity honesty rules
Read-only, offline, no data path touched. Feature-specific (a mined case gates FUTURE agent quality, so
honesty is load-bearing):
- **Never fabricate a `script`** (the replay fixtures were deliberately not logged, 5.4a). The skeleton's
  `script` is a placeholder that **must ERROR if scored unedited** — fail-loud, so a pasted-unedited
  skeleton can never silently pass (or fail) the suite. Pinned by a test.
- **A skeleton is never written into `agent_cases.json`** (preview-only): the scorer bracket-accesses
  `case["script"]` (`KeyError` if absent) and replays it — a script-less case breaks the suite.
- **`expectTool`/`expectAbstain` are labeled, human-confirmed guesses**, with the vote spread surfaced
  (`observedTools`, `abstainHintCounts`) so the human sees they're unverified, not authoritative.

## Scorer semantics (verified — the target contract)
`score_agent_case` (`eval/score_investigations.py:65-100`) is the scorer for `agent_cases.json`:
- `client_factory = lambda c: _client_from_script(c["script"])` — **bracket access**; a missing `script`
  raises `KeyError` before scoring (justifies preview-only, never auto-write).
- **abstain** (:70-72): `abstained = any(w in out["output_text"].lower() for w in
  ("can't","cannot","insufficient","enable monitoring","abstain"))`; then `abstain_ok = abstained ==
  bool(case.get("expectAbstain"))`. It's a **text heuristic on the answer**, NOT a `result.abstained`
  flag (that flag belongs to `score_investigation_case`, a DIFFERENT case schema — do not conflate).
  ⇒ a human completing an `expectAbstain: true` skeleton must author the script's final text to contain
  one of those five tokens, or the case fails regardless of the mined label. The preview must say this.
- **tool** (:76/:80): `case.get("expectTool") in tools_used` — `expectTool` is a single string.

## Design (mirrors `query/mine.py` from 3-A)

**New pure/stdlib module `fabric_audit_agent/eval/mine_evals.py`:**
- `parse_conversation_lines(lines) -> list[dict]` — for each line containing `[conversation] ` (logger
  prefix may precede), json.loads the remainder; keep `tag=="conversation"`; skip non-marker/malformed (never raise).
- `shape_key(question) -> str` — normalize for grouping, in order: lowercase; collapse whitespace; strip
  trailing punctuation; replace **time tokens** `\d{1,2}(:\d{2})?\s*(am|pm)` → `<TIME>` (so "spike at
  3pm"/"…9am" merge); replace remaining bare numbers → `<N>`; blank quoted-string contents → `<S>`.
  Deterministic, pure. Negation words (`not`/`no`/`n't`) survive (so "why did it spike" vs "why did it
  NOT spike" stay distinct).
- `rank_candidates(records, existing_cases, *, min_count=2, top_n=20) -> list[dict]` — group by
  `shape_key(question)`; **drop shapes already covered** by an existing `agent_cases.json` case (same
  `shape_key` on each case's last user message); keep groups with `count >= min_count`; representative
  question = **most-frequent exact** in the group; `expectTool` = most-common single tool across the
  group's `toolsCalled` (`None` if the group never called a tool); `expectAbstain` = majority
  `abstainedHint`; sort `(count desc, shapeKey asc)`; return top_n dicts:
  `{question, expectTool, expectAbstain, hitCount, observedTools:{tool:count}, abstainHintCounts:{"true":N,"false":M}}`.
- `to_eval_skeletons(ranked, existing_cases) -> list[dict]` — project each to a skeleton golden case:
  `{"name": <unique kebab, deduped vs existing_cases' names — lengthen hash on collision, per 3-A>,
    "messages": [{"role":"user","content":<question>}], "expectTool": <tool|null>,
    "expectAbstain": <bool>, "script": "REPLACE-ME: author replay fixtures (this string ERRORS if run)",
    "_minedFrom": {"hitCount":N, "observedTools":{...}, "abstainHintCounts":{...}}}`.
  The `script` is a **string** placeholder → `_client_from_script` iterates it → `TypeError` if scored
  unedited (fail-loud; pinned by a test). `_minedFrom` is provenance (non-schema; human strips before commit).

**CLI `mine-evals`** in `entrypoints.py` + `__main__.py`:
`python -m fabric_audit_agent mine-evals <logfile|-> [--min-count N] [--top N]`.
- **PREVIEW-ONLY (no `--write`, no file mutation).** Prints a ranked table (rank · hitCount · expectAbstain ·
  expectTool · one-line question) + a ready-to-paste skeleton JSON per candidate, under a header:
  *"Skeletons — before adding to agent_cases.json you MUST: (1) author a real `script` (the placeholder
  ERRORS if run); (2) for expectAbstain:true, ensure the script's final text contains one of
  can't/cannot/insufficient/enable monitoring/abstain; (3) delete `_minedFrom`. expectTool/expectAbstain
  are unverified hints — see observedTools/abstainHintCounts."*
- `-` = stdin; empty/below-threshold → clean "no candidates"; missing logfile → clean error string.

## Testing (TDD, offline, deterministic)
- `parse_conversation_lines`: extracts conversation records; skips non-marker/malformed (no raise); logger-prefix tolerated.
- `shape_key`: number/**time (3pm↔9am)**/quoted-value/whitespace/case differences MERGE; genuinely different questions and **negations ("did spike" vs "did NOT spike") do NOT merge**.
- `rank_candidates`: min_count/top_n honored; dedup vs an existing agent_cases.json case (same shape) excluded; representative = most-frequent exact; `expectTool` = most-common (None for no-tool group); `expectAbstain` = majority; `observedTools`/`abstainHintCounts` populated; deterministic order.
- `to_eval_skeletons`: all keys present; `script` is the ERROR-if-run placeholder (never fabricated); `name` unique + deduped vs existing_cases (forced-collision → lengthened); `_minedFrom` provenance present.
- **Fail-loud pin:** feed a `to_eval_skeletons` output to `score_agent_case` unedited → it **raises** (cannot be scored/pass). This locks the honesty guarantee against regression.
- CLI: preview writes nothing (assert no file mutation + header present); `-` stdin; missing logfile → clean error; "no candidates" path; `--min-count`/`--top` at the CLI level take effect.
- Tool count unchanged (18); full suite green.

## Deploy
None — offline maintenance CLI (like `mine-queries`).

## Explicitly NOT pursued — with reasons
- **`--write` into `agent_cases.json`** — a mined case lacks a human `script` and breaks the suite; skeletons are preview-only.
- **Fabricating a `script`** — never; the placeholder errors if run.
- **Leaving `expectAbstain` blank** — the scorer reads an absent key as `False` (`bool(None)`), silently mis-labeling the abstain-heavy suite; so we emit the majority guess + surface `abstainHintCounts`.
- **Multi-turn cases** — 5.4a logs the last user message only; single-turn v1.
- **A durable candidates store** — Phase 8; stdout preview is interim.

## Change log (v1 → v2)
- **Technical-accuracy:** corrected the scorer to `score_agent_case` (text-heuristic abstain: 5 tokens; `expectTool in tools_used`; `script` bracket-access → KeyError); added the human-authoring caveat that an `expectAbstain:true` script's text must hit a scorer abstain token.
- **Opus eval-integrity:** pin fail-loud with a test (unedited skeleton → scorer raises); keep `expectAbstain` as majority guess (NOT blank — blank = silent false) + surface `abstainHintCounts`; add a `shape_key` time-token rule so the "3pm↔9am" claim holds + a negation "must-not-merge" test; `to_eval_skeletons` takes `existing_cases` for name-dedup (3-A precedent); header tells the human to strip `_minedFrom`.
- **Coverage:** explicit ACs for representative-selection + `observedTools`; CLI-level `--min-count`/`--top` test.

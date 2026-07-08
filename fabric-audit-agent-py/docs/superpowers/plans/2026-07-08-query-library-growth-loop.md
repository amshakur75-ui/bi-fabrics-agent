# Implementation Plan: Query-Library Growth Loop (Part 3-A)

**Spec:** `docs/superpowers/specs/2026-07-08-query-library-growth-loop-design.md`
**Branch:** `feat/query-library-growth-loop` (head `d4236a5`, on `main` `29b1528` + schema-mirror fix `9c36cc3`)
**Repo root:** `C:/Users/am08570/ClaudeCode-Workspace/bi-fabrics-agent` · package under `fabric-audit-agent-py/`
**Method:** superpowers subagent-driven-development, TDD, per-task review (HANDOFF Part 4).

> Plan lives at the project's SDD path (`docs/superpowers/plans/`), not the skill's generic
> `tasks/plan.md`, because `scripts/task-brief`, `scripts/review-package`, and the 3 plan-reviewers all
> key off this location. The task list below *is* the todo list.

## Overview

Build an **offline** `mine-queries` CLI that grows `query_library.json` from the `[adhoc-kql]` stdout
audit log the firewall emits. Preview by default; `--write` appends firewall-revalidated, provenance-
tagged candidates to the library, landing via the normal PR → CI → merge gate. Three pure functions in
a new `query/mine.py` + one CLI seam in the existing `entrypoints.py`/`__main__.py`.

## Architecture decisions (with rationale)

- **`query/mine.py` is pure/stdlib — no file I/O.** All parsing/normalization/ranking is
  deterministic and I/O-free (matches the spec's "pure/stdlib module"). File reads (the log, the
  library) and the `--write` mutation live in `entrypoints.py`, which already owns file side effects
  (mirrors `run_audit_cli`). This keeps `mine.py` trivially unit-testable with in-memory fixtures.
- **The grounding bar is re-enforced by *calling the real* `validate_adhoc_kql`** — never a
  re-implementation (Part 4.12: one validator, no weaker copy). Import
  `from .query.firewall import validate_adhoc_kql, FirewallRejection`.
- **Fail-closed on redaction via a single `"***" in text` check.** All three `redact_secrets` subs
  emit a `***` run (`://***:***@`, `bearer ***`, `key=***` — `redact.py:29-31`), so any representative
  containing `***` was redacted at capture and never ran in that form → dropped. One substring test
  catches the whole class (Part 4.12: close the class, not the instance).
- **Strip a trailing `| take <int>` before both `shape_key` and dedup**, on the mined side *and* the
  library side, so a mined query normalizes identically to the hand-authored template it matches
  (the allowed audit line logs `f"{kql}\n| take {N}"` — `tools.py:1568,1576`).
- **`hitCount` is an extra entry key — verified safe:** `test_query_library.py:27` uses a superset
  check (`set(x) >= {…}`) and `query_library_handler` (`tools.py:1597-1604`) keys off fixed fields.
- **CLI parses its own args with `argparse`** inside `run_mine_queries_cli(rest, base_dir=None)`;
  `__main__.py` just routes `mine-queries` → that function (the other CLIs pass `rest` through the same
  way). Keeps arg handling testable via arg-list fixtures.

## Confirmed interfaces (grounded in the code — for the plan-reviewers)

- `firewall.validate_adhoc_kql(kql)` → returns `kql` unchanged, or raises
  `FirewallRejection(reason, stage)`. Pure, offline, no engine. (`firewall.py:107-162`)
- `kql_guard._strip_string_literals(s)` (blanks literal *content*, keeps quotes+length),
  `kql_guard.first_statement(s)`. Importable, pure. (`kql_guard.py`)
- `redact.redact_secrets` sentinels all contain `***`. (`redact.py:25-32`)
- `[adhoc-kql]` line: `print("[adhoc-kql] " + json.dumps(rec, ensure_ascii=False, separators=(",", ": ")))`.
  **Allowed** rec keys: `tag, engine, verdict:"allowed", rowCount, kql` (kql = bounded+redacted).
  **Rejected** rec keys: `tag, engine, verdict:"rejected", stage, reason` (no kql). `durationMs` is
  never passed on the run_kql path. (`tools.py:114-131, 1556-1576`)
- Library entry schema: `{name, category, engine, description, kql, groundedIn}`; `engine ∈
  {"capacity","la"}`; names unique + kebab (lowercase, no spaces); `description`/`groundedIn` non-empty.
  Loaded by `_load_query_library()` from package-adjacent `query_library.json`. (`tools.py:43-53`,
  `test_query_library.py`)
- `entrypoints.py` CLI pattern: each `run_*_cli(...)` returns a text block; `base_dir` redirects paths
  for tests. `__main__.main(argv)` dispatches on `cmd`.

## Test baseline

Green suite required after every task. On this machine (mcp installed) the baseline is **859 passed**
(post-`9c36cc3`); the author's machine reports `856 passed, 3 skipped` (mcp-gated tests skip there).
`cd fabric-audit-agent-py && python -m pytest -q`. Each task adds its own tests and keeps the suite green.

---

## Task List

### Task 1 — `parse_audit_lines` + `shape_key` (pure foundation)

**Description:** Create `fabric_audit_agent/query/mine.py` with the log parser and the shape
canonicalizer. No ranking yet.

**Interface:**
```python
def parse_audit_lines(lines) -> list[dict]:
    """Iterate text lines; for each line containing the '[adhoc-kql] ' marker, json.loads the
    JSON after the marker; skip lines without the marker and malformed JSON (never raise).
    Return only records with verdict == 'allowed'."""

def shape_key(kql: str) -> str:
    """Canonical grouping key. In order: (1) strip a trailing '| take <int>';
    (2) blank string-literal content via kql_guard._strip_string_literals;
    (3) replace ago(...)/datetime(...) args and bare numeric threshold literals with placeholders;
    (4) collapse whitespace; (5) lowercase KQL operators.
    NOT normalized: timespan-granularity literals (1h/1d/7d inside bin(...)) stay distinct."""
```

**Acceptance criteria:**
- [ ] `parse_audit_lines` extracts allowed records; skips rejected, non-marker, and malformed-JSON lines without raising.
- [ ] `shape_key`: queries differing only by date/threshold/whitespace/operator-case → same key; `| take 100` vs `| take 500` → same key; `bin(win,1h)` vs `bin(win,1d)` → different keys; genuinely different queries → different keys.
- [ ] Pure: no I/O, deterministic.

**Verification:** `python -m pytest tests/test_mine.py -q` green; full suite stays green.

**Files:** `fabric_audit_agent/query/mine.py`, `tests/test_mine.py`. **Dependencies:** None. **Scope:** S.

---

### Task 2 — `rank_candidates` (grouping, dedup, fail-closed drops, projection)

**Description:** Add the ranking + candidate projection to `mine.py`.

**Interface:**
```python
def rank_candidates(records, existing_templates, *, min_count=3, top_n=10) -> list[dict]:
    """Group allowed records by (engine, shape_key). Drop shapes already covered by
    existing_templates (SAME shape_key applied to library 'kql' — symmetric because both sides
    strip the trailing take). Keep groups with count >= min_count. Representative = the most
    frequent exact kql in the group (trailing '| take <int>' stripped), ties broken
    lexicographically. Apply fail-closed drops in order: (a) '***' in representative -> drop
    (redaction sentinel; never ran); (b) validate_adhoc_kql(rep) raises FirewallRejection -> drop.
    Sort surviving groups (count desc, shape_key asc); return top_n candidate dicts:
    {name, engine, category:'adhoc-mined', description, groundedIn:'mined from adhoc audit log',
     kql, hitCount}."""
```
- **`name`** — deterministic kebab: `f"adhoc-{engine}-{op}-{h}"` where `op` = dominant operator (e.g. `summarize`/`where`/`top`) and `h` = short stable hash of `shape_key` (e.g. first 6 hex of `hashlib.sha1(shape_key.encode()).hexdigest()` — `hashlib` is deterministic/stdlib, allowed). Lowercase, no spaces (satisfies the kebab test). A human renames on review.
- **`description`** — factual, generated, never a placeholder: e.g. `f"Auto-mined {engine} query ({op}); seen {hitCount}x in the ad-hoc audit log."` (non-empty, satisfies the description test).

**Acceptance criteria:**
- [ ] `min_count` and `top_n` honored; grouping by `(engine, shape_key)`.
- [ ] Dedup vs existing works, **including** a mined query equal to an existing template once trailing `| take` is stripped (must be deduped, not promoted).
- [ ] Fail-closed proven by tests: (a) representative containing `***` dropped even though logged "allowed"; (b) representative failing `validate_adhoc_kql` dropped.
- [ ] Every returned candidate carries all seven keys; `category=="adhoc-mined"`, non-empty `description`/`groundedIn`; `name` is unique-within-batch, kebab; and **each returned `kql` passes `validate_adhoc_kql`** (assert in-test).
- [ ] Deterministic ordering.

**Verification:** `python -m pytest tests/test_mine.py -q` green; full suite green.

**Files:** `fabric_audit_agent/query/mine.py`, `tests/test_mine.py`. **Dependencies:** Task 1. **Scope:** M.

---

### Task 3 — `mine-queries` CLI (preview + `--write`)

**Description:** Add `run_mine_queries_cli(rest, base_dir=None)` to `entrypoints.py` and route
`mine-queries` in `__main__.py`. Preview by default; `--write` appends candidates to
`query_library.json`.

**Interface / behavior:**
```python
# entrypoints.py
def run_mine_queries_cli(rest, base_dir=None) -> str:
    """argparse: positional logfile ('-' = stdin); --min-count (default 3); --top (default 10);
    --write. Read log text -> parse_audit_lines -> rank_candidates(existing=_load_query_library()).
    Preview: return a ranked table (rank · hitCount · engine · name · one-line kql) + a
    ready-to-paste JSON snippet per candidate; write nothing.
    --write: append candidates to query_library.json (package-adjacent path; base_dir override for
    tests), preserving existing entries, deterministic order, idempotent (re-run adds nothing since
    rank_candidates dedups vs the now-updated library); return a summary of what was added."""
```
- `__main__.py`: `elif cmd == "mine-queries": print(ep.run_mine_queries_cli(rest))`.
- Update the `__main__` module docstring usage block with the `mine-queries` line.
- `--write` writes with the library's existing formatting (`json.dump(..., indent=2, ensure_ascii=False)` — match the current file's shape; confirm by reading it) and a trailing newline.

**Acceptance criteria:**
- [ ] Preview mode writes **nothing** (no file mutation); prints candidates (or a clean "no candidates" message when the log is empty/below threshold).
- [ ] `--write` appends only firewall-passing candidates, preserves all existing entries, output stays schema-valid, and **re-running `--write` on the same log is idempotent** (no duplicates).
- [ ] After `--write`, `test_query_library.py`'s per-template firewall test still passes on the mutated file (every entry, old and new, passes the bar).
- [ ] `-` reads stdin; a missing logfile yields a clean error string, not a traceback.
- [ ] `python -m fabric_audit_agent mine-queries <file>` dispatches correctly.

**Verification:** `python -m pytest tests/test_mine.py tests/test_entrypoints.py tests/test_query_library.py -q` green; full suite green; manual `python -m fabric_audit_agent mine-queries <fixture-log>` and `... --write` against a temp library copy.

**Files:** `fabric_audit_agent/entrypoints.py`, `fabric_audit_agent/__main__.py`, `tests/test_mine.py` (or `tests/test_entrypoints.py`). **Dependencies:** Task 2. **Scope:** M.

---

### Checkpoint: after Task 3 (feature complete)
- [ ] Full suite green (≥ baseline + new tests); no skips regressed.
- [ ] `python -c "from fabric_audit_agent.tools import create_tool_definitions as f; print(len(f()))"` → still **18** (this feature adds NO MCP tool — offline CLI only).
- [ ] `python -m fabric_audit_agent mine-queries -` (empty stdin) exits clean with "no candidates".
- [ ] Read-only holds: nothing touches Fabric/Databricks; `--write` mutates only `query_library.json`.
- [ ] Ready for opus final whole-branch review.

---

## Global constraints block (copy verbatim into every implementer + reviewer prompt)

- **Read-only absolute.** No Fabric/Databricks calls; no network; `--write` may edit ONLY `fabric_audit_agent/query_library.json`. The tool never runs in the App.
- **Never label mock/proxy as live; no dishonest provenance.** Mined entries MUST carry `category:"adhoc-mined"`, `groundedIn:"mined from adhoc audit log"`, and a real (non-placeholder, non-empty) `description`. No `TODO`/placeholder text ever written to the library.
- **Never loosen the grounding bar.** Re-validate every candidate by CALLING `validate_adhoc_kql` (not a copy); drop on `FirewallRejection`. Also drop any representative containing `***` (redaction sentinel). The offline re-check is the STATIC half of the bar — the same bar CI already enforces per-template.
- **Conventions (Part 1e):** data keys camelCase, Python identifiers snake_case; nullish-not-falsy (`x if x is not None else d`); numeric guard excludes bool + non-finite; stdlib-only (`json`/`re`/`hashlib`/`argparse` OK); Python ≥3.10.
- **Tests:** offline, deterministic, injected fixtures; never hit an endpoint. Keep the suite green (baseline 859 on this machine / 856+3-skip on author's). TDD: failing test → implement → green → commit.
- **Do the work YOURSELF (Edit/Write/Bash); do NOT delegate to a nested agent.**
- **Commit trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (system-mandated; overrides the HANDOFF's Fable-5 trailer).

## Dependency graph

```
Task 1 (parse + shape) → Task 2 (rank + project) → Task 3 (CLI + --write)
```
Strictly sequential; no parallelization (single small module + one CLI seam).

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Mined text is bounded+redacted, not the original (reviewer Critical) | High — broken dedup + dishonest/non-grounded promotion | Strip trailing `\| take` both sides; drop `***`-containing reps; Task 2 acceptance tests both |
| A representative passes the STATIC gate but wouldn't bind live | Med | Grounded by `verdict=="allowed"` at capture + agent re-rehearses on every `run_kql`; documented honestly in spec invariant #3 |
| `shape_key` false-merge collapses distinct queries | Med | Keep `bin()` granularity distinct; explicit test fixtures; representative is a real concrete query |
| `--write` corrupts library formatting / ordering | Med | Match existing `json.dump` shape; idempotency + schema-valid + per-template-firewall tests on the mutated file |
| Data-starved (log empty until 3-B deployed) | Low | Preview/`--write` degrade to "no candidates"; `min_count=3` default sensible for real usage; first real pass waits on 3-B |

## Open questions

- None blocking. (`min_count=3`, `top_n=10` defaults approved in brainstorming; auto-write + human-merge gate approved as the Q3 reconciliation.)

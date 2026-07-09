# Implementation Plan: Query-Library Growth Loop (Part 3-A)

**Spec:** `docs/superpowers/specs/2026-07-08-query-library-growth-loop-design.md`
**Branch:** `feat/query-library-growth-loop` (head `708da6d`, on `main` `29b1528` + schema-mirror fix `9c36cc3`)
**Repo root:** `C:/Users/am08570/ClaudeCode-Workspace/bi-fabrics-agent` · package under `fabric-audit-agent-py/`
**Method:** superpowers subagent-driven-development, TDD, per-task review (HANDOFF Part 4).
**Revision:** v2 — incorporates all findings from the 3 plan-reviewers (coverage / technical-accuracy / improvability). Change log at the bottom.

> Plan lives at the project's SDD path (`docs/superpowers/plans/`), not the skill's generic
> `tasks/plan.md`, because `scripts/task-brief`, `scripts/review-package`, and the plan-reviewers all
> key off this location. The task list below *is* the todo list.

## Overview

Build an **offline** `mine-queries` CLI that grows `query_library.json` from the `[adhoc-kql]` stdout
audit log the firewall emits. Preview by default; `--write` appends firewall-revalidated, provenance-
tagged candidates to the library, landing via the normal PR → CI → merge gate. Pure functions in a new
`query/mine.py` + one CLI seam in the existing `entrypoints.py`/`__main__.py`.

## Architecture decisions (with rationale)

- **`query/mine.py` is pure/stdlib — no file I/O.** All parsing/normalization/ranking/projection is
  deterministic and I/O-free. File reads (the log, the library) and the `--write` mutation live in
  `entrypoints.py`, which already owns file side effects (mirrors `run_audit_cli`). Keeps `mine.py`
  trivially unit-testable with in-memory fixtures.
- **`mine.py` lives IN `fabric_audit_agent/query/`**, so its imports are same-package siblings:
  `from .firewall import validate_adhoc_kql, FirewallRejection` and `from . import kql_guard`
  (NOT `from .query.firewall …`).
- **The grounding bar is re-enforced by *calling the real* `validate_adhoc_kql`** — never a
  re-implementation (Part 4.12: one validator, no weaker copy).
- **Fail-closed on redaction via a single `"***" in text` check.** All three `redact_secrets` subs
  emit a `***` run (`redact.py:29-31`). But instead of dropping the whole group when its *modal* form
  is redacted, pick the **most-frequent non-`***` member** as the representative; only if *every*
  member contains `***` is the group dropped. (Disclosed false-drop class: a query legitimately
  containing `***` in a string literal, or `| extend sig=x` → `sig=***`, is dropped — acceptable,
  fail-closed.)
- **Trailing-bound strip is looped and covers `take` AND `limit`.** The allowed audit line is
  `f"{kql}\n| take {N}"` (`tools.py:1568`), but the agent's own query may already end in
  `| take M`/`| limit M`, yielding a *doubled* bound. Strip `\|\s*(take|limit)\s+\d+\s*$`
  **repeatedly** on the mined side *and* the library side so a mined query normalizes identically to
  the hand-authored template it matches — the precondition for idempotency.
- **`shape_key` numeric normalization is conservative to protect honesty.** Only two things are
  placeholdered: (1) numeric operands on the RHS of a comparison (`> >= < <= == !=`), so
  `where cu > 80` and `where cu > 90` share a shape; (2) the argument *inside* `ago(...)`/`datetime(...)`.
  **Never** touched: arithmetic operands (the CU-formula constants like `* 1000 * 30`), `bin()` args,
  and `take/limit/top N`. This keeps a query with a *wrong* formula constant a **distinct** shape (so
  it can't win "representative" for the correct shape — an honesty defect the reviewer flagged), and
  keeps `bin(win,1h)` ≠ `bin(win,1d)`.
- **`hitCount` is an extra entry key — verified safe:** `test_query_library.py:27` uses a superset
  check (`set(x) >= {…}`); `query_library_handler` (`tools.py:1597-1604`) keys off fixed fields.
- **`--write` reads and writes ONE resolved library path.** `_load_query_library()` hardcodes the
  package path and can't be redirected, so the CLI computes the library path itself (default =
  package-adjacent `query_library.json`; overridable for tests) and uses that SAME path for both the
  dedup-existing read and the append-write — otherwise a `base_dir` test would dedup against prod while
  writing a temp copy.
- **CLI parses its own args with `argparse`** inside `run_mine_queries_cli(rest, base_dir=None)`;
  `__main__.py` routes `mine-queries` → that function (same `rest`-passthrough as `import`/`inspect`).

## Confirmed interfaces (grounded in the code — for the plan-reviewers)

- `firewall.validate_adhoc_kql(kql)` → returns `kql` unchanged, or raises
  `FirewallRejection(reason, stage)` (both real attrs). Pure, offline. (`firewall.py:107-162`)
- `kql_guard._strip_string_literals(s)` blanks literal *content* to spaces, keeps quotes + length;
  `kql_guard.first_statement(s)` truncates at top-level `;`. Pure, importable. (`kql_guard.py:54-95`)
- `redact.redact_secrets` sentinels all contain `***`. (`redact.py:25-32`)
- `[adhoc-kql]` line: `print("[adhoc-kql] " + json.dumps(rec, ensure_ascii=False, separators=(",", ": ")))`.
  Parser must match on the **substring** `[adhoc-kql] ` (a logger prefix may precede it), then
  `json.loads` the remainder. **Allowed** rec keys: `tag, engine, verdict:"allowed", rowCount, kql`
  where `kql = f"{kql}\n| take {N}"` (bounded, redacted). **Rejected** rec: `tag, engine,
  verdict:"rejected", stage`, usually `reason`, and (correction from v1) **also `kql`** — harmless
  here because the parser keeps only `verdict=="allowed"`. `durationMs` never emitted on this path.
  (`tools.py:114-131, 1537-1576`)
- Library entry schema: `{name, category, engine, description, kql, groundedIn}` (that key order);
  `engine ∈ {"capacity","la"}`; names unique + lowercase + no-spaces; `description`/`groundedIn`
  non-empty. Loaded by `_load_query_library()` from package-adjacent `query_library.json`.
  (`tools.py:43-53`, `test_query_library.py`)
- **`query_library.json` on-disk format:** `json.dump(..., indent=2, ensure_ascii=False)` (default
  separators), trailing newline, CRLF line endings (Windows autocrlf — writing via normal text-mode
  `open(path,"w",encoding="utf-8")` reproduces it; do NOT force `newline=""`). `--write` must match to
  avoid whole-file diff churn.
- `entrypoints.py`: each `run_*_cli(...)` returns a text block; `base_dir` redirects paths for tests.
  `__main__.main(argv)` dispatches on `cmd`, passing `rest` through for `import`/`inspect`.

## Test baseline

Green suite after every task. This machine (mcp installed): **859 passed** (post-`9c36cc3`); author's
machine: `856 passed, 3 skipped`. `cd fabric-audit-agent-py && python -m pytest -q`. TDD each task.

---

## Task List

### Task 1 — `parse_audit_lines` + `shape_key` (pure foundation)

**Description:** Create `fabric_audit_agent/query/mine.py` with the log parser and the shape
canonicalizer.

**Interface:**
```python
def parse_audit_lines(lines) -> list[dict]:
    """For each line CONTAINING the substring '[adhoc-kql] ' (not necessarily at the start),
    json.loads the text after the marker; skip non-marker and malformed-JSON lines (never raise).
    Return only records with verdict == 'allowed' AND engine in {'capacity','la'} (drop unknown
    engines so a stray log value can't corrupt the library later)."""

def shape_key(kql: str) -> str:
    """Canonical grouping key. In order:
    (1) repeatedly strip a trailing '| take <int>' OR '| limit <int>' until none remain;
    (2) blank string-literal content via kql_guard._strip_string_literals;
    (3) placeholder ONLY: numeric RHS of a comparison (> >= < <= == !=), and the arg inside
        ago(...) / datetime(...);  NEVER arithmetic operands, bin() args, or take/limit/top N;
    (4) collapse whitespace; (5) lowercase KQL operator keywords.
    Deterministic, pure."""
```

**Acceptance criteria:**
- [ ] `parse_audit_lines`: extracts allowed records; skips rejected, non-marker, malformed-JSON without raising; drops `engine ∉ {capacity,la}`; extracts a line that has a logger prefix before `[adhoc-kql] `.
- [ ] `shape_key`: queries differing only by date/comparison-threshold/whitespace/operator-case → same key; `| take 100` vs `| take 500` → same; `| limit 100` vs `| take 100` → same; a query already ending `| take 50` then `| take 100` (doubled) normalizes to the same key as the single-take twin (looped strip); `ago(1d)` vs `ago(24h)` → same; `bin(win,1h)` vs `bin(win,1d)` → **different**; a query with both `ago(1d)` and `bin(win,1d)` normalizes the `ago` but preserves the `bin` timespan; two queries differing only in a **formula constant** (`*1000*30` vs `*1000*60`) → **different**; genuinely different queries → different.
- [ ] Pure, deterministic, no I/O.

**Verification:** `python -m pytest tests/test_mine.py -q` green; full suite green.
**Files:** `fabric_audit_agent/query/mine.py`, `tests/test_mine.py`. **Dependencies:** None. **Scope:** M.

---

### Task 2 — `rank_candidates` (group, dedup, fail-closed drops, representative)

**Description:** Add ranking to `mine.py`. No naming/description yet (that's Task 3).

**Interface:**
```python
def rank_candidates(records, existing_templates, *, min_count=3, top_n=10) -> list[dict]:
    """Group allowed records by (engine, shape_key). Drop shapes already covered by
    existing_templates (apply the SAME shape_key to each template's 'kql' — symmetric because both
    sides loop-strip trailing take/limit). Keep groups with count >= min_count.
    Representative = the most-frequent EXACT kql in the group (each candidate's kql first
    loop-stripped of trailing take/limit) whose text does NOT contain '***'; ties broken
    lexicographically. If EVERY member contains '***', drop the group. The representative is always a
    literal observed member (never synthesized from shape_key). Then drop the group if
    validate_adhoc_kql(rep) raises FirewallRejection. Sort survivors (count desc, shapeKey asc);
    return top_n dicts: {engine, shapeKey, kql, hitCount}."""
```

**Acceptance criteria:**
- [ ] Grouping by `(engine, shape_key)`; `min_count`/`top_n` honored; deterministic order.
- [ ] Dedup vs existing works, **including** a mined query equal to an existing template once trailing `take`/`limit` are looped-stripped (must be deduped).
- [ ] Fail-closed proven: (a) a group whose modal form contains `***` but has a clean lower-frequency member → the **clean** member is the representative; (b) a group where *all* members contain `***` → dropped; (c) a representative failing `validate_adhoc_kql` → group dropped.
- [ ] The returned `kql` is a **literal member** of the group's raw (post-strip) records — asserted in a test (spec's third fail-closed drop: never a placeholder/synthesized form).
- [ ] Every returned `kql` passes `validate_adhoc_kql` (assert in-test).

**Verification:** `python -m pytest tests/test_mine.py -q` green; full suite green.
**Files:** `fabric_audit_agent/query/mine.py`, `tests/test_mine.py`. **Dependencies:** Task 1. **Scope:** M.

---

### Task 3 — `to_library_entries` (name + description + projection)

**Description:** Add the projection from ranked groups to library-schema entries, with deterministic,
collision-safe naming. Isolated from Task 2 because this is where determinism/collision bugs hide.

**Interface:**
```python
def to_library_entries(ranked, existing_templates) -> list[dict]:
    """Project each ranked group {engine, shapeKey, kql, hitCount} to a library entry:
    {name, category, engine, description, kql, groundedIn, hitCount}  # existing key order + hitCount
      - category   = 'adhoc-mined'
      - groundedIn = 'mined from adhoc audit log'
      - description= f"Auto-mined {engine} query ({op}); seen {hitCount}x in the ad-hoc audit log."
                     (factual, non-empty; never a placeholder)
      - name       = f"adhoc-{engine}-{op}-{h}", lowercase/no-spaces (kebab). op = dominant KQL
                     operator by count, ties broken by first-appearing. h = sha1(shapeKey).hexdigest()[:6].
                     Name MUST be unique vs existing_templates AND within this batch; on collision,
                     lengthen h (7,8,… hex) until unique."""
```

**Acceptance criteria:**
- [ ] Entry key order matches existing entries (`name, category, engine, description, kql, groundedIn`) + trailing `hitCount`.
- [ ] `category=="adhoc-mined"`, `groundedIn` set, `description` non-empty and factual (no `TODO`/placeholder).
- [ ] Names are lowercase, space-free, unique vs existing library **and** within batch; a forced sha1-prefix collision (two shapes sharing engine+op+6-hex) still yields unique names.
- [ ] `op` (dominant operator) selection is deterministic (count, then first-appearing).
- [ ] Every produced entry still passes `validate_adhoc_kql` and would satisfy `test_query_library.py` (engine enum, non-empty fields).

**Verification:** `python -m pytest tests/test_mine.py -q` green; full suite green.
**Files:** `fabric_audit_agent/query/mine.py`, `tests/test_mine.py`. **Dependencies:** Task 2. **Scope:** S.

---

### Task 4 — `mine-queries` CLI (preview + `--write`)

**Description:** Add `run_mine_queries_cli(rest, base_dir=None)` to `entrypoints.py`; route `mine-queries`
in `__main__.py`. Preview by default; `--write` appends to the resolved `query_library.json`.

**Interface / behavior:**
```python
def run_mine_queries_cli(rest, base_dir=None) -> str:
    """argparse: positional logfile ('-' = stdin); --min-count (default 3); --top (default 10);
    --write. Resolve ONE library path (package-adjacent query_library.json; base_dir override).
    Read log text -> parse_audit_lines -> rank_candidates(existing=<read that same library path>)
    -> to_library_entries.
    Preview: return a ranked table (rank · hitCount · engine · name · one-line kql) + a
    ready-to-paste JSON snippet per candidate; write nothing. On zero candidates: a clean
    'no candidates' message.
    --write: if zero candidates, leave the file BYTE-IDENTICAL (do not re-dump). Else append entries,
    preserving all existing entries and their order, json.dump(indent=2, ensure_ascii=False) + trailing
    newline (text-mode open, preserve CRLF), and return a summary. Idempotent (re-run dedups vs the
    now-updated library). A missing logfile returns a clean error string, not a traceback."""
```
- `__main__.py`: `elif cmd == "mine-queries": print(ep.run_mine_queries_cli(rest))`; add the usage line to the module docstring.

**Acceptance criteria:**
- [ ] Preview writes **nothing**; prints candidates or a clean "no candidates" line.
- [ ] `--write` appends only firewall-passing candidates, preserves existing entries + order, output schema-valid; **zero candidates → file byte-identical**; **re-running `--write` is idempotent** (no dupes).
- [ ] After `--write`, `test_query_library.py`'s per-template firewall test passes on the mutated file (every entry, old + new).
- [ ] Dedup-existing and write target are the **same** resolved path (base_dir test proves temp library is both read and written).
- [ ] `-` reads stdin; missing logfile → clean error string.
- [ ] `python -m fabric_audit_agent mine-queries <file>` dispatches correctly.

**Verification:** `python -m pytest tests/test_mine.py tests/test_entrypoints.py tests/test_query_library.py -q` green; full suite green; manual `mine-queries <fixture-log>` + `--write` against a temp copy.
**Files:** `fabric_audit_agent/entrypoints.py`, `fabric_audit_agent/__main__.py`, tests. **Dependencies:** Task 3. **Scope:** M.

---

### Checkpoint: after Task 4 (feature complete)
- [ ] Full suite green (≥ baseline + new tests); no skips regressed.
- [ ] `python -c "from fabric_audit_agent.tools import create_tool_definitions as f; print(len(f()))"` → still **18** (adds NO MCP tool).
- [ ] `python -m fabric_audit_agent mine-queries -` (empty stdin) → clean "no candidates".
- [ ] Read-only holds: nothing touches Fabric/Databricks; `--write` mutates only `query_library.json`.
- [ ] Ready for opus final whole-branch review.

---

## Global constraints block (copy verbatim into every implementer + reviewer prompt)

- **Read-only absolute.** No Fabric/Databricks calls; no network; `--write` may edit ONLY the resolved `query_library.json`. Never runs in the App.
- **Never label mock/proxy as live; no dishonest provenance.** Mined entries MUST carry `category:"adhoc-mined"`, `groundedIn:"mined from adhoc audit log"`, and a real non-empty `description`. No placeholder text ever written.
- **Never loosen the grounding bar.** Re-validate every candidate by CALLING `validate_adhoc_kql` (not a copy); drop on `FirewallRejection`. Drop redaction-`***` forms (prefer a clean member; drop the group only if all are redacted). Offline re-check is the STATIC half of the bar — the same bar CI enforces per-template.
- **Imports:** `mine.py` is in `query/` → `from .firewall import validate_adhoc_kql, FirewallRejection`, `from . import kql_guard`. NOT `from .query.…`.
- **Conventions (Part 1e):** data keys camelCase, identifiers snake_case; nullish-not-falsy; numeric guard excludes bool + non-finite; stdlib-only (`json`/`re`/`hashlib`/`argparse`); Python ≥3.10.
- **Tests:** offline, deterministic, injected fixtures; keep the suite green (859 here / 856+3-skip author). TDD: failing test → implement → green → commit.
- **Do the work YOURSELF (Edit/Write/Bash); do NOT delegate to a nested agent.**
- **Commit trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (system-mandated; overrides the HANDOFF's Fable-5 trailer).

## Dependency graph

```
Task 1 (parse + shape) → Task 2 (rank) → Task 3 (name + project) → Task 4 (CLI + --write)
```
Strictly sequential; no parallelization.

## Edge-case test fixtures (must exist across the tasks)

- Agent query already ending `| take 50` (→ doubled take in log) → dedup/idempotency holds. (T1/T4)
- Agent query ending `| limit 100` → same shape as `| take 100` twin. (T1)
- Two queries differing only in a formula constant (`*1000*30` vs `*1000*60`) → **different** shape. (T1)
- `ago(1d)` vs `ago(24h)` → same; `bin(win,1h)` vs `bin(win,1d)` → different; query with both `ago(1d)` and `bin(win,1d)` → ago normalized, bin preserved. (T1)
- `top 50` vs `top 20` otherwise-identical → pinned behavior documented (top N not placeholdered → different shapes). (T1)
- Record with `engine ∉ {capacity,la}` → dropped, never written. (T1)
- Group whose modal exact-kql contains `***` but has a clean lower-freq member → clean member chosen; group where all members `***` → dropped. (T2)
- Legit query with literal `"***"` string / `| extend sig=value` → documented false-drop. (T2)
- `--write` with **zero** candidates → file byte-identical afterward. (T4)
- Two distinct shapes engineered to share engine+dominant-op+6-hex sha1 prefix → unique names survive. (T3)
- Log line with a leading logger prefix before `[adhoc-kql] ` → still extracted. (T1)

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Mined text is bounded+redacted, not original | High | Loop-strip trailing take/limit both sides; clean-member representative; `***` drop; tests |
| shape_key over-merges a wrong-constant query (honesty) | High | Numeric placeholder only on comparison RHS + ago/datetime args; never arithmetic/bin; explicit fixtures |
| Static-only re-check mistaken for full grounding | Med | Honestly scoped in spec + constraints; grounded by `verdict=="allowed"` at capture + live re-rehearsal on every `run_kql` |
| `--write` corrupts library format/order or dedups vs wrong file | Med | One resolved path for read+write; match indent/ensure_ascii/CRLF; byte-identical on zero; idempotency + per-template-firewall tests |
| Name collision vs existing library | Med | Uniqueness check vs existing+batch; lengthen hash on collision |
| Data-starved (empty log until 3-B) | Low | Preview/`--write` degrade to "no candidates"; byte-identical on zero |

## Open questions

- None blocking. (`min_count=3`, `top_n=10` approved in brainstorming; auto-write + human-merge gate approved as the Q3 reconciliation.)

## Change log (v1 → v2, from plan-reviewers)

- **Coverage:** added explicit AC/test that the representative is a literal group member (spec's 3rd fail-closed drop).
- **Technical-accuracy:** fixed import paths (`.firewall`/`. kql_guard`, not `.query.…`); corrected "rejected has no kql" (it does); fixed the `_load_query_library()` base_dir gap (CLI resolves one path for read+write); pinned on-disk format (indent=2, ensure_ascii=False, trailing newline, CRLF).
- **Improvability:** looped trailing-strip covering `take` AND `limit` (idempotency); conservative numeric normalization (comparison RHS + ago/datetime only) to prevent formula-constant over-merge; scoped `ago/datetime` replacement so `bin()` stays distinct; drop `engine ∉ {capacity,la}` at parse; clean-member representative instead of whole-group drop on `***`; name uniqueness vs existing library + deterministic dominant-op; byte-identical zero-candidate write + key-order alignment; **split Task 2 → Task 2 (rank) + Task 3 (name/project)**, CLI becomes Task 4.

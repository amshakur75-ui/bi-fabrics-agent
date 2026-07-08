# Query-Library Growth Loop — Design Spec

**Date:** 2026-07-08 · **Roadmap item:** Part 3-A (HANDOFF.md) · **Status:** design, pre-plan

## Purpose

Close the firewall's learning loop. `run_kql` emits a `[adhoc-kql]` audit line for every
agent-authored ad-hoc query (allowed or rejected). This feature mines those audit lines for the
**most-repeated *allowed* query shapes** and turns them into `query_library.json` templates, so the
grounded library **grows from real usage** instead of being hand-authored from guesses.

Ships as an **offline maintenance CLI** — `mine-queries` — with two modes: a safe **preview**
(default, prints ranked promotion candidates) and **`--write`** (appends firewall-passing candidates
directly into `query_library.json`, producing a reviewable git diff). It is data-starved until the
firewall is deployed and used (Part 3-B); this builds the tooling now so the first real pass is a
`--write` + PR the moment logs exist.

## The three invariants (this feature must not cross any)

1. **Read-only absolute.** The CLI reads a log file and (in `--write`) edits a *repo file on a dev
   machine*. It never touches any Fabric/Databricks resource, never runs in the write-free App.
2. **Never label mock/proxy as live.** Auto-added templates carry honest provenance
   (`category:"adhoc-mined"`, `groundedIn:"mined from adhoc audit log"`, `hitCount`) so a machine-grown
   template is always distinguishable from a human-curated one.
3. **Never loosen the grounding bar.** Every candidate is re-validated through
   `firewall.validate_adhoc_kql` before it can be proposed *or* written; a candidate that does not pass
   is dropped. The existing per-template firewall test on `query_library.json` remains the permanent
   CI enforcement.

## Autonomy boundary (decided)

`--write` edits the local library file; the change still lands via the **normal PR → CI → merge**
gate (human authorizes the merge to the PUBLIC repo). Full no-human autonomy (auto-commit +
auto-merge) is **out of scope**: the repo is public, the library is a grounded artifact, and the
production App is write-free, so library growth is inherently an offline, human-merged maintenance
action — never something the live agent does to itself.

## Data source

The `[adhoc-kql]` stdout audit line (`tools._adhoc_audit_log`), one JSON object per line:
`{"tag":"adhoc-kql","engine":...,"verdict":"allowed"|"rejected","stage":...,"reason":...,"kql":...,"rowCount":...,"durationMs":...}`.
Input is a **log file path** (or `-` for stdin) that a human has captured from the App's stdout.
Fetching prod logs over the network is a separate ops step, not part of this tool.

## Architecture

New pure/stdlib module **`fabric_audit_agent/query/mine.py`**:

- `parse_audit_lines(lines) -> list[dict]` — scan text for `[adhoc-kql] ` prefixed lines, parse the
  trailing JSON, tolerate non-matching / malformed lines (skip, never raise), return the records with
  `verdict == "allowed"` (rejected queries are never promotion material).
- `shape_key(kql) -> str` — canonicalize a query into a grouping key: blank string literals (reuse
  `firewall`/`kql_guard` literal-stripping so it matches the firewall's own view), replace numeric
  literals and time args (`ago(...)`, `datetime(...)`) with placeholders, collapse whitespace,
  lowercase KQL operators. Deterministic and pure.
- `rank_candidates(records, existing_templates, *, min_count=3, top_n=10) -> list[dict]` —
  group allowed records by `(engine, shape_key)`; **drop shapes already covered** by an existing
  library template (same normalization applied to `query_library.json` entries); keep groups with
  `count >= min_count`; sort by `(count desc, shape_key)` for deterministic order; for each group pick
  a **concrete representative** query — the **most frequent exact kql** within the shape group, ties
  broken lexicographically (deterministic; audit records carry no reliable timestamp, so "most
  recent" is unavailable — most-frequent-exact is the stable canonical instance). It is a real,
  runnable query, so what gets promoted is genuine, not a placeholder-ized form the engine couldn't run;
  **re-validate that representative through `firewall.validate_adhoc_kql`** and drop it if it fails;
  return the top `top_n` as candidate dicts:
  `{name, engine, category:"adhoc-mined", description, groundedIn, kql, hitCount}`.
  - `name` — deterministic, derived from engine + dominant operator(s) + a short shape hash (e.g.
    `adhoc-capacity-summarize-a1b2c3`); collision-safe; a human renames on review.
  - `description` — factual, generated from the shape (e.g. `"Auto-mined: capacity query summarizing
    over CapacityEvents; seen 5x"`), never a bare `TODO` (honesty: a grounded library ships no
    placeholder prose).

CLI **`mine-queries`** wired in `entrypoints.py` + `__main__.py` dispatch:
`python -m fabric_audit_agent mine-queries <logfile|-> [--min-count N] [--top N] [--write]`.
- **preview (default):** print a ranked table (rank · hitCount · engine · name · one-line kql) plus a
  ready-to-paste JSON snippet per candidate. Writes nothing.
- **`--write`:** append the candidates into `query_library.json` (schema-identical to existing
  entries), idempotently (dedup by shape-key vs current library so re-runs add nothing already
  present), deterministic ordering for clean diffs; print a summary of what was added. The human
  reviews the diff and merges the PR.

## Testing (TDD, offline, deterministic)

Synthetic `[adhoc-kql]` log fixtures — never a live endpoint:
- `parse_audit_lines`: extracts allowed records; skips rejected, non-`[adhoc-kql]`, and malformed-JSON
  lines without raising.
- `shape_key`: two queries differing only by date/threshold/whitespace/operator-case collapse to one
  key; genuinely different queries do not.
- `rank_candidates`: min-count and top-N honored; dedup vs existing library works; only
  firewall-passing representatives emitted (include a fixture whose representative would fail → dropped
  even though it was logged "allowed", proving the re-check); deterministic ordering.
- `--write`: appends valid entries, is idempotent on re-run, preserves existing entries, output stays
  schema-valid (each new entry still passes the existing per-template firewall test).
- CLI: preview writes nothing; `--write` mutates only the library file.

## Explicitly NOT pursued — with reasons

- **Runtime MCP tool** for "top repeated ad-hoc queries" — declined; the payoff is an offline PR
  workflow, and the App is write-free (can't self-grow).
- **Auto-commit + auto-merge** — declined; public repo + grounded artifact + read-only App ⇒ human
  merge gate stays.
- **Parameterized templates** (placeholders in the library) — the firewall forbids parameterization
  machinery and placeholders aren't runnable; we promote concrete representatives instead.
- **Network log fetch** — out of scope; the CLI takes a captured file. Pulling prod logs is ops.
- **LLM-based clustering/naming** — deterministic normalization + hashing is sufficient, reviewable,
  and test-stable; an LLM here would add nondeterminism to a grounded artifact.

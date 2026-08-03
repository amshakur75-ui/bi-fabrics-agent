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
   CI enforcement. **Honest scope of the offline re-check:** the shipped firewall is *static gate
   (`validate_adhoc_kql`) + take-0 engine rehearsal*. Offline mining can only re-run the **static
   half** — the engine binder is not available to the CLI. That is not a loosening: it is exactly the
   bar `query_library.json` is already held to in CI (the per-template test is static-only), so mined
   templates clear the same gate as hand-authored ones. The live-rehearsal grounding rests on the fact
   that each mined query was `verdict=="allowed"` at capture time (it bound and ran then), and on the
   agent re-rehearsing it live every time `run_kql` executes a template thereafter.

## Autonomy boundary (decided)

`--write` edits the local library file; the change still lands via the **normal PR → CI → merge**
gate (human authorizes the merge to the PUBLIC repo). Full no-human autonomy (auto-commit +
auto-merge) is **out of scope**: the repo is public, the library is a grounded artifact, and the
production App is write-free, so library growth is inherently an offline, human-merged maintenance
action — never something the live agent does to itself.

## Data source

The `[adhoc-kql]` stdout audit line (`tools._adhoc_audit_log`), one JSON object per line, prefix
exactly `[adhoc-kql] `. On an **allowed** line the keys are `tag`, `engine`, `verdict:"allowed"`,
`rowCount`, `kql` (camelCase). On a **rejected** line: `tag`, `engine`, `verdict:"rejected"`, `stage`,
`reason` (no `kql`). `durationMs` is defined by the emitter but **not passed** on the `run_kql` path,
so do not depend on it. Input is a **log file path** (or `-` for stdin) captured from the App's stdout.
Fetching prod logs over the network is a separate ops step, not part of this tool.

**Two facts about the logged `kql` that the mining logic must handle (verified against `tools.py`):**

1. **It is the *bounded* query, not the agent's original.** The allowed path logs
   `f"{kql}\n| take {maxRows}"` — the hard-cap `| take N` is already appended. Both `shape_key` and the
   chosen representative must **strip a trailing `| take <int>`** so (a) the mined shape matches an
   existing hand-authored template that has no such bound, and (b) a promoted template doesn't carry a
   redundant `| take` that would double up when `run_kql` re-bounds it.
2. **It is *redaction-processed* (`redact.redact_secrets`).** If a query contained a secret-shaped
   `key=`/`token=`/`sig=` fragment, that span is masked to a sentinel (`=***`, ` ***`, `:***@`). A
   masked query is not the query that ran and is often invalid KQL that would still slip past the
   *static* gate. **Fail-closed:** any candidate whose representative text contains a redaction sentinel
   is **dropped** (it did not run in that form — promoting it would violate both the honesty and
   grounding invariants).

## Architecture

New pure/stdlib module **`fabric_audit_agent/query/mine.py`**:

- `parse_audit_lines(lines) -> list[dict]` — scan text for `[adhoc-kql] ` prefixed lines, parse the
  trailing JSON, tolerate non-matching / malformed lines (skip, never raise), return the records with
  `verdict == "allowed"` (rejected queries are never promotion material).
- `shape_key(kql) -> str` — canonicalize a query into a grouping key. Steps, in order: (1) **strip a
  trailing `| take <int>`** (the logged bound — see Data source); (2) blank string-literal *contents*
  via `kql_guard._strip_string_literals` (note: it replaces content with spaces but **keeps the quotes
  and length**, matching the firewall's own view — good enough as a grouping key); (3) replace
  `ago(...)`/`datetime(...)` time-window arguments and bare numeric **threshold** literals with
  placeholders; (4) collapse whitespace; (5) lowercase KQL operators. **Deliberately NOT normalized:**
  timespan-granularity literals such as the `1h`/`1d`/`7d` inside `bin(win, 1h)` are left intact, so an
  hourly aggregation and a daily one stay **distinct shapes** (collapsing them would promote one
  misleading representative for two different queries — a false-merge). Deterministic and pure; covered
  by an explicit test fixture (`bin(...,1h)` vs `bin(...,1d)` → different keys).
- `rank_candidates(records, existing_templates, *, min_count=3, top_n=10) -> list[dict]` —
  group allowed records by `(engine, shape_key)`; **drop shapes already covered** by an existing
  library template — applying the **same `shape_key`** to `query_library.json` entries (which, because
  step 1 strips the trailing `| take`, now normalizes symmetrically so a mined query identical to an
  existing template is correctly deduped); keep groups with `count >= min_count`; sort by
  `(count desc, shape_key)` for deterministic order; for each group pick a **concrete representative** —
  the **most frequent exact kql** within the group (with its trailing `| take` stripped), ties broken
  lexicographically (deterministic; audit records carry no reliable timestamp, so "most recent" is
  unavailable). Then apply, in order, three **fail-closed drops**: (a) drop if the representative
  contains any redaction sentinel (`=***`/` ***`/`:***@`) — it never ran in that form; (b) re-validate
  through `firewall.validate_adhoc_kql` and drop on `FirewallRejection`; (c) it must be a real, runnable
  query — never a placeholder-ized form. Return the top `top_n` survivors as candidate dicts:
  `{name, engine, category:"adhoc-mined", description, groundedIn, kql, hitCount}` (`hitCount` is an
  extra key; verified it breaks neither the per-template firewall test nor `query_library_handler`,
  both of which key off `kql`/fixed fields).
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

Synthetic `[adhoc-kql]` log fixtures — never a live endpoint. Fixtures use the **real** logged shape
(bounded `| take N` appended; a redaction-sentinel case):
- `parse_audit_lines`: extracts allowed records; skips rejected, non-`[adhoc-kql]`, and malformed-JSON
  lines without raising.
- `shape_key`: two queries differing only by date/threshold/whitespace/operator-case collapse to one
  key; a trailing `| take 100` vs `| take 500` does not change the key (both stripped); genuinely
  different queries do not collapse; **`bin(win, 1h)` vs `bin(win, 1d)` produce different keys**
  (granularity not normalized).
- `rank_candidates`: min-count and top-N honored; dedup vs existing library works **including** a mined
  query that equals an existing template once the trailing `| take` is stripped (must be deduped);
  fail-closed drops proven — (a) a representative containing a redaction sentinel is dropped even though
  logged "allowed"; (b) a representative that fails `validate_adhoc_kql` is dropped; deterministic
  ordering.
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

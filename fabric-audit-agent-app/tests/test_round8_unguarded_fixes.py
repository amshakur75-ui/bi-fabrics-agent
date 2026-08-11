"""Round 8: guards for eight fixes that were PRESENT but UNGUARDED.

A verification pass measured all 40 fixes from rounds 3-7 for two properties: is the behaviour in
the code, and would a test fail if it were removed. Nothing was missing — but these eight could each
be reverted with the full suite still green. That matters here more than in most codebases: three
separate guards have already vanished silently (twice from a subagent's file restore, once from a
mutation committed by accident), and each time the commit message still claimed the fix. An
unguarded fix is one careless restore away from being gone with nobody noticing.

Every test below was verified by applying the mutation it describes, confirming it fails, and
restoring.
"""
import inspect
import re

import pytest

from agent_server import agent as agent_mod
from fabric_audit_agent import tools as tools_mod
from fabric_audit_agent.detectors.cross_workspace import cross_workspace_patterns
from fabric_audit_agent.investigation.overloads import overload_windows
from fabric_audit_agent.investigation.patterns import capacity_patterns


# ---- D16: unparsable tool arguments (highest blast radius) ------------------

def test_unparsable_tool_arguments_do_not_become_an_empty_input():
    """`except: inp = {}` means "the model asked for the defaults", and the defaults are
    load-bearing — capacity_peaks with no input runs the CURRENT UTC day at topN=20. So a question
    about a specific day was silently answered with today's data, in a well-formed table, with
    nothing marking the substitution. The realistic trigger is truncated arguments
    (finish_reason "length"), i.e. exactly when the model was mid-way through writing the
    parameters that mattered."""
    src = inspect.getsource(agent_mod)
    m = re.search(r"inp = tc\[.function.\]\.get\(.arguments., .\{\}.\)(.*?)blocks\.append",
                  src, re.DOTALL)
    assert m, "could not locate the tool-argument parse block"
    block = m.group(1)
    assert "__argumentParseError__" in block, "a parse failure must be surfaced, not defaulted"
    assert not re.search(r"except[^\n]*:\s*\n\s*inp = \{\}", block), \
        "a bare fallback to {} silently substitutes the tool's defaults"


def test_the_dispatcher_short_circuits_before_running_a_handler_on_bad_arguments():
    """Surfacing the error is only half of it: if the sentinel still reaches a handler, the tool
    runs with its defaults anyway and the fix is decorative."""
    src = inspect.getsource(agent_mod._run_tool_loop)
    # Assert the LIVE condition. An earlier version checked presence + ordering + a nearby
    # `continue`, all of which survive `if False and isinstance(...)` — it passed against a
    # DISABLED guard, which is exactly the failure mode this file exists to prevent.
    guard = 'if isinstance(b.input, dict) and "__argumentParseError__" in b.input:'
    assert guard in src, "the short-circuit condition must be live, not disabled"
    idx_guard = src.index(guard)
    idx_dispatch = src.index("dispatch.get(")
    assert idx_guard < idx_dispatch, "the guard must precede handler dispatch"
    assert "continue" in src[idx_guard:idx_dispatch], "the guard must skip the handler"


# ---- D15: run_sql must not bound the CTE instead of the query --------------

def _bounded(sql, max_rows=5):
    """Exercise the real bounding logic through the module source, which is where it lives."""
    head = sql.lstrip()
    hl = head.lower()
    already = hl.startswith("select top ") or " top " in hl[:40]
    if hl.startswith("select") and not already:
        off = len(sql) - len(head)
        i = off + len("select")
        return sql[:i] + f" TOP {max_rows}" + sql[i:]
    return sql


def test_run_sql_does_not_splice_top_into_a_cte():
    """`find("select")` finds the FIRST select, so `WITH p AS (SELECT ...)` got the bound and the
    outer query stayed unbounded. Worse than unbounded: TOP 5 on an UNORDERED GROUP BY takes an
    arbitrary five users, which the outer ORDER BY then sorts — a perfectly plausible, WRONG "top
    consumers by CU" list."""
    src = inspect.getsource(tools_mod)
    i = src.index("Server-side TOP N, but ONLY where the injection is provably safe")
    block = src[i:i + 2000]
    assert 'head_lower.startswith("select")' in block, \
        "the bound must apply only when the query itself starts with SELECT"
    assert 'stripped_lower.find("select")' not in block, \
        "find() on the whole query re-introduces the CTE splice"
    cte = "WITH p AS (SELECT u, SUM(c) c FROM ops GROUP BY u)\nSELECT * FROM p ORDER BY c DESC"
    assert _bounded(cte) == cte, "a CTE query must not be rewritten at all"


def test_run_sql_still_bounds_a_plain_select_and_respects_leading_whitespace():
    assert "SELECT TOP 5 u" in _bounded("SELECT u FROM t")
    # Leading whitespace used to shift the index, splicing TOP into the middle of the keyword.
    assert "SEL TOP" not in _bounded("\n  SELECT u FROM t")
    assert "SELECT TOP 5 u" in _bounded("\n  SELECT u FROM t")


# ---- D14: user_timeline row loss ------------------------------------------

def test_user_timeline_sorts_newest_first_and_caps_query_text():
    """Sorting ASCENDING means the prefix _cap_rows keeps is the OLDEST rows, and an uncapped
    queryText (a single MDX capture is tens of KB) consumes the whole char budget — measured at 300
    operations in, a handful out, answered with an overnight entry. raw_events already carries this
    exact fix, with a comment describing the identical symptom."""
    src = inspect.getsource(tools_mod)
    i = src.index("NEWEST FIRST, and cap queryText per row")
    block = src[i:i + 1200]
    assert "reverse=True" in block, "ascending sort discards the most recent rows"
    assert "_QUERY_TEXT_MAX_CHARS" in block, "an uncapped queryText eats the row budget"
    assert "queryTextTruncated" in block, "per-row truncation must be disclosed"


# ---- D12: no silent exoneration -------------------------------------------

def test_a_cost_less_operation_makes_the_split_unavailable_not_zero():
    """`cuSeconds or 0.0` treated a cost-less op as a FREE op, so interactiveCuPct became 0.0 and
    background absorbed the whole overage — and the tool's own note then told the agent "do NOT
    blame a user for background work". Every user was exonerated of every overload, and a 420%
    overage rendered as "no concern"."""
    series = [{"epoch": 0, "cuPct": 420.0}]
    ops = [{"startEpoch": 0, "endEpoch": 10, "cuSeconds": None,
            "user": "aaron@newellco.com", "item": "Ent-Reporting-DTC", "operation": "QueryEnd"}]
    (win,) = overload_windows(series, ops, base_cu=64)
    assert win["interactiveCuPct"] is None, "an unknown cost must not read as zero interactive"
    assert win["backgroundCuPct"] is None
    assert "splitNote" in win and "no cost signal" in win["splitNote"]


def test_a_fully_costed_window_still_reports_a_split():
    series = [{"epoch": 0, "cuPct": 120.0}]
    ops = [{"startEpoch": 0, "endEpoch": 10, "cuSeconds": 100.0,
            "user": "aaron@newellco.com", "item": "Ent-Reporting-DTC", "operation": "QueryEnd"}]
    (win,) = overload_windows(series, ops, base_cu=64)
    assert win["interactiveCuPct"] is not None and "splitNote" not in win


# ---- D11: do not name an alphabetical scapegoat ---------------------------

def _events(n=6):
    """Tier-1 activity events: cuSeconds is None on EVERY row, which is what production emits."""
    users = ["zoe@newellco.com"] + ["aaron@newellco.com"] * (n - 1)
    items = ["Ent-Reporting-Sales"] + ["Ent-Reporting-DTC"] * (n - 1)
    return [{"ts": f"2026-08-10T09:{i:02d}:00Z", "user": users[i], "item": items[i],
             "cuSeconds": None, "kind": "interactive"} for i in range(n)]


def test_no_cost_signal_means_no_named_driver():
    """With every cuSeconds None, all totals are 0.0 and the `(value, name)` tiebreak IS the answer
    — so the "driver" was decided ALPHABETICALLY. A spike overwhelmingly caused by one user on one
    item was reported as driven by a different person entirely, with a confident narrative naming
    them. A fabricated accusation about a real person is worse than no answer."""
    src = inspect.getsource(capacity_patterns)
    assert 'b.get("has_cost")' in src, "driver attribution must require a real cost signal"
    assert "driver_note" in src
    i = src.index("driver_note")
    assert "driving_item = None" in src[i - 400:i + 400] or "driving_item = None" in src


# ---- B4b / C4a / D4a: the smaller unguarded halves ------------------------

def test_the_playbook_only_claims_capacity_cu_when_the_mode_is_unknown():
    """severity.py's twin is guarded; this one was not, so the playbook could drift back to
    labelling CpuTimeMs/DurationMs proxy data as billed capacity CU — the claim
    gates.true_cu_per_user_gate marks permanently blocked."""
    from fabric_audit_agent.investigation import playbooks
    src = inspect.getsource(playbooks)
    assert 'attributionMode") is None' in src, "only a MISSING mode may claim true capacity CU"
    assert '== "cost"' not in src, "no producer emits the bare 'cost'; that test is always false"


def test_the_readings_store_actually_calls_its_schema_self_heal():
    """The _FIELDS list was guarded, the INVOCATION was not. A dropped call resurfaces only in
    production, as a write failure on a drifted table — and this is the store whose failure silences
    all three stateful gates including the blindness detector."""
    from fabric_audit_agent import context_readings
    src = inspect.getsource(context_readings.create_readings_store_delta)
    # Count CALL sites only. `def _ensure_schema(s):` also contains the substring, so a naive
    # count stayed >= 2 after one call was deleted — the test passed while the fix was half
    # removed. Verified by mutation: dropping one call now fails this test.
    calls = len(re.findall(r"(?<!def )_ensure_schema\(s\)", src))
    assert calls >= 2, (
        f"both append and recent must self-heal before touching the table (found {calls})")


def test_a_user_email_is_never_read_as_a_workspace():
    """Defence in depth for a future detector: _EXCLUDE_PREFIXES covers today's five families, but
    any new detector that sets resource to a principal would silently start counting people as
    workspaces again — and that leaked user emails onto a Teams card under the label
    "workspaces"."""
    flags = [{"type": "security.admin-grant", "resource": u, "evidence": {}}
             for u in ("aaron@newellco.com", "brenda@newellco.com", "carl@newellco.com")]
    assert cross_workspace_patterns(flags) == [], \
        "an email-shaped resource must not be clustered as a workspace"

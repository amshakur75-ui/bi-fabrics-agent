"""Capacity-events collector against the shape the DEPLOYED KQL actually returns.

Two separate P0s reached production because every fixture in
``test_capacity_events_collector.py`` feeds a shape the deployed query cannot emit (ints, a
nested ``data`` envelope, ``*ThresholdPercentage`` columns, a single capacityId). A
mutation-testing audit then found four guards in ``collector_capacity_events`` that can be
deleted with the whole suite still green, for exactly that reason. Every fixture here is the
flat 7-column ``todouble(...)`` projection from ``databricks.yml``, and every assertion pins one
of those guards.
"""
import ast
import re
from pathlib import Path

import pytest

from fabric_audit_agent.adapters import collector_capacity_events as cce
from fabric_audit_agent.adapters.collector_capacity_events import (
    create_capacity_events_collector,
)

_APP_ROOT = Path(__file__).resolve().parents[1]
_COLLECTOR_SRC = Path(cce.__file__)

# F64 -> baseCapacityUnits 64 CU/sec -> 30s budget = 64 * 1000 * 30 = 1,920,000 CU-ms.
_BUDGET_64 = 64 * 1000 * 30


def _deployed_kql():
    """The single deployed ``FABRIC_CAPACITY_EVENTS_KQL``.

    Raises rather than skips on anything unexpected: a contract test that quietly skips is how
    both prior P0s survived review.
    """
    yml = _APP_ROOT / "databricks.yml"
    if not yml.is_file():
        raise AssertionError(f"databricks.yml not found at {yml} — cannot verify the deployed query")
    blocks = re.findall(r'FABRIC_CAPACITY_EVENTS_KQL:\s*"([^"]*)"', yml.read_text(encoding="utf-8"))
    if not blocks:
        raise AssertionError(f"no FABRIC_CAPACITY_EVENTS_KQL found in {yml}")
    assert len(set(blocks)) == 1, (
        f"the {len(blocks)} FABRIC_CAPACITY_EVENTS_KQL blocks in databricks.yml have diverged; "
        "every job must query the same projection or the collector's contract differs per job"
    )
    return blocks[0]


def _stage(kql, verb):
    for s in (p.strip() for p in kql.split("|")):
        if s.startswith(verb + " "):
            return s[len(verb) + 1:]
    raise AssertionError(f"deployed KQL has no `{verb}` stage: {kql}")


def _split_top_level(text):
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _projected_columns(kql):
    return _split_top_level(_stage(kql, "project"))


def _extend_aliases(kql):
    return [p.split("=", 1)[0].strip() for p in _split_top_level(_stage(kql, "extend"))]


def _read_field_groups():
    """Every ``_row(row, *names)`` alternatives-group the collector reads, derived from the AST.

    Loop-driven groups (the threshold / overage ``for (camel, pascal, dst) in (...)`` tables)
    are unrolled one group PER table entry, so a single dropped overage column cannot hide
    behind its two siblings still resolving.
    """
    tree = ast.parse(_COLLECTOR_SRC.read_text(encoding="utf-8"))
    groups = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scopes = [{}]

        def visit_For(self, node):
            names = [e.id for e in node.target.elts if isinstance(e, ast.Name)] \
                if isinstance(node.target, ast.Tuple) else []
            elts = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List)) else []
            unrolled = False
            for elt in elts:
                if not isinstance(elt, (ast.Tuple, ast.List)):
                    continue
                vals = [e.value for e in elt.elts if isinstance(e, ast.Constant)]
                if names and len(vals) == len(elt.elts) == len(names):
                    unrolled = True
                    self.scopes.append(dict(zip(names, vals)))
                    for stmt in node.body:
                        self.visit(stmt)
                    self.scopes.pop()
            if not unrolled:
                self.generic_visit(node)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "_row":
                group = []
                for arg in node.args[1:]:
                    if isinstance(arg, ast.Constant):
                        group.append(arg.value)
                    elif isinstance(arg, ast.Name):
                        for scope in reversed(self.scopes):
                            if arg.id in scope:
                                group.append(scope[arg.id])
                                break
                if group:
                    groups.append(tuple(group))
            self.generic_visit(node)

    _Visitor().visit(tree)
    assert groups, (
        f"derived ZERO _row() field groups from {_COLLECTOR_SRC.name} — the AST walker no longer "
        "matches the code, so this contract test would pass vacuously. Fix the walker."
    )
    return sorted(set(groups))


def _deployed_row(cap="cap-prod", ts="2026-08-09T12:00:00.0000000Z", used=0.0, base=64.0,
                  add=None, burndown=None, total=None):
    """One row exactly as the deployed projection emits it: flat (``extend`` + ``project`` has
    already collapsed the ``data`` envelope) and every numeric a float (``todouble(...)``).
    The overage columns are always projected, so they are always present on the wire."""
    return {
        "capacityId": cap,
        "windowStartTime": ts,
        "capacityUnitMs": float(used),
        "baseCapacityUnits": float(base),
        "overageAddCapacityUnitMs": 0.0 if add is None else float(add),
        "overageBurndownCapacityUnitMs": 0.0 if burndown is None else float(burndown),
        "overageTotalCapacityUnitMs": 0.0 if total is None else float(total),
    }


def _reduce(rows):
    return create_capacity_events_collector(lambda kql: rows)["collect"]()


# ---------------------------------------------------------------------------
# A — the contract: deployed projection vs the columns the parser reads
# ---------------------------------------------------------------------------

def test_deployed_projection_satisfies_every_field_the_parser_reads():
    """Highest-value test in the file: it catches BOTH prior P0s.

    P0 #1 dropped the three overage columns from ``project`` — their read groups would go
    unsatisfied here. Anything the parser needs but the query stops projecting fails this test
    instead of silently reading None in production.

    The ``*ThresholdPercentage`` groups are the ONE documented gap: this tenant's stream never
    carried them and the deployed query never projected them, which is why
    ``_check_throttle_imminent`` was retired. Any OTHER unsatisfied group is a bug, so the
    exemption is expressed as a rule rather than a list of blessed field names.
    """
    projected = set(_projected_columns(_deployed_kql()))
    unsatisfied = [g for g in _read_field_groups() if not (set(g) & projected)]
    unexpected = [g for g in unsatisfied
                  if not all(n.endswith("ThresholdPercentage") for n in g)]
    assert not unexpected, (
        "the deployed KQL projects no column for these reads, so the collector will silently "
        f"see None in production: {unexpected}; projected={sorted(projected)}"
    )


def test_every_deployed_column_is_actually_read_by_the_parser():
    """The other direction: a projected column no read group claims is either a typo or dead
    weight, and a typo'd alias in ``project`` is what killed the collector outright in P0 #2.
    """
    projected = set(_projected_columns(_deployed_kql()))
    read = {n for g in _read_field_groups() for n in g}
    assert projected - read == set(), (
        f"databricks.yml projects columns the collector never reads: {sorted(projected - read)}"
    )


def test_every_projected_alias_is_defined_by_the_extend_clause():
    """P0 #2: a column was added to ``project`` without a matching ``extend`` alias, and Kusto
    failed the whole query with "Failed to resolve scalar expression" — the collector returned
    nothing on every sweep.
    """
    kql = _deployed_kql()
    aliases = set(_extend_aliases(kql))
    missing = [c for c in _projected_columns(kql) if c not in aliases]
    assert not missing, (
        f"projected without an `extend` alias: {missing} — Kusto rejects the entire query "
        f"('Failed to resolve scalar expression'). extend defines {sorted(aliases)}"
    )


# ---------------------------------------------------------------------------
# B — round-trip on the real wire shape
# ---------------------------------------------------------------------------

def test_reduced_dict_from_exact_deployed_projection():
    rows = [
        _deployed_row(ts="t1", used=1_152_000.0, total=1_920_000.0),   # 60%,  cum 100% -> 0.5 min
        _deployed_row(ts="t2", used=2_016_000.0, total=3_840_000.0),   # 105%, cum 200% -> 1.0 min
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["capacityId"] == "cap-prod"
    assert cap["peakCuPct"] == 105.0
    assert cap["peakAt"] == "t2"
    assert cap["throttleMinutes"] == 0.5           # one window >= 100% * 30s
    assert cap["overageTotalMs"] == 3_840_000.0
    assert cap["overageCumulativePct"] == 200.0
    assert cap["minutesToBurndown"] == 0.5         # worst (smallest) across windows


def test_deployed_projection_cannot_yield_threshold_maxima():
    """Pins the fixture-realism gap. ``_windows`` will populate these from a
    ``*ThresholdPercentage`` column, but the deployed query projects no such column, so any
    consumer treating them as always-present is reasoning about a shape production never emits.
    """
    cap = _reduce([_deployed_row(ts="t1", used=1_920_000.0)])["capacity"]
    for absent in ("maxInteractiveDelayPct", "maxInteractiveRejectionPct",
                   "maxBackgroundRejectionPct"):
        assert absent not in cap


# ---------------------------------------------------------------------------
# C — multi-capacity safety
# ---------------------------------------------------------------------------

def test_peak_and_throttle_are_scoped_to_the_capacity_owning_the_peak():
    """A trial capacity sharing the Eventstream spiked to 4000% and was reported against the
    healthy prod capacity, with a throttleMinutes larger than the elapsed window. No other test
    puts two capacityIds in one row set, so dropping the ``own`` filter goes unnoticed.
    """
    rows = [
        _deployed_row(cap="cap-prod", ts="t1", used=1_152_000.0),                # 60%
        _deployed_row(cap="cap-prod", ts="t2", used=2_016_000.0),                # 105%
        _deployed_row(cap="cap-prod", ts="t3", used=2_016_000.0),                # 105%
        _deployed_row(cap="cap-trial", ts="t1", used=2_400_000.0, base=2.0),     # 4000%
        _deployed_row(cap="cap-trial", ts="t2", used=2_400_000.0, base=2.0),     # 4000%
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["capacityId"] == "cap-trial"
    assert cap["peakCuPct"] == 4000.0
    assert cap["throttleMinutes"] == 1.0       # 2 trial windows only; 4 would include prod's
    assert cap["minutesOverBudget"] == 1.0


def test_overage_is_scoped_to_the_capacity_owning_the_peak():
    """Same guard, overage axis: the prod capacity's much larger accumulated overage must not
    be attributed to the trial capacity that owns the peak.
    """
    rows = [
        _deployed_row(cap="cap-prod", ts="t1", used=2_016_000.0, total=19_200_000.0),
        _deployed_row(cap="cap-trial", ts="t1", used=2_400_000.0, base=2.0, total=120_000.0),
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["capacityId"] == "cap-trial"
    assert cap["overageTotalMs"] == 120_000.0
    assert cap["overageCumulativePct"] == 200.0     # 120_000 / (2*1000*30) * 100


# ---------------------------------------------------------------------------
# D — the overage hoist onto the REDUCED dict
# ---------------------------------------------------------------------------

def test_overage_is_hoisted_onto_the_reduced_dict():
    """Un-hoisted, ``_check_overage`` read ``cap.get("overageTotalMs")`` and got None on every
    sweep — the detector was dead and tier2_capacity_reporting's overage columns were
    permanently NULL. Every other overage test goes through ``capacity_series``, a different
    code path, so it cannot see this regression.
    """
    rows = [
        _deployed_row(ts="t1", used=1_920_000.0, add=480_000.0, burndown=-240_000.0,
                      total=960_000.0),                                   # cum 50%  -> 0.25 min
        _deployed_row(ts="t2", used=2_016_000.0, add=960_000.0, burndown=-120_000.0,
                      total=2_880_000.0),                                 # cum 150% -> 0.75 min
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["overageTotalMs"] == 2_880_000.0     # largest accumulated overage
    assert cap["overageCumulativePct"] == 150.0
    assert cap["minutesToBurndown"] == 0.25


def test_zero_overage_still_hoists_because_the_column_is_always_projected():
    """The deployed projection always emits the overage columns, so a quiet capacity reports
    0.0 rather than the absent-key state that used to mean "detector has nothing to compare".
    """
    cap = _reduce([_deployed_row(ts="t1", used=1_152_000.0, total=0.0)])["capacity"]
    assert cap["overageTotalMs"] == 0.0
    assert cap["overageCumulativePct"] == 0.0
    assert cap["minutesToBurndown"] == 0.0


# ---------------------------------------------------------------------------
# E — minutesToBurndown is the WORST case, not the rosiest
# ---------------------------------------------------------------------------

def test_minutes_to_burndown_is_the_most_urgent_window():
    """Smaller = more urgent, so the escalation axis must take the minimum. Taking the maximum
    reports the calmest window and under-escalates a capacity that is minutes from burndown.
    Note the two axes disagree on purpose: the max-overage window is the LEAST urgent one here.
    """
    rows = [
        _deployed_row(ts="t1", used=2_016_000.0, total=7_680_000.0),   # cum 400% -> 2.0 min
        _deployed_row(ts="t2", used=2_016_000.0, total=1_920_000.0),   # cum 100% -> 0.5 min
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["overageTotalMs"] == 7_680_000.0
    assert cap["minutesToBurndown"] == 0.5


# ---------------------------------------------------------------------------
# F — budget guard and the over-window boundary
# ---------------------------------------------------------------------------

def test_zero_base_capacity_units_yields_nothing():
    """A zero budget is a divide-by-zero, not a 0% window; the row is unusable and the
    collector must contribute nothing rather than fabricate a percentage."""
    assert _reduce([_deployed_row(ts="t1", used=1_152_000.0, base=0.0)]) == {}


def test_negative_base_capacity_units_yields_nothing():
    """A negative budget would flip the sign of CU% and report a busy capacity as idle."""
    assert _reduce([_deployed_row(ts="t1", used=1_152_000.0, base=-64.0)]) == {}


def test_unusable_budget_rows_are_dropped_without_losing_the_usable_ones():
    rows = [
        _deployed_row(ts="t1", used=1_152_000.0, base=0.0),
        _deployed_row(ts="t2", used=1_152_000.0, base=-64.0),
        _deployed_row(ts="t3", used=2_016_000.0),      # 105%
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["peakCuPct"] == 105.0
    assert cap["peakAt"] == "t3"
    assert cap["throttleMinutes"] == 0.5


def test_window_at_exactly_one_hundred_percent_counts_as_over_budget():
    """100.0% is at the budget, i.e. over-budget for smoothing purposes; an exclusive boundary
    silently reports 0 minutes for a capacity pinned exactly at its limit."""
    rows = [
        _deployed_row(ts="t1", used=float(_BUDGET_64)),          # exactly 100.0%
        _deployed_row(ts="t2", used=_BUDGET_64 * 0.999),         # 99.9%
    ]
    cap = _reduce(rows)["capacity"]
    assert cap["peakCuPct"] == 100.0
    assert cap["throttleMinutes"] == 0.5
    assert cap["minutesOverBudget"] == 0.5

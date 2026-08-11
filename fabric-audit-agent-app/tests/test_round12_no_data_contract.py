"""Round 12: an empty result must never read as a clean negative.

This defect has now shipped THREE times in three different consumers — decompose_throttle
(round 9), investigation/diagnose.py (round 11), capacity_overloads (round 12) — each fixed in
isolation, each time the next consumer repeating it. `mark_no_data` is the shared contract so the
fourth consumer inherits the behaviour instead of re-deriving it.
"""
import inspect

import pytest

from fabric_audit_agent.tools import mark_no_data


def test_an_unconfigured_source_is_not_a_negative_finding():
    out = mark_no_data({"overloads": []}, what="capacity overload windows",
                       window_label="2026-08-10", source_configured=False)
    assert out["noData"] is True
    assert "NOT CONFIGURED" in out["noDataMessage"]
    assert "not evidence that nothing happened" in out["noDataMessage"].lower()


def test_a_configured_but_empty_source_says_so_without_claiming_absence():
    out = mark_no_data({"overloads": []}, what="capacity overload windows",
                       window_label="2026-08-10", source_configured=True)
    assert out["noData"] is True
    assert "ZERO" in out["noDataMessage"]
    assert "not proof the condition was absent" in out["noDataMessage"]


def test_a_truncated_result_is_flagged_as_partial():
    out = mark_no_data({"rows": []}, what="operations", truncated=True)
    assert "TRUNCATED" in out["noDataMessage"]


def test_the_payload_is_not_mutated_in_place():
    original = {"overloads": []}
    mark_no_data(original, what="x")
    assert "noData" not in original, "callers may reuse the dict they passed"


# ---- the consumer that motivated it ---------------------------------------

def _overloads_handler_source():
    """The handler's own source, located by NAME.

    Both tests below used to anchor on a comment string plus a fixed character window
    (`src[i:i+1200]`), so lengthening a comment broke them while the behaviour was intact -- and,
    worse, a real change just outside the window would have gone unnoticed. Anchor on the function.
    """
    from fabric_audit_agent import tools

    src = inspect.getsource(tools)
    i = src.index("def capacity_overloads_handler(")
    j = src.index("\n    def ", i + 1)          # the next sibling nested def
    return src[i:j]


def test_an_unconfigured_capacity_stream_reports_not_configured():
    """BEHAVIOURAL. With no telemetry env, `_capacity_series_only` returns an empty series, and the
    tool must say the source is unavailable rather than report a clean estate. This is the case the
    round-12 defect actually shipped."""
    from fabric_audit_agent import tools

    # create_tool_definitions takes no collector port -- the tools build their own from env via
    # `_build_collector`, so that is what has to be replaced.
    facts = {"capacity": {"peakCuPct": None, "sku": "F64"}, "items": []}
    _orig = tools._build_collector
    tools._build_collector = lambda env, window=None: {"collect": lambda: facts}
    try:
        defs = {t["name"]: t for t in tools.create_tool_definitions()}
        out = defs["capacity_overloads"]["handler"]({"start": "2026-08-11T00:00:00Z",
                                                    "end": "2026-08-11T23:59:00Z"})
    finally:
        tools._build_collector = _orig
    assert out["noData"] is True
    assert "NOT CONFIGURED" in out["noDataMessage"]
    assert "measuredNegative" not in out, (
        "an unconfigured stream must never be presented as a measured negative")


def test_the_three_empty_cases_are_still_distinguished_in_the_handler():
    """The empty-overloads case has to split three ways: no stream at all (absence of evidence), a
    stream that never crossed (a MEASURED negative -- refusing to confirm a true negative is the
    mirror image of asserting a false one), and a truncated contributor pull (the split is a floor,
    so a background-dominated window is not proof no user was involved).

    Asserted on the live conditions, not on comment prose."""
    block = _overloads_handler_source()
    assert "mark_no_data(" in block, "the no-stream case must route through the shared contract"
    assert "not windows and not series" in block,         "no stream at all must be distinguishable from a quiet capacity"
    assert "measuredNegative" in block,         "a stream that never crossed the threshold is a finding, not a data gap"
    assert 'meta.get("truncated")' in block,         "a truncated contributor pull must not exonerate users"

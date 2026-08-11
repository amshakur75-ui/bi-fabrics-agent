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

def test_capacity_overloads_marks_no_data_rather_than_reporting_a_clean_negative():
    """`overloads: []` meant three different things and distinguished none of them: the capacity
    genuinely never crossed the threshold, the CU stream is not configured (_capacity_series_only
    returns ([], meta) with seriesError=None), or the window was empty. Only the first is a finding.
    The prompt's ZERO-ROWS rule keys off `noData`, which this tool never set."""
    from fabric_audit_agent import tools

    src = inspect.getsource(tools)
    i = src.index("An empty `overloads` list meant three different things")
    block = src[i:i + 1200]
    assert "mark_no_data(" in block, "the empty case must route through the shared contract"
    assert "source_configured=bool(series)" in block, \
        "an unconfigured stream must be distinguishable from a quiet capacity"


def test_a_truncated_contributor_pull_does_not_exonerate_users():
    """The interactive/background split is computed from a 5000-event cost-ordered cut, so dropped
    user operations land in the background residual — while the tool's own note tells the reader
    background is system work and not to blame a user."""
    from fabric_audit_agent import tools

    src = inspect.getsource(tools)
    i = src.index("An empty `overloads` list meant three different things")
    block = src[i:i + 1600]
    assert 'meta.get("truncated")' in block
    assert "splitTruncated" in block

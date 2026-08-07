"""Tests for automation.health — the permanent unhealthy-state visibility surface (Sub-plan 4
Part 4). Covers the pure HealthReport accumulator, the daily-digest banner wiring, and the
startup MODEL_MAP invariant recording an issue instead of raising.
"""
from fabric_audit_agent.automation.health import HealthReport, render_health_line
from fabric_audit_agent.automation.daily_summary import build_daily_summary
from fabric_audit_agent.resolve.routing_table import ROUTING_TABLE
from fabric_audit_agent.resolve.catalog import default_catalog
from fabric_audit_agent.job import _check_startup_invariant


# ---- HealthReport: pure accumulator ----

def test_all_ok_is_not_degraded_and_renders_no_line():
    h = HealthReport()
    h.record_collector("primary", True)
    h.record_detector("absolute_cost", True)
    h.record_delivery("chat", True)
    assert h.degraded is False
    assert h.summary == ""
    assert render_health_line(h) is None


def test_no_report_renders_no_line():
    assert render_health_line(None) is None


def test_collector_failure_marks_degraded_and_summary_mentions_it():
    h = HealthReport()
    h.record_collector("logAnalytics", False, "auth error (401)")
    assert h.degraded is True
    assert "auth error (401)" in h.summary
    line = render_health_line(h)
    assert line is not None
    assert line.startswith("⚠ Degraded:")
    assert "auth error (401)" in line


def test_record_collector_failures_from_merged_sources_list():
    """collector_merge.py's merged["sourcesFailed"] is a plain list of error strings — feed it
    straight in without reclassifying."""
    h = HealthReport()
    h.record_collector_failures(["Log Analytics unreachable", "List Usages 403"])
    assert h.degraded is True
    assert len(h.collectors) == 2
    assert all(not c["ok"] for c in h.collectors)
    assert "Log Analytics unreachable" in h.summary
    assert "List Usages 403" in h.summary


def test_detector_error_marks_degraded():
    h = HealthReport()
    h.record_detector("query_shape", False, "KeyError: 'queryText'")
    assert h.degraded is True
    assert "query_shape" in h.summary


def test_delivery_failure_marks_degraded_and_counts_by_channel():
    h = HealthReport()
    h.record_delivery("chat", False, "TimeoutError")
    h.record_delivery("chat", False, "TimeoutError")
    h.record_delivery("ticket", True)
    assert h.degraded is True
    assert "2 chat write(s) failed" in h.summary


def test_record_issue_marks_degraded_standalone():
    h = HealthReport()
    h.record_issue("readings store unavailable: ConnectionError: refused")
    assert h.degraded is True
    assert "readings store unavailable" in h.summary


def test_to_dict_is_a_plain_serializable_snapshot():
    h = HealthReport()
    h.record_collector("primary", False, "boom")
    d = h.to_dict()
    assert d["degraded"] is True
    assert "boom" in d["summary"]
    assert d["collectors"][0]["reason"] == "boom"
    assert d["issues"] == []


# ---- Daily digest banner ----

def test_digest_shows_banner_when_degraded():
    h = HealthReport()
    h.record_collector("logAnalytics", False, "auth error")
    md, card, _ = build_daily_summary(
        open_tickets=[], capacity={}, coverage_gaps=[], date_str="2026-08-07",
        app_url="https://app", health=h)
    assert "Degraded" in md
    assert "auth error" in md
    import json
    blob = json.dumps(card)
    assert "Degraded" in blob
    assert "auth error" in blob


def test_digest_omits_banner_when_healthy():
    md, card, _ = build_daily_summary(
        open_tickets=[], capacity={}, coverage_gaps=[], date_str="2026-08-07",
        app_url="https://app", health=HealthReport())
    assert "Degraded" not in md
    import json
    assert "Degraded" not in json.dumps(card)


def test_digest_omits_banner_when_health_not_passed():
    """Backward-compatible: callers that don't pass health at all (the pre-existing shape) see no
    banner and no behaviour change."""
    md, card, _ = build_daily_summary(
        open_tickets=[], capacity={}, coverage_gaps=[], date_str="2026-08-07", app_url="https://app")
    assert "Degraded" not in md


# ---- Startup invariant: recorded, never raised ----

def test_startup_invariant_passes_silently_on_real_catalog():
    h = HealthReport()
    names = {e["canonicalName"] for e in ROUTING_TABLE}
    _check_startup_invariant(h, catalog=default_catalog(), known_names=names)
    assert h.degraded is False
    assert h.issues == []


def test_startup_invariant_records_issue_instead_of_raising_on_drift():
    h = HealthReport()
    incomplete = {e["canonicalName"] for e in ROUTING_TABLE} - {"Ent-Reporting-Sales"}
    # Must not raise — that's the whole point (today it's only asserted in tests via pytest.raises).
    _check_startup_invariant(h, catalog=default_catalog(), known_names=incomplete)
    assert h.degraded is True
    assert any("MODEL_MAP" in issue for issue in h.issues)


def test_startup_invariant_works_without_a_health_report():
    """Callers that don't pass a health object (e.g. a bare invocation) never crash either — the
    failure just isn't recorded anywhere beyond the WARN print."""
    incomplete = {e["canonicalName"] for e in ROUTING_TABLE} - {"Ent-Reporting-Sales"}
    _check_startup_invariant(None, catalog=default_catalog(), known_names=incomplete)  # no raise

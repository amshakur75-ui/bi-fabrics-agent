"""TASK 1-WIRE: raw per-operation events wired onto facts["events"] in the collection path, so the
activity detectors (detectors/absolute_cost.py, detectors/query_shape.py) actually run in the
sweep. Offline — monkeypatches `adapters.clients.build_log_analytics_query` (the SP token/HTTP
builder) so `job._build_events_collector` runs against a fake `query(kql) -> list[dict]`, exactly
as `create_event_collector` is exercised in tests/test_collector_events_la.py.
"""
import fabric_audit_agent.adapters.clients as clients

from fabric_audit_agent.detectors import detect_all
from fabric_audit_agent.job import build_collector_from_env, _build_events_collector

_ENV = {
    "FABRIC_LA_WORKSPACE_ID": "ws", "FABRIC_TENANT_ID": "t",
    "FABRIC_CLIENT_ID": "cid", "FABRIC_CLIENT_SECRET": "s",
}

# One slow/costly op (>= slowOperationSeconds=300 default) from "alice", plus a query SHAPE that
# recurs 3x (>= recurringShapeMinCount) across 2 distinct users (>= recurringShapeMinUsers).
_ROWS = [
    {"TimeGenerated": "2026-08-01T00:00:00Z", "ExecutingUser": "alice@co", "ArtifactName": "Sales",
     "PowerBIWorkspaceName": "Fin", "OperationName": "QueryEnd", "CpuTimeMs": 700000,
     "DurationMs": 700000, "EventText": "EVALUATE FILTER(Sales, Sales[Amount] > 100)"},
    {"TimeGenerated": "2026-08-01T00:01:00Z", "ExecutingUser": "bob@co", "ArtifactName": "Sales",
     "PowerBIWorkspaceName": "Fin", "OperationName": "QueryEnd", "CpuTimeMs": 500,
     "DurationMs": 500, "EventText": "EVALUATE FILTER(Sales, Sales[Amount] > 200)"},
    {"TimeGenerated": "2026-08-01T00:02:00Z", "ExecutingUser": "carol@co", "ArtifactName": "Sales",
     "PowerBIWorkspaceName": "Fin", "OperationName": "QueryEnd", "CpuTimeMs": 500,
     "DurationMs": 500, "EventText": "EVALUATE FILTER(Sales, Sales[Amount] > 300)"},
]


def _fake_build_log_analytics_query(monkeypatch, rows=None, raise_exc=None):
    def fake(workspace_id, tenant_id, client_id, client_secret, session=None, **kw):
        def query(kql):
            if raise_exc:
                raise raise_exc
            return rows if rows is not None else []
        return query
    monkeypatch.setattr(clients, "build_log_analytics_query", fake)


def test_build_events_collector_attaches_events(monkeypatch):
    _fake_build_log_analytics_query(monkeypatch, rows=_ROWS)
    facts = _build_events_collector(_ENV)["collect"]()
    assert len(facts["events"]) == 3
    assert facts["events"][0]["user"] == "alice@co"


def test_build_events_collector_raises_so_the_merge_can_see_the_failure(monkeypatch):
    """A query failure must NOT read as a quiet estate.

    The old contract returned {"events": []} and never raised. collector_merge drops falsy lists
    (`if rows:`), so the key vanished entirely and absolute_cost / query_shape /
    query_antipatterns / xmla_errors / user_baseline all read `facts.get("events") or []` -> [].
    Worse, because the exception was caught INSIDE collect(), the merge recorded the source as
    "ok": sourcesFailed stayed empty, record_collector_failures never fired, collectorOk stayed
    True. Five detectors went silent with no surface anywhere. Raising hands the failure to
    collector_merge, which records it per-source and keeps the other sources' output.
    """
    import pytest
    _fake_build_log_analytics_query(monkeypatch, raise_exc=RuntimeError("LA unreachable"))
    with pytest.raises(RuntimeError, match="LA unreachable"):
        _build_events_collector(_ENV)["collect"]()


def test_a_failed_events_source_lands_in_sourcesFailed_not_in_silence(monkeypatch):
    """End-to-end: the raise above must actually become a health-visible record rather than
    aborting the sweep. This is the half the unit test cannot see."""
    from fabric_audit_agent.adapters.collector_merge import create_merged_collector
    from fabric_audit_agent.automation.health import HealthReport
    _fake_build_log_analytics_query(monkeypatch, raise_exc=RuntimeError("LA unreachable"))
    good = {"collect": lambda: {"capacity": {"peakCuPct": 55.0}}}
    merged = create_merged_collector([good, _build_events_collector(_ENV)])["collect"]()
    assert merged["capacity"]["peakCuPct"] == 55.0        # the healthy source still lands
    assert any("LA unreachable" in str(x) for x in merged.get("sourcesFailed") or [])
    health = HealthReport()
    health.record_collector_failures(merged["sourcesFailed"])
    assert health.degraded is True                        # ... and it reaches the Degraded line


def test_build_events_collector_empty_when_no_rows(monkeypatch):
    _fake_build_log_analytics_query(monkeypatch, rows=[])
    facts = _build_events_collector(_ENV)["collect"]()
    assert facts == {"events": []}


def test_build_collector_from_env_single_source_carries_events(monkeypatch):
    # Only the LA env is set -> build_collector_from_env's collectors list has both the summarized
    # LA collector AND the events collector, so this exercises the merge path even with one source
    # family configured.
    _fake_build_log_analytics_query(monkeypatch, rows=_ROWS)
    collector = build_collector_from_env(_ENV)
    facts = collector["collect"]()
    assert len(facts.get("events") or []) == 3


def test_detect_all_fires_activity_detectors_end_to_end(monkeypatch):
    """The wiring's whole point: given a collector whose merged facts include events with a slow
    op and a recurring shape, detect_all(facts) now emits both new activity findings."""
    _fake_build_log_analytics_query(monkeypatch, rows=_ROWS)
    collector = build_collector_from_env(_ENV)
    facts = collector["collect"]()
    types = {f["type"] for f in detect_all(facts)}
    assert "activity.slow-operation" in types
    assert "activity.recurring-shape" in types


def test_detect_all_no_error_when_events_absent():
    # Fail-open at the OTHER end: a facts dict with no "events" key (e.g. offline/mock/CSV-only
    # collectors, which never populate it) must not error, and the new detectors must simply no-op.
    flags = detect_all({"capacity": {"tenant": "C"}})
    types = {f["type"] for f in flags}
    assert "activity.slow-operation" not in types
    assert "activity.recurring-shape" not in types
    assert "meta.detector-error" not in types

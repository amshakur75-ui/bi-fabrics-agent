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


def test_build_events_collector_fails_open_on_query_error(monkeypatch):
    _fake_build_log_analytics_query(monkeypatch, raise_exc=RuntimeError("LA unreachable"))
    facts = _build_events_collector(_ENV)["collect"]()
    assert facts == {"events": []}   # never raises; empty is the fail-open contract


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

"""TASK 4d (alerting-redesign Sub-plan 4, Part 25e) -- ``describe_source_handler`` (and its
sibling ``sample_events_handler``) must not conflate an AUTH/throttle/timeout/network FAILURE
with a genuine "table not found" / no-data result. Offline: monkeypatches
``adapters.clients.build_log_analytics_query`` (the SP token/HTTP builder) exactly as
``tests/test_events_wiring.py`` does, so the handler runs against a fake ``query(kql)`` that
raises the relevant error -- deterministic, no network.

Covers both the shared classifier (``query.kql_audit_rules.classify_live_query_error``) and its
wiring into the handler layer.
"""
import fabric_audit_agent.adapters.clients as clients
from fabric_audit_agent.query.kql_audit_rules import classify_live_query_error
from fabric_audit_agent.tools import create_tool_definitions

_counter = [0]


def _env():
    """A fresh credential tuple per call -- ``tools._memo_client`` caches the built query
    callable by (workspace, tenant, client_id, secret), so reusing one fixed tuple across tests
    would silently reuse the FIRST test's fake query function for every later test."""
    _counter[0] += 1
    n = _counter[0]
    return {
        "FABRIC_LA_WORKSPACE_ID": f"ws{n}", "FABRIC_TENANT_ID": f"t{n}",
        "FABRIC_CLIENT_ID": f"cid{n}", "FABRIC_CLIENT_SECRET": f"s{n}",
    }


def _fake_build_log_analytics_query(monkeypatch, exc):
    def fake(workspace_id, tenant_id, client_id, client_secret, session=None, **kw):
        def query(kql):
            raise exc
        return query
    monkeypatch.setattr(clients, "build_log_analytics_query", fake)


def _set_env(monkeypatch):
    for key, value in _env().items():
        monkeypatch.setenv(key, value)


def _handlers():
    return {t["name"]: t["handler"] for t in create_tool_definitions()}


# ─────────────────────────────────────────────────────────
# classify_live_query_error -- pure unit coverage
# ─────────────────────────────────────────────────────────

def test_classify_not_found():
    assert classify_live_query_error(RuntimeError(
        "Failed to resolve table or column expression named 'NoSuchTable'")) == "not-found"
    assert classify_live_query_error("SEM0100: entity not found") == "not-found"


def test_classify_auth():
    assert classify_live_query_error(RuntimeError("401 Unauthorized: token has expired")) == "auth"
    assert classify_live_query_error("AADSTS700082: token expired") == "auth"


def test_classify_throttled():
    assert classify_live_query_error(RuntimeError("429 Too Many Requests")) == "throttled"
    assert classify_live_query_error("query was throttled by the service") == "throttled"


def test_classify_timeout():
    assert classify_live_query_error(RuntimeError("Request timed out after 55s")) == "timeout"


def test_classify_network():
    assert classify_live_query_error(RuntimeError("Connection refused")) == "network"


def test_classify_unknown_falls_through():
    assert classify_live_query_error(RuntimeError("something bizarre happened")) == "unknown"


# ─────────────────────────────────────────────────────────
# describe_source_handler -- handler-layer wiring
# ─────────────────────────────────────────────────────────

def test_describe_source_not_found_is_distinct_from_error(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(
        monkeypatch, RuntimeError("Failed to resolve table or column expression named 'Ghost'"))
    out = _handlers()["describe_source"]({"source": "events", "table": "Ghost"})
    assert out["errorClass"] == "not-found"
    assert out.get("found") is False
    assert "error" not in out          # not-found is NOT the generic error shape
    assert "no data" not in out["note"].lower()
    assert "empty" not in out["note"].lower()


def test_describe_source_auth_failure_is_not_phrased_as_no_data(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(monkeypatch, RuntimeError("401 Unauthorized: invalid_grant"))
    out = _handlers()["describe_source"]({"source": "events"})
    assert out["errorClass"] == "auth"
    assert "error" in out
    assert "no data" not in out["error"].lower()
    assert "empty" not in out["error"].lower()


def test_describe_source_throttled_failure(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(monkeypatch, RuntimeError("429 throttled, retry later"))
    out = _handlers()["describe_source"]({"source": "events"})
    assert out["errorClass"] == "throttled"
    assert "no data" not in out["error"].lower()


def test_describe_source_timeout_failure(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(monkeypatch, RuntimeError("The request timed out"))
    out = _handlers()["describe_source"]({"source": "events"})
    assert out["errorClass"] == "timeout"
    assert "no data" not in out["error"].lower()


def test_describe_source_unknown_still_keeps_uniform_envelope(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(monkeypatch, RuntimeError("something bizarre happened"))
    out = _handlers()["describe_source"]({"source": "events"})
    assert out["errorClass"] == "unknown"
    assert out["error"] == "something bizarre happened"
    assert out["source"] == "events"


# ─────────────────────────────────────────────────────────
# sample_events_handler -- same conflation fix applies (identical generic wrap)
# ─────────────────────────────────────────────────────────

def test_sample_events_not_found_is_distinct(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(
        monkeypatch, RuntimeError("SEM0100: entity 'Ghost' not found"))
    out = _handlers()["sample_events"]({"source": "events", "table": "Ghost"})
    assert out["errorClass"] == "not-found"
    assert "error" not in out


def test_sample_events_auth_failure(monkeypatch):
    _set_env(monkeypatch)
    _fake_build_log_analytics_query(monkeypatch, RuntimeError("403 Forbidden"))
    out = _handlers()["sample_events"]({"source": "events"})
    assert out["errorClass"] == "auth"
    assert "no data" not in out["error"].lower()

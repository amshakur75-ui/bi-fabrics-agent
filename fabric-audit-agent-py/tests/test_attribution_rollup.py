"""Shared attribution rollup — source-tolerant per-(workspace,item) + per-user aggregation."""
from fabric_audit_agent.adapters.attribution_rollup import rollup_attribution, identity_email


def test_identity_email_resolves_structured_and_plain():
    assert identity_email({"Email": "a@x.com"}) == "a@x.com"
    assert identity_email({"email": "b@x.com"}) == "b@x.com"
    assert identity_email({"UserPrincipalName": "c@x.com"}) == "c@x.com"
    assert identity_email("d@x.com") == "d@x.com"      # Log Analytics passes a plain string
    assert identity_email(None) is None


def test_rollup_resolves_identity_and_duration_proxy():
    # Live Workspace-Monitoring shape: structured Identity, no CpuTimeMs -> DurationMs is the proxy.
    rows = [
        {"ItemName": "M", "WorkspaceName": "WS", "Identity": {"Email": "a@x.com"}, "DurationMs": 300},
        {"ItemName": "M", "WorkspaceName": "WS", "Identity": {"Email": "b@x.com"}, "DurationMs": 100},
    ]
    out = rollup_attribution(rows)
    item = out["items"][0]
    # 300ms + 100ms = 400ms -> 0.4 CU-seconds (ms converted to seconds, matching normalize_event).
    assert item["name"] == "M" and item["cuSeconds"] == 0.4
    assert item["topUsers"][0]["user"] == "a@x.com" and item["userCount"] == 2
    users = {u["user"]: u for u in out["users"]}
    assert round(users["a@x.com"]["sharePct"]) == 75
    assert users["a@x.com"]["topItems"][0]["name"] == "M"


def test_rollup_skips_nondict_rows():
    # A real query returns dict rows; a stray string/None must never crash the audit.
    rows = [{"ItemName": "M", "ExecutingUser": "u@x.com", "cpuMs": 5}, "PowerBIDatasetsWorkspace", None, 42]
    out = rollup_attribution(rows)
    assert out["items"][0]["name"] == "M" and out["items"][0]["cuSeconds"] == 0.005   # 5ms -> 0.005s


def test_rollup_converts_ms_to_cu_seconds_matching_event_path():
    # A 9000ms CpuTimeMs op must roll up to 9.0 cuSeconds — the SAME scale normalize_event emits
    # (round(9000/1000, 3) == 9.0). Guards against the ~1000x aggregate/event mismatch regression.
    from fabric_audit_agent.investigation.events import normalize_event
    ev = normalize_event({"CpuTimeMs": 9000, "OperationName": "QueryEnd", "ExecutingUser": "x@co"})
    out = rollup_attribution([{"ItemName": "M", "ExecutingUser": "x@co", "CpuTimeMs": 9000}])
    assert out["items"][0]["cuSeconds"] == 9.0 == ev["cuSeconds"]
    assert out["users"][0]["cuSeconds"] == 9.0


def test_rollup_preexisting_cu_seconds_input_is_not_rescaled():
    # If a source already provides cuSeconds (real seconds), don't divide it again.
    out = rollup_attribution([{"ItemName": "M", "ExecutingUser": "x@co", "cuSeconds": 12.5}])
    assert out["items"][0]["cuSeconds"] == 12.5


def test_rollup_empty():
    assert rollup_attribution([]) == {"items": [], "users": []}
    assert rollup_attribution(None) == {"items": [], "users": []}

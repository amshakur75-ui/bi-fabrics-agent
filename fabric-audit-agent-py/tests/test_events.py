from fabric_audit_agent.investigation.events import normalize_event, is_spike

def test_normalize_classifies_refresh_vs_interactive_and_cost():
    q = normalize_event({"TimeGenerated": "2026-06-30T19:57Z", "ExecutingUser": "x@co",
                         "ArtifactName": "Sales", "OperationName": "QueryEnd", "CpuTimeMs": 9000})
    assert q["kind"] == "interactive" and q["cuSeconds"] == 9.0 and q["user"] == "x@co"
    r = normalize_event({"OperationName": "CommandEnd", "DurationMs": 4000, "Identity": {"Email": "y@co"}})
    assert r["kind"] == "refresh" and r["cuSeconds"] == 4.0 and r["user"] == "y@co"  # CpuTimeMs absent -> DurationMs

def test_is_spike_relative_or_absolute():
    assert is_spike({"cuSeconds": 100}, p95=50, floor_cu=1000) is True     # above p95
    assert is_spike({"cuSeconds": 1200}, p95=99999, floor_cu=1000) is True  # above absolute floor
    assert is_spike({"cuSeconds": 10}, p95=50, floor_cu=1000) is False

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
    assert item["name"] == "M" and item["cuSeconds"] == 400
    assert item["topUsers"][0]["user"] == "a@x.com" and item["userCount"] == 2
    users = {u["user"]: u for u in out["users"]}
    assert round(users["a@x.com"]["sharePct"]) == 75
    assert users["a@x.com"]["topItems"][0]["name"] == "M"


def test_rollup_skips_nondict_rows():
    # A real query returns dict rows; a stray string/None must never crash the audit.
    rows = [{"ItemName": "M", "ExecutingUser": "u@x.com", "cpuMs": 5}, "PowerBIDatasetsWorkspace", None, 42]
    out = rollup_attribution(rows)
    assert out["items"][0]["name"] == "M" and out["items"][0]["cuSeconds"] == 5


def test_rollup_empty():
    assert rollup_attribution([]) == {"items": [], "users": []}
    assert rollup_attribution(None) == {"items": [], "users": []}

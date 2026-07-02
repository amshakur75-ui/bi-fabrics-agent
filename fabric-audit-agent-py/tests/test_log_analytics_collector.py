"""Tests for the Log Analytics user-attribution collector (offline; injected query)."""
from fabric_audit_agent.adapters.collector_log_analytics import create_log_analytics_collector


def test_groups_ranks_and_shares():
    rows = [
        {"ArtifactName": "Sales Model", "ExecutingUser": "jane@x.com", "cpuMs": 800},
        {"ArtifactName": "Sales Model", "ExecutingUser": "bob@x.com", "cpuMs": 200},
        {"ArtifactName": "Ops Model", "ExecutingUser": "amy@x.com", "cpuMs": 1000},
    ]
    col = create_log_analytics_collector(lambda kql: rows, {"workspace": "Workspace A"})
    items = {it["name"]: it for it in col["collect"]()["items"]}

    sales = items["Sales Model"]
    assert sales["cuSeconds"] == 1.0                 # 800ms + 200ms = 1000ms -> 1.0 CU-seconds
    assert round(sales["sharePct"]) == 50            # 1000 of 2000 ms total (ratio unchanged)
    assert sales["topUsers"][0]["user"] == "jane@x.com"   # ranked by cpu
    assert sales["userCount"] == 2
    assert sales["attributionMode"] == "cost"
    assert sales["workspace"] == "Workspace A"       # stamped from config when LA omits workspace


def test_resolves_eventhouse_spelling_too():
    # _row tolerates the Eventhouse 'ItemName' spelling as well as LA 'ArtifactName'.
    rows = [{"ItemName": "M", "ExecutingUser": "u@x.com", "cpuMs": 5}]
    items = create_log_analytics_collector(lambda kql: rows)["collect"]()["items"]
    assert items[0]["name"] == "M" and items[0]["sharePct"] == 100


def test_empty():
    assert create_log_analytics_collector(lambda kql: [])["collect"]() == {"items": [], "users": []}


def test_workspace_filter_injects_clause():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    # string form (comma-split) and list form both produce an `in (...)` clause
    create_log_analytics_collector(capture, {"workspaceFilter": "Workspace A, Workspace B"})["collect"]()
    assert 'PowerBIWorkspaceName in ("Workspace A", "Workspace B")' in seen["kql"]

    create_log_analytics_collector(capture, {"workspaceFilter": ["Workspace A"]})["collect"]()
    assert 'PowerBIWorkspaceName in ("Workspace A")' in seen["kql"]


def test_no_filter_is_whole_estate():
    seen = {}
    def cap(kql):
        seen["kql"] = kql
        return []
    create_log_analytics_collector(cap, {})["collect"]()
    assert "PowerBIWorkspaceName in (" not in seen["kql"]   # no filter -> all workspaces


def test_rows_without_user_skipped_in_attribution():
    rows = [
        {"ArtifactName": "M", "ExecutingUser": "", "cpuMs": 50},
        {"ArtifactName": "M", "ExecutingUser": "u@x.com", "cpuMs": 50},
    ]
    item = create_log_analytics_collector(lambda kql: rows)["collect"]()["items"][0]
    assert item["cuSeconds"] == 0.1        # 50ms + 50ms = 100ms -> 0.1 CU-seconds (both rows count)
    assert item["userCount"] == 1          # only the named user is attributed

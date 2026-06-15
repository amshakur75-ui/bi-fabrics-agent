"""Live-source collectors (List Usages + Workspace Monitoring + CSV + merge) — offline with fakes.

Proves the composition: CSV gives authoritative CU share, Workspace Monitoring gives the driving
users, List Usages gives capacity sku/quota; merged, the 30% concentration alert names a user.
"""
from fabric_audit_agent.adapters import (
    create_csv_collector, create_list_usages_collector,
    create_workspace_monitoring_collector, create_merged_collector, merge_facts_list,
)
from fabric_audit_agent.detectors.concentration import detect_concentration


class _FakeHttp:
    def __init__(self, pages):
        self.pages = pages

    def get_json(self, url):
        return self.pages.get(url, {})


# ---------- CSV collector ----------
def test_csv_collector_builds_capacity_and_items(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,45,F64\n2026-06-01T00:00:30,80,F64\n", encoding="utf-8")
    items = tmp_path / "Items.csv"
    items.write_text("Item Name,CU(s),Workspace,Users\nSales Model,7000,Finance,3\nTiny,100,Finance,1\n", encoding="utf-8")
    facts = create_csv_collector([str(cap), str(items)])["collect"]()
    assert facts["capacity"]["peakCuPct"] == 80 and facts["capacity"]["sku"] == "F64"
    sales = next(i for i in facts["items"] if i["name"] == "Sales Model")
    assert sales["sharePct"] > 30   # 7000/7100


def test_csv_collector_sanitizes_raw_spike_percent(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %\n2026-06-01T00:00:00,23069.72\n", encoding="utf-8")
    facts = create_csv_collector([str(cap)])["collect"]()
    assert facts["capacity"]["peakCuPct"] == 0   # > 1000 raw spike sanitized


# ---------- List Usages collector ----------
def test_list_usages_collector_capacity_metadata_and_quota():
    http = _FakeHttp({
        "caps": {"value": [{"id": "cap-1", "displayName": "PROD", "sku": "F64", "state": "Active", "region": "eastus"}]},
        "usages": {"value": [{"name": {"value": "CU"}, "currentValue": 40, "limit": 64}]},
    })
    facts = create_list_usages_collector(http, {"capacitiesUrl": "caps", "usagesUrl": "usages", "capacity": "PROD"})["collect"]()
    c = facts["capacity"]
    assert c["capacityId"] == "PROD" and c["sku"] == "F64" and c["state"] == "Active"
    assert c["cuQuotaUsed"] == 40 and c["cuQuotaLimit"] == 64


def test_list_usages_collector_empty_when_unconfigured():
    assert create_list_usages_collector(_FakeHttp({}), {})["collect"]() == {}


# ---------- Workspace Monitoring collector ----------
def test_workspace_monitoring_ranks_users_per_item():
    rows = [
        {"Workspace": "Finance", "Item": "Sales Model", "ExecutingUser": "alice", "cpuMs": 6000},
        {"Workspace": "Finance", "Item": "Sales Model", "ExecutingUser": "bob", "cpuMs": 3000},
        {"Workspace": "Finance", "Item": "Tiny", "ExecutingUser": "carol", "cpuMs": 100},
    ]
    captured = {}

    def query(kql):
        captured["kql"] = kql
        return rows

    facts = create_workspace_monitoring_collector(query, {"window": "2d"})["collect"]()
    assert "ago(2d)" in captured["kql"]
    sales = next(i for i in facts["items"] if i["name"] == "Sales Model")
    assert sales["topUsers"][0]["user"] == "alice" and sales["userCount"] == 2
    assert sales["attributionMode"] == "cost"


# ---------- merge ----------
def _csv_facts():
    return {"capacity": {"capacityId": "PROD", "peakCuPct": 80, "sku": ""},
            "items": [{"workspace": "Finance", "name": "Sales Model", "sharePct": 34, "cuSeconds": 7000, "users": 3}]}


def _usages_facts():
    return {"capacity": {"capacityId": "PROD", "sku": "F64", "state": "Active", "cuQuotaUsed": 40}}


def _wm_facts():
    return {"items": [{"workspace": "Finance", "name": "Sales Model", "sharePct": 98,
                       "topUsers": [{"user": "alice", "cuSeconds": 6000}], "userCount": 2, "attributionMode": "cost"}]}


def test_merge_combines_cu_share_metadata_and_users():
    merged = merge_facts_list([_csv_facts(), _usages_facts(), _wm_facts()])
    c = merged["capacity"]
    assert c["peakCuPct"] == 80 and c["sku"] == "F64" and c["state"] == "Active"   # CU from CSV, sku/state from usages
    it = merged["items"][0]
    assert it["sharePct"] == 34            # authoritative CU share (CSV) wins over WM's CPU proxy (98)
    assert it["topUsers"][0]["user"] == "alice" and it["userCount"] == 2            # users from WM


def test_merged_collector_then_concentration_names_the_user():
    merged = create_merged_collector([
        {"collect": _csv_facts}, {"collect": _usages_facts}, {"collect": _wm_facts},
    ])["collect"]()
    flags = detect_concentration(merged)
    assert len(flags) == 1
    f = flags[0]
    assert "alice" in f["what"] and "34%" in f["what"]
    assert f["evidence"]["sharePct"] == 34 and f["evidence"]["attributionMode"] == "cost"

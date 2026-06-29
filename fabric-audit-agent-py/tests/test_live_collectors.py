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
    assert facts["capacity"]["peakCuPct"] == 0   # > 1000 raw spike, no usable timepoints signal -> sanitized


def test_csv_collector_uses_timepoints_signal_when_percent_is_raw(tmp_path):
    # Raw "%" column is a pre-smoothing spike (>1000); the real signal is Total CU(s)/baseline + Overloaded states.
    p = tmp_path / "data.csv"
    lines = ["Timepoint,Total CU Usage %,Total CU(s),100% in CU(s),Capacity State Change From Previous Window,SKU"]
    for i in range(10):
        lines.append(f"2026-06-01T00:0{i}:00,30,300,1000,,F64")          # ~30% normal load
    lines.append("2026-06-01T00:10:00,23069,1100,1000,Overloaded,F64")   # raw-% spike + overload
    lines.append("2026-06-01T00:10:30,150,1050,1000,Overloaded,F64")     # over the limit + overload
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    facts = create_csv_collector([str(p)])["collect"]()
    cap = facts["capacity"]
    assert 0 < cap["peakCuPct"] < 1000          # computed p95, not the 23069 raw spike
    assert cap["throttleMinutes"] >= 1          # Overloaded windows counted

    from fabric_audit_agent.detectors.capacity import detect_capacity
    assert "capacity.throttle" in [f["type"] for f in detect_capacity(facts)]   # now correctly flagged


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


def test_list_usages_collector_uses_separate_clients_per_scope():
    # Fabric capacities (Power BI scope) and ARM usages (ARM scope) must use DIFFERENT tokens.
    # Regression guard: one ARM-scoped client for both 401s the Fabric capacities call.
    cap_http = _FakeHttp({"caps": {"value": [{"displayName": "PROD", "sku": "F64"}]}})
    arm_http = _FakeHttp({"usages": {"value": [{"name": {"value": "CU"}, "currentValue": 40, "limit": 64}]}})
    facts = create_list_usages_collector(cap_http, {"capacitiesUrl": "caps", "usagesUrl": "usages"},
                                         usages_http=arm_http)["collect"]()
    c = facts["capacity"]
    assert c["sku"] == "F64"        # capacities resolved via the Power BI client
    assert c["cuQuotaUsed"] == 40   # usages resolved via the ARM client; a shared client would miss this


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


# ---------- additions: WM users[], failed-source surfacing, honest labels ----------
def test_workspace_monitoring_emits_users_for_the_30pct_detector():
    # Before the shared rollup, WM emitted items[] only — so the per-user concentration detector
    # (which reads facts["users"]) never fired from Eventhouse data. It must now emit users[].
    rows = [
        {"Workspace": "Fin", "Item": "Sales", "ExecutingUser": "alice", "cpuMs": 900},
        {"Workspace": "Fin", "Item": "Sales", "ExecutingUser": "bob", "cpuMs": 100},
    ]
    facts = create_workspace_monitoring_collector(lambda kql: rows)["collect"]()
    assert "users" in facts
    assert facts["users"][0]["user"] == "alice" and round(facts["users"][0]["sharePct"]) == 90


def test_merge_surfaces_failed_sources():
    good = {"collect": lambda: {"items": [{"workspace": "W", "name": "I", "sharePct": 10, "cuSeconds": 1}]}}

    def boom():
        raise RuntimeError("LA unreachable")

    merged = create_merged_collector([good, {"collect": boom}])["collect"]()
    assert merged["items"][0]["name"] == "I"                         # the healthy source still lands
    assert any("LA unreachable" in s for s in merged.get("sourcesFailed", []))   # the gap is surfaced


def test_concentration_label_proxy_vs_authoritative():
    # proxy (LA/Eventhouse) share must not claim to be a true capacity share
    proxy = {"items": [{"workspace": "W", "name": "A4A", "sharePct": 100, "cuSeconds": 9,
                        "topUsers": [{"user": "x@co"}], "userCount": 1, "attributionMode": "cost"}]}
    what_proxy = detect_concentration(proxy)[0]["what"]
    assert "monitored CU" in what_proxy and "capacity CU" not in what_proxy
    # authoritative (CSV / Capacity Metrics) keeps "capacity CU"
    auth = {"items": [{"workspace": "W", "name": "Sales", "sharePct": 60, "cuSeconds": 9,
                       "topUsers": [{"user": "x@co"}], "userCount": 1}]}
    assert "capacity CU" in detect_concentration(auth)[0]["what"]

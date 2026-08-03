import math
from fabric_audit_agent.importers.map import map_table, merge_facts, num, find_cu_pct


def test_num_extracts_messy_numbers():
    assert num("87%") == 87
    assert num("1,234 ms") == 1234
    assert num("4.2 GB") == 4.2
    assert math.isnan(num("n/a"))


def test_maps_capacity_items_export():
    headers = ["Capacity Name", "SKU", "Timepoint", "CU % of base capacity", "Throttling (min)", "Workspace", "Item Name", "Size (GB)", "Duration (min)", "Scheduled"]
    rows = [
        {"Capacity Name": "PROD-CAP", "SKU": "F64", "Timepoint": "2026-06-09T09:00", "CU % of base capacity": "72", "Throttling (min)": "0", "Workspace": "Finance", "Item Name": "GL Model", "Size (GB)": "5.1", "Duration (min)": "14", "Scheduled": "06:00"},
        {"Capacity Name": "PROD-CAP", "SKU": "F64", "Timepoint": "2026-06-09T10:00", "CU % of base capacity": "93", "Throttling (min)": "12", "Workspace": "Sales", "Item Name": "Pipeline", "Size (GB)": "1.2", "Duration (min)": "4", "Scheduled": "06:00"},
    ]
    r = map_table(headers, rows)
    c = r["capacity"]
    assert c["sku"] == "F64" and c["capacityId"] == "PROD-CAP"
    assert c["peakCuPct"] == 93 and c["peakAt"] == "2026-06-09T10:00"
    assert c["throttleMinutes"] == 12
    assert len(c["refreshes"]) == 2 and c["refreshes"][0]["sizeGB"] == 5.1
    assert any(cv["field"] == "peakCuPct" and cv["source"] == "CU % of base capacity" for cv in r["coverage"])


def test_coverage_note_when_missing_throttle():
    t = next(c for c in map_table(["SKU", "CU %"], [{"SKU": "F32", "CU %": "50"}])["coverage"] if c["field"] == "throttleMinutes")
    assert t["source"] is None and "throttling" in t["note"]


def test_maps_model_export():
    headers = ["Workspace", "Model Name", "Size (GB)", "Bidirectional Relationships", "Auto Date/Time", "Refresh Fail Rate %"]
    rows = [{"Workspace": "Finance", "Model Name": "GL", "Size (GB)": "7.5", "Bidirectional Relationships": "6", "Auto Date/Time": "Yes", "Refresh Fail Rate %": "12"}]
    assert map_table(headers, rows)["models"] == [{"workspace": "Finance", "name": "GL", "sizeGB": 7.5, "bidirectionalRels": 6, "autoDateTime": True, "refreshFailRatePct": 12}]


def test_maps_report_normalizes_mode():
    headers = ["Workspace", "Report Name", "Visuals", "Storage Mode", "Slowest Visual (ms)"]
    rep = map_table(headers, [{"Workspace": "Sales", "Report Name": "Exec", "Visuals": "34", "Storage Mode": "Direct Query", "Slowest Visual (ms)": "8200"}])["reports"][0]
    assert rep["visuals"] == 34 and rep["mode"] == "DirectQuery" and rep["slowestVisualMs"] == 8200


def test_merge_capacity_max_cu_sum_throttle():
    a = map_table(["SKU", "CU %", "Throttling min"], [{"SKU": "F64", "CU %": "70", "Throttling min": "5"}])
    b = map_table(["SKU", "CU %", "Throttling min"], [{"SKU": "F64", "CU %": "95", "Throttling min": "8"}])
    facts = merge_facts([a, b])
    assert facts["capacity"]["peakCuPct"] == 95 and facts["capacity"]["throttleMinutes"] == 13


def test_merge_joins_models_with_capacity():
    cap = map_table(["SKU", "CU %"], [{"SKU": "F64", "CU %": "88"}])
    mod = map_table(["Model Name", "Bidirectional Rels"], [{"Model Name": "X", "Bidirectional Rels": "9"}])
    facts = merge_facts([cap, mod])
    assert facts["capacity"]["peakCuPct"] == 88 and facts["models"][0]["bidirectionalRels"] == 9


def test_non_capacity_table_yields_empty_facts():
    assert merge_facts([map_table(["Foo", "Bar"], [{"Foo": "1", "Bar": "2"}])]) == {}


def test_real_timepoint_picks_total_cu_usage_not_baseline():
    headers = ["Background %", "Interactive %", "100% in CU(s)", "Autoscale %", "Timepoint", "Total CU Usage %", "Total CU(s)", "CU % Limit"]
    rows = [
        {"Total CU Usage %": "42", "100% in CU(s)": "30720", "CU % Limit": "100", "Timepoint": "t1"},
        {"Total CU Usage %": "118", "100% in CU(s)": "30720", "CU % Limit": "100", "Timepoint": "t2"},
    ]
    r = map_table(headers, rows)
    assert next(c for c in r["coverage"] if c["field"] == "peakCuPct")["source"] == "Total CU Usage %"
    assert r["capacity"]["peakCuPct"] == 118 and r["capacity"]["sku"] == ""


def test_find_cu_pct_priority():
    assert find_cu_pct(["100% in CU(s)", "Total CU Usage %", "CU % Limit"]) == "Total CU Usage %"

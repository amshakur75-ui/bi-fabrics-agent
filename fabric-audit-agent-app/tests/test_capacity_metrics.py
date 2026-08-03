from fabric_audit_agent.importers.capacity_metrics import (
    looks_like_items, map_items, looks_like_timepoints, analyze_timepoints, inspect_columns,
)

ITEM_HEADERS = ["Workspace", "Item kind", "Item name", "CU (s)", "Duration (s)", "Users", "Rejected count", "Billing type"]
TP_HEADERS = ["Background %", "Interactive %", "100% in CU(s)", "Autoscale %", "Timepoint", "Total CU Usage %", "Total CU(s)", "CU % Limit", "Capacity State Change From Previous Window"]


def test_recognizes_items_not_timepoints():
    assert looks_like_items(ITEM_HEADERS) is True
    assert looks_like_items(TP_HEADERS) is False


def test_map_items_ranks_totals_rejections():
    rows = [
        {"Workspace": "Fin", "Item kind": "SemanticModel", "Item name": "GL", "CU (s)": "700000", "Duration (s)": "40", "Users": "12", "Rejected count": "3", "Billing type": "Billable"},
        {"Workspace": "Sales", "Item kind": "Report", "Item name": "Exec", "CU (s)": "250000", "Duration (s)": "5", "Users": "80", "Rejected count": "0", "Billing type": "Billable"},
        {"Workspace": "Ops", "Item kind": "SemanticModel", "Item name": "Inv", "CU (s)": "50000", "Duration (s)": "9", "Users": "4", "Rejected count": "0", "Billing type": "Billable"},
    ]
    a = map_items(ITEM_HEADERS, rows)
    assert a["itemCount"] == 3 and a["totalCu"] == 1_000_000
    assert a["top"][0]["name"] == "GL" and a["top"][0]["pctOfTotal"] == 70
    assert a["rejectedTotal"] == 3 and a["rejectedItems"][0]["name"] == "GL"


def test_recognizes_timepoints_reported_and_computed():
    assert looks_like_timepoints(TP_HEADERS) is True
    rows = [
        {"100% in CU(s)": "30720", "Timepoint": "t1", "Total CU Usage %": "23069", "Total CU(s)": "30720", "Capacity State Change From Previous Window": "None"},
        {"100% in CU(s)": "30720", "Timepoint": "t2", "Total CU Usage %": "15000", "Total CU(s)": "46080", "Capacity State Change From Previous Window": "Overloaded"},
    ]
    a = analyze_timepoints(TP_HEADERS, rows)
    assert a["reportedPeakPct"] == 23069 and a["baseline"] == 30720
    assert a["computedPeakPct"] == 150
    assert a["states"] == {"None": 1, "Overloaded": 1}


def test_inspect_hides_labels_shows_categories_numbers():
    rows = [
        {"Item name": "Secret-X", "CU (s)": "100", "Item kind": "Report"},
        {"Item name": "Secret-Y", "CU (s)": "300", "Item kind": "SemanticModel"},
    ]
    stats = {s["column"]: s for s in inspect_columns(["Item name", "CU (s)", "Item kind"], rows)}
    assert stats["CU (s)"]["type"] == "number" and stats["CU (s)"]["max"] == 300
    assert stats["Item name"]["type"] == "label" and "values" not in stats["Item name"]
    assert stats["Item kind"]["type"] == "category" and stats["Item kind"]["values"] == ["Report", "SemanticModel"]

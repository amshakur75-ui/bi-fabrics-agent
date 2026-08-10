from fabric_audit_agent.importers.capacity_metrics import (
    looks_like_items, map_items, looks_like_timepoints, analyze_timepoints, inspect_columns,
    capacity_signal_from_timepoints,
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
    # Timepoint values are the US-locale shape the real Capacity Metrics export writes. The
    # placeholders "t1"/"t2" used here before are a shape production cannot produce, which is how
    # a Timepoint that never parsed went unnoticed.
    rows = [
        {"100% in CU(s)": "30720", "Timepoint": "8/7/2026 9:00:00 AM", "Total CU Usage %": "23069", "Total CU(s)": "30720", "Capacity State Change From Previous Window": "None"},
        {"100% in CU(s)": "30720", "Timepoint": "8/7/2026 9:05:00 AM", "Total CU Usage %": "15000", "Total CU(s)": "46080", "Capacity State Change From Previous Window": "Overloaded"},
    ]
    a = analyze_timepoints(TP_HEADERS, rows)
    assert a["reportedPeakPct"] == 23069 and a["baseline"] == 30720
    assert a["computedPeakPct"] == 150
    assert a["states"] == {"None": 1, "Overloaded": 1}


# ── capacity_signal_from_timepoints: throttleMinutes is a DURATION, never a row count ──

def _overload_rows(timepoints):
    """24 consecutive Overloaded windows — 5 minutes apart, so the truth is 120 minutes."""
    return [
        {"100% in CU(s)": "30720", "Timepoint": tp, "Total CU(s)": "31900",
         "Capacity State Change From Previous Window": "Overloaded"}
        for tp in timepoints
    ]


def _us_locale_timepoints():
    out = []
    for i in range(24):
        hour, minute = 9 + (i * 5) // 60, (i * 5) % 60
        half = "AM" if hour < 12 else "PM"
        display = hour if hour <= 12 else hour - 12
        out.append(f"8/7/2026 {display}:{minute:02d}:00 {half}")
    return out


def test_throttle_minutes_from_us_locale_export():
    """The export's own locale format: 24 five-minute Overloaded windows = 120 minutes. This
    returned 24 — the raw row count, unflagged — because bare fromisoformat() parsed nothing."""
    sig = capacity_signal_from_timepoints(TP_HEADERS, _overload_rows(_us_locale_timepoints()))
    assert sig["overloadedCount"] == 24
    assert sig["throttleMinutes"] == 120
    assert "throttleMinutesUnavailable" not in sig


def test_throttle_minutes_from_seven_digit_fractional_iso():
    """Log Analytics emits 7 fractional digits; fromisoformat() raises on that under 3.10, which
    is what the job compute runs."""
    timepoints = [f"2026-08-07T{9 + (i * 5) // 60:02d}:{(i * 5) % 60:02d}:00.0000000Z"
                  for i in range(24)]
    sig = capacity_signal_from_timepoints(TP_HEADERS, _overload_rows(timepoints))
    assert sig["throttleMinutes"] == 120


def test_throttle_minutes_unavailable_when_interval_underivable():
    """No derivable interval must not degrade to "24 minutes throttled": severity.py needs > 30
    minutes for Critical, so a count silently downgraded a two-hour overload to Warning."""
    sig = capacity_signal_from_timepoints(TP_HEADERS, _overload_rows(["not a timestamp"] * 24))
    assert sig["overloadedCount"] == 24
    assert sig["throttleMinutes"] is None
    assert "not minutes" in sig["throttleMinutesUnavailable"]


def test_throttle_minutes_is_zero_when_never_overloaded():
    rows = [dict(r, **{"Capacity State Change From Previous Window": "None"})
            for r in _overload_rows(_us_locale_timepoints())]
    sig = capacity_signal_from_timepoints(TP_HEADERS, rows)
    assert sig["overloadedCount"] == 0 and sig["throttleMinutes"] == 0
    assert "throttleMinutesUnavailable" not in sig


def test_inspect_hides_labels_shows_categories_numbers():
    rows = [
        {"Item name": "Secret-X", "CU (s)": "100", "Item kind": "Report"},
        {"Item name": "Secret-Y", "CU (s)": "300", "Item kind": "SemanticModel"},
    ]
    stats = {s["column"]: s for s in inspect_columns(["Item name", "CU (s)", "Item kind"], rows)}
    assert stats["CU (s)"]["type"] == "number" and stats["CU (s)"]["max"] == 300
    assert stats["Item name"]["type"] == "label" and "values" not in stats["Item name"]
    assert stats["Item kind"]["type"] == "category" and stats["Item kind"]["values"] == ["Report", "SemanticModel"]

"""Round 6: unit confusion in the CSV mapper, and a KB gap that shipped placeholder text.

Both produced confident, wrong, user-visible output rather than errors.
"""
from fabric_audit_agent.importers.map import _duration_unit, map_table
from fabric_audit_agent.kb import get_remediation


def _cap(headers, rows):
    # The refreshes block sits inside a broader capacity gate, so a capacity-ish column (SKU) has to
    # be present for this path to run at all.
    return map_table(headers, rows).get("capacity") or {}


# ---- duration units -------------------------------------------------------

def test_duration_unit_detection_works_on_the_normalized_header():
    """_norm strips punctuation, so "Duration (s)" arrives as "durations" and "Duration (ms)" as
    "durationms" — a naive `"(s)" in h` matches neither, which is how seconds were read as minutes."""
    assert _duration_unit("durations") == "sec"
    assert _duration_unit("durationsec") == "sec"
    assert _duration_unit("durationseconds") == "sec"
    assert _duration_unit("durationms") == "ms"
    assert _duration_unit("durationmillis") == "ms"
    assert _duration_unit("duration") == "min"
    assert _duration_unit("durationmin") == "min"


def test_a_seconds_duration_column_is_converted_not_read_as_minutes():
    """A real export ships "Duration (s)". Reading 300 seconds as 300 MINUTES reported a 5-minute
    refresh as "refreshes in 300 min"."""
    cap = _cap(["SKU", "Workspace", "DatasetName", "Duration (s)", "Scheduled Time"],
               [{"SKU": "F64", "Workspace": "Ent", "DatasetName": "Ent-Reporting-DTC",
                 "Duration (s)": "300", "Scheduled Time": "2026-08-10 09:00"}])
    assert cap["refreshes"][0]["durationMin"] == 5.0


def test_a_milliseconds_duration_column_is_converted_too():
    cap = _cap(["SKU", "Workspace", "DatasetName", "Duration (ms)", "Scheduled Time"],
               [{"SKU": "F64", "Workspace": "Ent", "DatasetName": "Ent-Reporting-DTC",
                 "Duration (ms)": "300000", "Scheduled Time": "2026-08-10 09:00"}])
    assert cap["refreshes"][0]["durationMin"] == 5.0


def test_a_minutes_duration_column_is_still_read_as_minutes():
    cap = _cap(["SKU", "Workspace", "DatasetName", "DurationMin", "Scheduled Time"],
               [{"SKU": "F64", "Workspace": "Ent", "DatasetName": "Ent-Reporting-DTC",
                 "DurationMin": "5", "Scheduled Time": "2026-08-10 09:00"}])
    assert cap["refreshes"][0]["durationMin"] == 5.0


# ---- a percentage is not a duration ---------------------------------------

def test_an_interactive_delay_percentage_is_not_summed_into_throttle_minutes():
    """The throttle matcher accepted "interactivedelay", a PERCENTAGE column, and the mapper SUMS
    whatever it matches into throttleMinutes. 288 daily rows averaging 0.5% summed to 144, and
    severity.py (Critical above 30 min) reported "CU peaked 92% with 144 min throttled" for a
    capacity that never delayed a single request."""
    rows = [{"Timepoint": f"2026-08-10T09:{i:02d}:00Z", "Total CU Usage %": "92",
             "InteractiveDelay %": "0.5"} for i in range(30)]
    cap = _cap(["Timepoint", "Total CU Usage %", "InteractiveDelay %"], rows)
    assert cap["throttleMinutes"] == 0
    assert cap["peakCuPct"] == 92.0


def test_a_genuine_throttle_column_still_counts():
    rows = [{"Timepoint": f"2026-08-10T09:{i:02d}:00Z", "Total CU Usage %": "92",
             "Throttled Minutes": "2"} for i in range(10)]
    cap = _cap(["Timepoint", "Total CU Usage %", "Throttled Minutes"], rows)
    assert cap["throttleMinutes"] == 20


# ---- the commonest refresh finding must have real remediation --------------

def test_the_parent_refresh_finding_has_a_real_playbook():
    """detectors/refresh.py emits `refresh.failing` for EVERY failed refresh and only narrows to a
    sub-cause when the error string is recognisable. Without a KB entry, the most frequent refresh
    finding in the product rendered into the report and the Teams card as "Pattern not yet in the
    knowledge base. / Investigate manually and add a playbook entry.\""""
    rem = get_remediation("refresh.failing")
    blob = str(rem).lower()
    assert "not yet in the knowledge base" not in blob
    assert "add a playbook entry" not in blob
    assert rem.get("rootCause") and rem.get("fixes")
    assert len(rem["fixes"]) >= 2

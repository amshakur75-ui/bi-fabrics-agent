"""B3: refresh chronic/repeat-pattern detection + error text + the refresh collector + merge slot."""
from fabric_audit_agent.detectors.refresh import detect_refreshes, refresh_failure_pattern
from fabric_audit_agent.detectors import detect_all
from fabric_audit_agent.adapters.collector_refresh import create_refresh_collector
from fabric_audit_agent.adapters.collector_merge import merge_facts_list


def _fail(code, desc=None, when="2026-08-05T06:00:00Z", ds="Sales Model", ws="Finance"):
    import json as _j
    body = {"errorCode": code}
    if desc:
        body["errorDescription"] = desc
    return {"status": "Failed", "refreshType": "Scheduled", "startTime": when, "endTime": when,
            "serviceExceptionJson": _j.dumps(body), "datasetName": ds, "workspace": ws,
            "refreshAttempts": [{"type": "Data", "startTime": when, "endTime": when}]}


def test_chronic_flag_when_same_error_repeats():
    # 3 failures, same error code -> chronic (default chronicFailureCount=3)
    rs = [_fail("Timeout", when=f"2026-08-05T0{i}:00:00Z") for i in range(3)]
    flags = detect_refreshes({"refreshes": rs})
    chronic = [f for f in flags if f["type"] == "refresh.chronic"]
    assert len(chronic) == 1
    assert chronic[0]["evidence"]["count"] == 3 and chronic[0]["evidence"]["errorCode"] == "Timeout"


def test_single_failure_is_not_chronic():
    flags = detect_refreshes({"refreshes": [_fail("Timeout")]})
    assert not any(f["type"] == "refresh.chronic" for f in flags)   # one blip != chronic
    assert any(f["type"] == "refresh.failing" for f in flags)


def test_error_text_surfaced_not_just_code():
    f = next(x for x in detect_refreshes({"refreshes": [_fail("GatewayOffline", "The gateway is offline")]})
             if x["type"] == "refresh.failing")
    assert f["evidence"]["errorText"] == "The gateway is offline"
    assert "The gateway is offline" in f["what"]


def test_failure_pattern_groups_and_counts():
    rs = [_fail("A"), _fail("A"), _fail("B"), {"status": "Completed", "datasetName": "x", "workspace": "y"}]
    groups = refresh_failure_pattern(rs)
    assert groups[0]["count"] == 2 and groups[0]["errorCode"] == "A"    # sorted by count desc
    assert {g["errorCode"] for g in groups} == {"A", "B"}               # completed one ignored


def test_collector_maps_api_to_detector_shape():
    class _Http:
        def get_json(self, url):
            assert "groups/ws1/datasets/ds1/refreshes" in url
            return {"value": [{"status": "Failed", "refreshType": "Scheduled",
                               "startTime": "2026-08-05T06:00:00Z", "endTime": "2026-08-05T06:01:00Z",
                               "serviceExceptionJson": "{\"errorCode\":\"Boom\"}",
                               "refreshAttempts": [{"type": "Data"}]}]}
    col = create_refresh_collector(_Http(), {"datasets": [
        {"group": "ws1", "dataset": "ds1", "workspace": "Finance", "datasetName": "Sales"}]})
    facts = col["collect"]()
    assert len(facts["refreshes"]) == 1
    r = facts["refreshes"][0]
    assert r["workspace"] == "Finance" and r["datasetName"] == "Sales" and r["status"] == "Failed"
    # end to end: the collected shape drives the detector
    assert any(f["type"] == "refresh.failing" for f in detect_refreshes(facts))


def test_collector_fail_open_on_http_error():
    class _Boom:
        def get_json(self, url):
            raise RuntimeError("404 not a member")
    col = create_refresh_collector(_Boom(), {"datasets": [{"group": "g", "dataset": "d"}]})
    assert col["collect"]()["refreshes"] == []   # skipped, no crash


def test_merge_preserves_refreshes_slot():
    # a standalone refresh collector's facts must survive being merged with other sources
    merged = merge_facts_list([
        {"capacity": {"peakCuPct": 50}},
        {"refreshes": [{"status": "Failed", "datasetName": "d", "workspace": "w"}]},
    ])
    assert len(merged.get("refreshes", [])) == 1


def test_detect_all_emits_refresh_flags_when_fed():
    facts = {"refreshes": [_fail("Timeout", when=f"2026-08-05T0{i}:00:00Z") for i in range(3)]}
    types = {f["type"] for f in detect_all(facts)}
    assert "refresh.failing" in types and "refresh.chronic" in types

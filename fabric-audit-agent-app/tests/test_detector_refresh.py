from fabric_audit_agent.detectors.refresh import detect_refreshes

_FAILED = {"status": "Failed", "refreshType": "Scheduled", "startTime": "2026-07-07T06:00:00Z",
           "endTime": "2026-07-07T06:01:00Z",
           "serviceExceptionJson": "{\"errorCode\":\"ModelRefreshFailed_CredentialsNotSpecified\"}",
           "datasetName": "Sales Model", "workspace": "Finance",
           "refreshAttempts": [{"attemptId": 1, "type": "Data",
                                 "startTime": "2026-07-07T06:00:00Z", "endTime": "2026-07-07T06:01:00Z",
                                 "serviceExceptionJson": "{\"errorCode\":\"ModelRefreshFailed_CredentialsNotSpecified\"}"}]}

def test_failed_refresh_classified_by_error_code():
    flags = detect_refreshes({"refreshes": [_FAILED]})
    f = next(x for x in flags if x["type"] == "refresh.failing")
    assert f["evidence"]["errorCode"] == "ModelRefreshFailed_CredentialsNotSpecified"
    assert "Sales Model" in f["resource"] and "CredentialsNotSpecified" in f["what"]

def test_retry_storm_flagged_at_three_attempts():
    r = {**_FAILED, "refreshAttempts": [_FAILED["refreshAttempts"][0]] * 3}
    assert any(x["type"] == "refresh.retry-storm" for x in detect_refreshes({"refreshes": [r]}))
    assert not any(x["type"] == "refresh.retry-storm"
                   for x in detect_refreshes({"refreshes": [_FAILED]}))   # 1 attempt: no storm

def test_slow_data_phase_flagged():
    r = {"status": "Completed", "datasetName": "Big", "workspace": "W",
         "startTime": "2026-07-07T01:00:00Z",
         "refreshAttempts": [{"attemptId": 1, "type": "Data",
                               "startTime": "2026-07-07T01:00:00Z", "endTime": "2026-07-07T02:30:00Z"}]}
    f = next(x for x in detect_refreshes({"refreshes": [r]}) if x["type"] == "refresh.slow-phase")
    assert f["evidence"] == {"phase": "Data", "minutes": 90.0}

def test_malformed_exception_json_yields_unparseable_not_crash():
    r = {**_FAILED, "serviceExceptionJson": "not json"}
    f = next(x for x in detect_refreshes({"refreshes": [r]}) if x["type"] == "refresh.failing")
    assert f["evidence"]["errorCode"] == "unparseable"

def test_no_refreshes_key_returns_empty():
    assert detect_refreshes({}) == []
    assert detect_refreshes({"refreshes": []}) == []

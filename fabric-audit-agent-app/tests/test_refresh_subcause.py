"""tightening.md Part 12 Cat 1: refresh-failure sub-cause classification — distinct causes
(credential/gateway/timeout/concurrency/constraint) surfaced as their own findings, separate from
the generic refresh.failing and the aggregate refresh.chronic."""
import json as _j

from fabric_audit_agent.detectors.refresh import classify_refresh_failure, detect_refreshes


def _fail(code, desc=None, when="2026-08-07T06:00:00Z", ds="Sales Model", ws="Finance"):
    body = {"errorCode": code}
    if desc:
        body["errorDescription"] = desc
    return {"status": "Failed", "refreshType": "Scheduled", "startTime": when, "endTime": when,
            "serviceExceptionJson": _j.dumps(body), "datasetName": ds, "workspace": ws,
            "refreshAttempts": [{"type": "Data", "startTime": when, "endTime": when}]}


def test_credential_subcause():
    r = _fail("ExpiredToken", "AADSTS70008: The provided authorization code or access token has expired")
    assert classify_refresh_failure(r) == "credential"
    f = next(x for x in detect_refreshes({"refreshes": [r]}) if x["type"] == "refresh.credential")
    assert f["evidence"]["cause"] == "credential"
    assert f["evidence"]["item"] == "Sales Model" and f["evidence"]["workspace"] == "Finance"


def test_gateway_subcause():
    r = _fail("DMGatewayError", "The on-premises data gateway is offline or unreachable")
    assert classify_refresh_failure(r) == "gateway"
    assert any(x["type"] == "refresh.gateway" for x in detect_refreshes({"refreshes": [r]}))


def test_timeout_subcause():
    r = _fail(-2147467259, "Execution Timeout Expired")
    assert classify_refresh_failure(r) == "timeout"
    assert any(x["type"] == "refresh.timeout" for x in detect_refreshes({"refreshes": [r]}))


def test_concurrency_subcause():
    r = _fail("RefreshConcurrencyLimit", "The refresh has exceeded the maximum number of concurrent refreshes")
    assert classify_refresh_failure(r) == "concurrency"
    assert any(x["type"] == "refresh.concurrency" for x in detect_refreshes({"refreshes": [r]}))


def test_constraint_subcause():
    r = _fail("SqlError", "Violation of PRIMARY KEY constraint: Cannot insert duplicate key")
    assert classify_refresh_failure(r) == "constraint"
    assert any(x["type"] == "refresh.constraint" for x in detect_refreshes({"refreshes": [r]}))


def test_unknown_error_yields_no_subcause():
    r = _fail("SomeWeirdError", "Something unrelated happened")
    assert classify_refresh_failure(r) is None
    types = {x["type"] for x in detect_refreshes({"refreshes": [r]})}
    assert not any(t.startswith("refresh.") and t not in ("refresh.failing",) for t in types)


def test_successful_refresh_yields_no_subcause():
    r = {"status": "Completed", "datasetName": "d", "workspace": "w"}
    assert classify_refresh_failure(r) is None
    assert detect_refreshes({"refreshes": [r]}) == []


def test_malformed_record_does_not_crash():
    assert classify_refresh_failure(None) is None
    assert classify_refresh_failure("not a dict") is None
    assert classify_refresh_failure({"status": "Failed", "serviceExceptionJson": 12345}) is None
    # a malformed refresh record fed through detect_refreshes should not crash the pipeline
    detect_refreshes({"refreshes": [{"status": "Failed"}]})

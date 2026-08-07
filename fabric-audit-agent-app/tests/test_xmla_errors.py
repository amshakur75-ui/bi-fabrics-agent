from fabric_audit_agent.detectors.xmla_errors import detect_xmla_errors
from fabric_audit_agent.investigation.xmla_classify import classify_xmla_error


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "durationMs": 1000, "cuSeconds": 1.0, "queryText": None}
    ev.update(overrides)
    return ev


# ---- classify_xmla_error (pure) ----

def test_classify_auth_failure():
    assert classify_xmla_error("Authentication failed: AADSTS50173 token is expired") == "auth"


def test_classify_bad_request_tmsl():
    assert classify_xmla_error("Bad Request: the TMSL command could not be processed") == "bad-request"


def test_classify_timeout():
    assert classify_xmla_error("XML for Analysis request timed out after 300000ms") == "timeout"


def test_classify_connection_drop():
    assert classify_xmla_error("An existing connection was forcibly closed by the remote host") == "connection-drop"


def test_classify_session_moved_is_suppressed():
    assert classify_xmla_error("The session has been moved to another node for load balancing") is None
    assert classify_xmla_error("Session moved to another node.") is None


def test_classify_benign_query_text_returns_none():
    assert classify_xmla_error("EVALUATE SUMMARIZE(Sales, Sales[Region])") is None


def test_classify_malformed_input_never_raises():
    assert classify_xmla_error(None) is None
    assert classify_xmla_error(123) is None
    assert classify_xmla_error("") is None
    assert classify_xmla_error("   ") is None
    assert classify_xmla_error({"not": "a string"}) is None


# ---- detect_xmla_errors (facts-in/flags-out wiring) ----

def test_detector_fires_for_each_cause():
    events = [
        _event(queryText="Authentication failed: AADSTS50173 token is expired", item="Model A"),
        _event(queryText="Bad Request: the TMSL command could not be processed", item="Model B"),
        _event(queryText="XML for Analysis request timed out after 300000ms", item="Model C"),
        _event(queryText="An existing connection was forcibly closed by the remote host", item="Model D"),
    ]
    flags = detect_xmla_errors({"events": events})
    types = {f["type"] for f in flags}
    assert types == {"xmla.auth", "xmla.bad-request", "xmla.timeout", "xmla.connection-drop"}
    for f in flags:
        assert f["evidence"]["item"]
        assert f["evidence"]["user"] == "alice@corp.com"
        assert f["evidence"]["cause"] in ("auth", "bad-request", "timeout", "connection-drop")


def test_detector_suppresses_session_moved():
    events = [_event(queryText="The session was moved to another node for load balancing.")]
    flags = detect_xmla_errors({"events": events})
    assert flags == []


def test_detector_ignores_benign_operation():
    events = [_event(queryText="EVALUATE SUMMARIZE(Sales, Sales[Region])")]
    flags = detect_xmla_errors({"events": events})
    assert flags == []


def test_detector_handles_malformed_events_without_crash():
    events = [
        {},
        {"queryText": None},
        {"queryText": 12345},
        _event(queryText="Bad Request: TMSL error", item="Model E"),
    ]
    flags = detect_xmla_errors({"events": events})
    assert len(flags) == 1
    assert flags[0]["type"] == "xmla.bad-request"


def test_detector_empty_facts_no_crash():
    assert detect_xmla_errors({}) == []
    assert detect_xmla_errors(None) == []


def test_detector_truncates_long_error_text():
    long_text = "Bad Request: " + ("x" * 500)
    events = [_event(queryText=long_text)]
    flags = detect_xmla_errors({"events": events})
    assert len(flags) == 1
    assert len(flags[0]["evidence"]["errorText"]) <= 300

import pytest
from fabric_audit_agent.finding import create_finding, wrap_envelope


def _ok():
    return {"what": "w", "where": "x", "when": "", "why": "y", "impact": "i",
            "fix": ["f"], "score": {"level": "Warning", "reason": "r"}}


def test_create_finding_builds_seven_fields():
    f = create_finding(_ok())
    assert set(f.keys()) == {"what", "where", "when", "why", "impact", "fix", "score"}
    assert f["fix"] == ["f"]


def test_create_finding_allows_empty_string_when():
    assert create_finding({**_ok(), "when": ""})["when"] == ""


def test_create_finding_missing_field_raises():
    bad = _ok()
    del bad["impact"]
    with pytest.raises(ValueError):
        create_finding(bad)


def test_create_finding_fix_must_be_list():
    with pytest.raises(TypeError):
        create_finding({**_ok(), "fix": "not-a-list"})


def test_wrap_envelope_shape():
    env = wrap_envelope(agent_id="a", findings=[], summary="s")
    assert env["success"] is True
    assert env["agent_id"] == "a"
    assert env["data"]["findings"] == []
    assert "timestamp" in env

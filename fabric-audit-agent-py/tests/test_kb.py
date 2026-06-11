from fabric_audit_agent.kb import get_remediation


def test_known_flag_returns_playbook():
    r = get_remediation("capacity.concentration")
    assert "noisy neighbor" in r["rootCause"]
    assert isinstance(r["fixes"], list) and r["fixes"]
    assert r["owner"]


def test_every_domain_has_at_least_one_known_flag():
    for t in [
        "capacity.throttle", "model.bidirectional", "report.slow-visual", "pipeline.gateway",
        "lineage.blast-radius", "security.admin-grant", "cost.idle-capacity", "meta.detector-error",
    ]:
        assert get_remediation(t)["rootCause"] != "Pattern not yet in the knowledge base.", t


def test_unknown_flag_returns_default():
    assert get_remediation("nope.nope")["rootCause"].startswith("Pattern not yet")

from fabric_audit_agent.eval.score_investigations import run_agent_suite


def test_agent_suite_all_golden_cases_pass(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    res = run_agent_suite()
    assert res["total"] >= 1 and res["passed"] == res["total"]

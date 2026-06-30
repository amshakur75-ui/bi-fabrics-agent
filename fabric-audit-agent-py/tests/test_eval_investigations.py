from fabric_audit_agent.eval.score_investigations import run_suite


def test_suite_runs_and_all_golden_cases_pass():
    res = run_suite()
    assert res["total"] >= 2
    assert res["passed"] == res["total"]      # the shipped golden cases must pass on the stub

"""Tests for automation.preflight.run_preflight -- the read-only startup config/health snapshot
(Sub-plan 5 Part 5d). Never a connection test: no live calls, just env-var presence and
catalog-file presence checks."""
from fabric_audit_agent.automation.preflight import run_preflight
from fabric_audit_agent.resolve import catalog_dir


def _names(report):
    return {c["name"] for c in report["checks"]}


def test_missing_la_env_reports_that_check_failed_with_detail():
    report = run_preflight({})
    la = next(c for c in report["checks"] if c["name"] == "log-analytics")
    assert la["ok"] is False
    assert la["detail"]
    assert "FABRIC_LA_WORKSPACE_ID" in la["detail"]
    assert report["ok"] is False
    assert report["degraded"] is True


def test_partial_la_env_still_reports_failed_with_only_missing_named():
    report = run_preflight({"FABRIC_LA_WORKSPACE_ID": "ws-1"})
    la = next(c for c in report["checks"] if c["name"] == "log-analytics")
    assert la["ok"] is False
    assert "FABRIC_CLIENT_ID" in la["detail"]
    assert "FABRIC_LA_WORKSPACE_ID" not in la["detail"]


def test_all_present_env_checks_pass():
    env = {
        "FABRIC_LA_WORKSPACE_ID": "ws-1",
        "FABRIC_CLIENT_ID": "client-1",
        "FABRIC_CAPACITY_EVENTS_CLUSTER": "cluster-1",
        "FABRIC_CAPACITY_EVENTS_DB": "db-1",
    }
    report = run_preflight(env)
    la = next(c for c in report["checks"] if c["name"] == "log-analytics")
    ce = next(c for c in report["checks"] if c["name"] == "capacity-events")
    assert la["ok"] is True
    assert ce["ok"] is True


def test_unset_csv_paths_is_ok_optional_source():
    report = run_preflight({})
    csv = next(c for c in report["checks"] if c["name"] == "csv-paths")
    assert csv["ok"] is True


def test_configured_but_missing_csv_path_fails():
    report = run_preflight({"FABRIC_CSV_PATHS": "/definitely/not/a/real/path.csv"})
    csv = next(c for c in report["checks"] if c["name"] == "csv-paths")
    assert csv["ok"] is False
    assert "path.csv" in csv["detail"]


def test_catalog_present_on_real_repo_checkout():
    # The real repo ships the pre-built catalog manifest -- this exercises the happy path
    # without mocking the filesystem.
    manifest = catalog_dir() / "manifest.json"
    report = run_preflight({})
    catalog_check = next(c for c in report["checks"] if c["name"] == "catalog-manifest")
    assert catalog_check["ok"] is manifest.exists()


def test_catalog_missing_detected(monkeypatch, tmp_path):
    import fabric_audit_agent.resolve as resolve_mod
    monkeypatch.setattr(resolve_mod, "catalog_dir", lambda: tmp_path / "no-such-catalog")
    report = run_preflight({})
    catalog_check = next(c for c in report["checks"] if c["name"] == "catalog-manifest")
    assert catalog_check["ok"] is False
    assert "manifest.json" in catalog_check["detail"]


def test_run_preflight_never_raises_on_none_env():
    report = run_preflight(None)
    assert isinstance(report, dict)
    assert "checks" in report


def test_structured_result_shape():
    report = run_preflight({})
    assert set(report.keys()) == {"ok", "checks", "degraded"}
    assert report["degraded"] == (not report["ok"])
    for check in report["checks"]:
        assert set(check.keys()) == {"name", "ok", "detail"}

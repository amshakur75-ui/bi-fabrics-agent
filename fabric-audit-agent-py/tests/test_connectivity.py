"""Phase 2 SP connectivity test — offline (fake http + fake token provider; no msal/requests)."""
from fabric_audit_agent.connectivity import (
    check_connectivity, workspace_probe_url, format_report,
)


class _FakeHttp:
    def __init__(self, page=None, error=None):
        self._page = page if page is not None else {"value": []}
        self._error = error
        self.calls = []

    def get_json(self, url):
        self.calls.append(url)
        if self._error:
            raise self._error
        return self._page


def test_workspace_probe_url_builds_from_id_or_passes_url():
    assert workspace_probe_url("ws-123").endswith("/groups/ws-123/datasets")
    assert workspace_probe_url("https://api.powerbi.com/x") == "https://api.powerbi.com/x"
    assert workspace_probe_url(None) is None and workspace_probe_url("") is None


def test_check_connectivity_all_good():
    http = _FakeHttp({"value": [{"id": "d1"}, {"id": "d2"}]})
    res = check_connectivity(http, "u", token_provider=lambda: "TOK")
    assert res["ok"] is True
    assert [s["step"] for s in res["steps"]] == ["token", "workspace_read"]
    assert "2 dataset" in res["steps"][1]["detail"]


def test_check_connectivity_token_failure_short_circuits_before_api():
    http = _FakeHttp()

    def boom():
        raise RuntimeError("invalid_client")

    res = check_connectivity(http, "u", token_provider=boom)
    assert res["ok"] is False
    assert len(res["steps"]) == 1 and res["steps"][0]["step"] == "token"
    assert http.calls == []   # never hit the API without a token


def test_check_connectivity_workspace_403_is_authorization_failure():
    http = _FakeHttp(error=PermissionError("403 Forbidden"))
    res = check_connectivity(http, "u", token_provider=lambda: "TOK")
    assert res["ok"] is False
    assert res["steps"][1]["step"] == "workspace_read" and res["steps"][1]["ok"] is False


def test_check_connectivity_without_token_provider_just_reads():
    res = check_connectivity(_FakeHttp({"value": [1]}), "u")
    assert res["ok"] is True and [s["step"] for s in res["steps"]] == ["workspace_read"]


def test_format_report_pass_and_fail_with_hint():
    ok = format_report({"ok": True, "steps": [{"step": "token", "ok": True, "detail": "x"}]})
    assert "PASS" in ok
    bad = format_report({"ok": False, "steps": [{"step": "workspace_read", "ok": False, "detail": "403"}]})
    assert "FAIL" in bad and "Power BI APIs" in bad

"""fetch_all_pages — page-guard log line must redact secrets embedded in the URL."""
from fabric_audit_agent.adapters.collector_rest import fetch_all_pages


class _InfinitePagesHttp:
    """Always returns a page with a nextLink, forcing the 1000-page guard to trip."""

    def __init__(self, base_url):
        self.base_url = base_url

    def get_json(self, url):
        return {"value": [{"id": 1}], "nextLink": self.base_url}


def test_fetch_all_pages_guard_log_redacts_secret_in_url(capsys):
    secret_url = "https://api.fabric.example.com/refreshes?continuationToken=abc&sig=SECRETTOKEN"
    http = _InfinitePagesHttp(secret_url)

    fetch_all_pages(http, secret_url)

    out = capsys.readouterr().out
    assert "page guard reached" in out
    assert "SECRETTOKEN" not in out
    assert "sig=***" in out

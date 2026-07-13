"""Tests for the direct read-only Fabric client (Feature 2). Offline — injected fake HTTP."""
import asyncio

from agent_server.fabric_direct import direct_tools_and_dispatch, _ENDPOINTS, is_configured


ENV = {"FABRIC_TENANT_ID": "tenant-1", "FABRIC_CLIENT_ID": "client-1", "FABRIC_CLIENT_SECRET": "secret-1"}


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Http:
    def __init__(self, token=None, get=None):
        self.posts = []
        self.gets = []
        self._token = token or _Resp({"access_token": "tok-abc", "expires_in": 3600})
        self._get = get or _Resp([{"id": "w1", "displayName": "Sales"}])

    def post(self, url, data=None, timeout=None):
        self.posts.append({"url": url, "data": data})
        return self._token

    def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url, "headers": headers})
        return self._get


def _run(coro):
    return asyncio.run(coro)


# ---- inert / configuration ----
def test_inert_when_unconfigured():
    assert direct_tools_and_dispatch({}) == ([], {})
    assert is_configured({}) is False
    assert is_configured(ENV) is True


def test_tools_shape_when_configured():
    tools, dispatch = direct_tools_and_dispatch(ENV, http=_Http())
    names = {t["name"] for t in tools}
    assert names == set(_ENDPOINTS)                       # exactly the allowlist, nothing more
    for t in tools:
        assert t["name"].startswith("fabric_")           # no collision with MCP tool names
        assert t["input_schema"]["type"] == "object"
    assert set(dispatch) == set(_ENDPOINTS)


# ---- read-only by construction ----
def test_data_path_is_get_only_and_only_post_is_the_token_endpoint():
    http = _Http()
    _, dispatch = direct_tools_and_dispatch(ENV, http=http)
    _run(dispatch["fabric_list_workspaces"]({}))
    # exactly one Fabric data call, and it's a GET
    assert len(http.gets) == 1 and http.gets[0]["url"].endswith("/workspaces")
    # the ONLY POST is to the Entra token endpoint — never to a Fabric resource
    assert len(http.posts) == 1
    assert "login.microsoftonline.com" in http.posts[0]["url"]
    assert "api.fabric.microsoft.com" not in http.posts[0]["url"]


def test_bearer_token_attached():
    http = _Http()
    _, dispatch = direct_tools_and_dispatch(ENV, http=http)
    _run(dispatch["fabric_list_workspaces"]({}))
    assert http.gets[0]["headers"]["Authorization"] == "Bearer tok-abc"


# ---- param handling ----
def test_param_substitution_url_encoded():
    http = _Http()
    _, dispatch = direct_tools_and_dispatch(ENV, http=http)
    _run(dispatch["fabric_list_items"]({"workspaceId": "ws 1/a"}))
    assert http.gets[0]["url"].endswith("/workspaces/ws%201%2Fa/items")


def test_missing_param_errors_without_calling_fabric():
    http = _Http()
    _, dispatch = direct_tools_and_dispatch(ENV, http=http)
    out = _run(dispatch["fabric_list_items"]({}))
    assert "missing required parameter" in out["error"]
    assert http.gets == []                                # never hit Fabric on a bad call


# ---- secret scrub (shape-only; leaves metadata alone) ----
def test_secret_scrubbed_from_response():
    leaky = _Resp({"value": [{"name": "ok", "token": "eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2"}]})
    _, dispatch = direct_tools_and_dispatch(ENV, http=_Http(get=leaky))
    out = _run(dispatch["fabric_list_workspaces"]({}))
    assert "eyJhbGciOiJI" not in str(out)                 # JWT masked
    assert out["value"][0]["name"] == "ok"                # ordinary metadata preserved


def test_ordinary_metadata_not_scrubbed():
    body = _Resp({"value": [{"id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301", "displayName": "Ent-Reporting"}]})
    _, dispatch = direct_tools_and_dispatch(ENV, http=_Http(get=body))
    out = _run(dispatch["fabric_list_workspaces"]({}))
    assert out["value"][0]["id"] == "3f2504e0-4f89-11d3-9a0c-0305e82c3301"   # GUID untouched
    assert out["value"][0]["displayName"] == "Ent-Reporting"


# ---- error surface ----
def test_4xx_returns_clean_error():
    _, dispatch = direct_tools_and_dispatch(ENV, http=_Http(get=_Resp({"error": "forbidden"}, status=403)))
    out = _run(dispatch["fabric_list_workspaces"]({}))
    assert out["error"] == "Fabric REST 403"


def test_pbi_endpoints_hit_powerbi_base_get_only():
    # C1: schedule/datasets live on the Power BI REST base with its own token scope - still GET-only.
    http = _Http()
    _, dispatch = direct_tools_and_dispatch(ENV, http=http)
    _run(dispatch["fabric_refresh_schedule"]({"workspaceId": "w1", "datasetId": "d1"}))
    assert http.gets[-1]["url"] == "https://api.powerbi.com/v1.0/myorg/groups/w1/datasets/d1/refreshSchedule"
    _run(dispatch["fabric_list_datasets"]({"workspaceId": "w1"}))
    assert http.gets[-1]["url"].endswith("/groups/w1/datasets")
    # every token POST still goes to Entra, never to a data host
    assert all("login.microsoftonline.com" in p["url"] for p in http.posts)
    scopes = {p["data"]["scope"] for p in http.posts}
    assert "https://analysis.windows.net/powerbi/api/.default" in scopes

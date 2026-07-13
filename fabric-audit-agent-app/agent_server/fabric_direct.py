"""Direct read-only Microsoft Fabric access for the CAMP agent (Feature 2).

A SECOND data path alongside the MCP tools: the model can query Fabric REST directly when that's the
better route, and the agent decides per task which path to use.

READ-ONLY BY CONSTRUCTION. Every Fabric data call is an HTTP **GET** against a fixed allowlist of
endpoints — there is no POST/PUT/PATCH/DELETE to any Fabric resource anywhere in this module (the only
POST is to the Entra token endpoint to authenticate). It is additionally bounded by the service
principal's read-only permissions.

INERT unless FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET all resolve: with no creds it
offers zero tools and the MCP path is entirely unaffected — nothing breaks pre-configuration.
"""
import asyncio
import os
import re
import time
import urllib.parse

_DEFAULT_BASE = "https://api.fabric.microsoft.com/v1"
_PBI_BASE = "https://api.powerbi.com/v1.0/myorg"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
# Per-base token scopes: Fabric REST and Power BI REST are separate resources.
_SCOPES = {
    "fabric": "https://api.fabric.microsoft.com/.default",
    "pbi": "https://analysis.windows.net/powerbi/api/.default",
}
_TIMEOUT = 30

# Fixed allowlist: tool name -> {GET path template, required params, description}. GET-only, always.
_ENDPOINTS = {
    "fabric_list_workspaces": {
        "path": "/workspaces", "params": [],
        "desc": "List the Fabric workspaces the agent's identity can see (direct Fabric REST, read-only)."},
    "fabric_list_items": {
        "path": "/workspaces/{workspaceId}/items", "params": ["workspaceId"],
        "desc": "List items (datasets, reports, dataflows, etc.) in a Fabric workspace (direct, read-only)."},
    "fabric_list_capacities": {
        "path": "/capacities", "params": [],
        "desc": "List the Fabric capacities visible to the agent's identity (direct Fabric REST, read-only)."},
    "fabric_dataset_refresh_history": {
        "path": "/workspaces/{workspaceId}/semanticModels/{semanticModelId}/refreshes",
        "params": ["workspaceId", "semanticModelId"],
        "desc": "Refresh history for a semantic model / dataset (direct Fabric REST, read-only). Funnel stage: WHY - failed/long refreshes."},
    "fabric_refresh_schedule": {
        "base": "pbi",
        "path": "/groups/{workspaceId}/datasets/{datasetId}/refreshSchedule",
        "params": ["workspaceId", "datasetId"],
        "desc": "A dataset's configured refresh SCHEDULE (direct Power BI REST, read-only). Funnel stage: WHY - overlapping schedules are the refresh-contention signal."},
    "fabric_list_datasets": {
        "base": "pbi",
        "path": "/groups/{workspaceId}/datasets",
        "params": ["workspaceId"],
        "desc": "Datasets in a workspace (direct Power BI REST, read-only). Funnel stage: ATTRIBUTE - map an item name to its dataset id."},
}

# Shape-only secret scrub: mask a JWT or a connection-string secret IF one ever appears in a response.
# Deliberately does NOT touch GUIDs, names, or ordinary metadata — the team needs that data (per the
# user's "don't hide legitimate data" direction). This only catches accidental credential leakage.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_CONN_SECRET_RE = re.compile(r"(?i)(accountkey|sharedaccesskey|password)=([^;\"']+)")

_CACHE = {"built": False, "tools": [], "dispatch": {}}


def _scrub(value):
    if isinstance(value, str):
        return _CONN_SECRET_RE.sub(r"\1=***", _JWT_RE.sub("***", value))
    if isinstance(value, list):
        return [_scrub(x) for x in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def is_configured(env):
    return all(env.get(k) for k in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET"))


def _default_http():
    import requests
    return requests


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"raw": getattr(resp, "text", "")}


def _acquire_token(env, http, scope):
    url = _TOKEN_URL.format(tenant=urllib.parse.quote(str(env["FABRIC_TENANT_ID"]), safe=""))
    resp = http.post(url, data={
        "grant_type": "client_credentials",
        "client_id": env["FABRIC_CLIENT_ID"],
        "client_secret": env["FABRIC_CLIENT_SECRET"],
        "scope": scope,
    }, timeout=_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return body["access_token"], time.monotonic() + int(body.get("expires_in", 3600)) - 60


def _build_path(spec, inp):
    inp = inp or {}
    missing = [p for p in spec["params"] if not str(inp.get(p) or "").strip()]
    if missing:
        return None, f"missing required parameter(s): {', '.join(missing)}"
    path = spec["path"]
    for p in spec["params"]:
        path = path.replace("{" + p + "}", urllib.parse.quote(str(inp[p]), safe=""))
    return path, None


def direct_tools_and_dispatch(env=None, *, http=None):
    """Return (tools, dispatch) for the direct read-only Fabric endpoints, or ([], {}) if unconfigured.

    ``tools`` mirror the MCP tool shape ({name, description, input_schema}); ``dispatch`` maps each
    name to an ``async fn(inp) -> result``. The real (non-injected-http) build is cached so the SP
    token is reused across turns.
    """
    env = env if env is not None else os.environ
    if not is_configured(env):
        return [], {}
    if http is None and _CACHE["built"]:
        return _CACHE["tools"], _CACHE["dispatch"]

    real = http is None
    http = http if http is not None else _default_http()
    bases = {"fabric": (env.get("FABRIC_API_BASE") or _DEFAULT_BASE).rstrip("/"),
             "pbi": _PBI_BASE}
    token_boxes = {k: {"token": None, "exp": 0.0} for k in _SCOPES}

    def _token(base_key):
        box = token_boxes[base_key]
        if not box["token"] or time.monotonic() >= box["exp"]:
            box["token"], box["exp"] = _acquire_token(env, http, _SCOPES[base_key])
        return box["token"]

    def _get_sync(name, inp):
        spec = _ENDPOINTS[name]
        path, err = _build_path(spec, inp)
        if err:
            return {"error": err}
        base_key = spec.get("base", "fabric")
        resp = http.get(bases[base_key] + path,
                        headers={"Authorization": f"Bearer {_token(base_key)}"}, timeout=_TIMEOUT)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            return {"error": f"Fabric REST {status}", "detail": _scrub(_safe_json(resp))}
        return _scrub(_safe_json(resp))

    def _make(name):
        async def handler(inp):
            return await asyncio.to_thread(_get_sync, name, inp)
        return handler

    tools = [{
        "name": name,
        "description": spec["desc"],
        "input_schema": {
            "type": "object",
            "properties": {p: {"type": "string"} for p in spec["params"]},
            "required": list(spec["params"]),
        },
    } for name, spec in _ENDPOINTS.items()]
    dispatch = {name: _make(name) for name in _ENDPOINTS}

    if real:
        _CACHE.update(built=True, tools=tools, dispatch=dispatch)
    return tools, dispatch

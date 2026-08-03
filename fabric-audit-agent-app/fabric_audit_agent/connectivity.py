"""Phase 2 — single-workspace service-principal connectivity test.

Proves, locally and read-only, that the Entra SP can (1) authenticate (client-credentials)
and (2) read ONE Power BI workspace over the REST API — BEFORE any Databricks wiring. It
isolates the two failure modes so you know exactly which knob is wrong:

  * token step fails        -> SP client id / tenant id / secret wrong (an Entra/AAD problem)
  * workspace-read 401/403  -> tenant setting "Service principals can use Power BI APIs" not
                               enabled for your group, OR the SP isn't in that group, OR the SP
                               isn't a Viewer on this workspace (a Power BI authorization problem)
  * workspace-read OK       -> identity + gate + scope all good; ready for Phase 3 (Databricks)

Run (default = SP client-credentials):
    python -m fabric_audit_agent.connectivity <workspaceId | full-url>
Or as yourself (delegated device-code sign-in — no SP secret, no tenant setting):
    python -m fabric_audit_agent.connectivity <workspaceId> --auth user
SP mode needs FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET; user mode needs just
FABRIC_TENANT_ID / FABRIC_CLIENT_ID. Needs the ``.[prod]`` extras (requests + msal). Read-only:
the only call is a GET of the workspace datasets.
"""
import os
import sys

_PBI_BASE = "https://api.powerbi.com/v1.0/myorg"


def workspace_probe_url(workspace):
    """A workspace-scoped, read-only endpoint. Accepts a full URL or a workspace (group) id."""
    if not workspace:
        return None
    if str(workspace).startswith("http"):
        return workspace
    return f"{_PBI_BASE}/groups/{workspace}/datasets"


def _record_count(page):
    if isinstance(page, dict):
        v = page.get("value")
        return len(v) if isinstance(v, list) else 1
    if isinstance(page, list):
        return len(page)
    return 0


def check_connectivity(http, workspace_url, token_provider=None):
    """Run the SP smoke test against an injected http client (testable offline).

    Returns ``{"ok": bool, "steps": [{"step","ok","detail"}, ...]}``. ``token_provider`` (optional)
    is called first so an auth failure is reported distinctly from an authorization failure.
    """
    steps = []
    if token_provider is not None:
        try:
            tok = token_provider()
            steps.append({"step": "token", "ok": bool(tok),
                          "detail": "access token acquired" if tok else "empty token returned"})
        except Exception as e:  # noqa: BLE001 - surface the exact auth failure to the user
            steps.append({"step": "token", "ok": False, "detail": f"{type(e).__name__}: {e}"})
            return {"ok": False, "steps": steps}   # no point probing the API without a token

    try:
        page = http.get_json(workspace_url)
        steps.append({"step": "workspace_read", "ok": True,
                      "detail": f"read {_record_count(page)} dataset(s) from {workspace_url}"})
    except Exception as e:  # noqa: BLE001 - surface the exact API failure (401/403/etc.)
        steps.append({"step": "workspace_read", "ok": False, "detail": f"{type(e).__name__}: {e}"})

    return {"ok": all(s["ok"] for s in steps), "steps": steps}


_HINTS = {
    "token": "Check FABRIC_CLIENT_ID / FABRIC_TENANT_ID / FABRIC_CLIENT_SECRET (Entra app + secret).",
    "workspace_read": ('401/403 -> in the Power BI Admin portal enable "Service principals can use '
                       'Power BI APIs" for your security group, add the SP to that group, and add the '
                       "SP as Viewer on this workspace."),
}


def format_report(result):
    lines = ["", "===== Phase 2 - single-workspace SP connectivity test =====", ""]
    for s in result["steps"]:
        lines.append(f"  [{'OK' if s['ok'] else 'XX'}] {s['step']}: {s['detail']}")
        if not s["ok"]:
            lines.append(f"        -> {_HINTS.get(s['step'], '')}")
    lines.append("")
    lines.append("  RESULT: " + (
        "PASS - identity + gate + workspace scope all good. Ready for Phase 3 (Databricks)."
        if result["ok"] else "FAIL - fix the step(s) above and re-run."))
    return "\n".join(lines) + "\n"


def _parse_args(argv):
    """Return ``(auth_mode, workspace)``. ``--auth sp|user`` (default sp); first positional = workspace."""
    auth, workspace = "sp", None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--auth", "-a") and i + 1 < len(argv):
            auth = argv[i + 1].lower(); i += 2; continue
        if a.startswith("--auth="):
            auth = a.split("=", 1)[1].lower(); i += 1; continue
        if not a.startswith("-") and workspace is None:
            workspace = a
        i += 1
    return auth, workspace


_USAGE = (
    "Usage: python -m fabric_audit_agent.connectivity <workspaceId | full-url> [--auth sp|user]\n"
    "  --auth sp   (default) one service principal -> one workspace; needs FABRIC_TENANT_ID /\n"
    "              FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET + the SP API tenant setting + Viewer.\n"
    "  --auth user your own login (device-code), no SP/secret/tenant-setting; needs FABRIC_TENANT_ID\n"
    "              / FABRIC_CLIENT_ID (public-client app) and that you're a member of the workspace.\n"
    "  (workspace also reads from FABRIC_TEST_WORKSPACE.)"
)


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = env if env is not None else os.environ
    auth, workspace = _parse_args(argv)
    workspace = workspace or env.get("FABRIC_TEST_WORKSPACE")
    url = workspace_probe_url(workspace)

    if auth not in ("sp", "user"):
        print(f'Unknown --auth "{auth}" (use: sp | user)')
        return {"ok": False, "steps": []}
    if not url:
        print(_USAGE)
        return {"ok": False, "steps": []}

    need = ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET") if auth == "sp" \
        else ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID")
    missing = [k for k in need if not env.get(k)]
    if missing:
        print(f"Missing required env for --auth {auth}: {', '.join(missing)} — set and re-run.")
        return {"ok": False, "steps": []}

    from .adapters.clients import EntraHttp, build_entra_token_provider, build_user_token_provider
    if auth == "sp":
        token = build_entra_token_provider(env["FABRIC_TENANT_ID"], env["FABRIC_CLIENT_ID"], env["FABRIC_CLIENT_SECRET"])
    else:
        token = build_user_token_provider(env["FABRIC_TENANT_ID"], env["FABRIC_CLIENT_ID"])

    print(f"(auth mode: {auth})")
    result = check_connectivity(EntraHttp(token), url, token_provider=token)
    print(format_report(result))
    return result


if __name__ == "__main__":
    main()

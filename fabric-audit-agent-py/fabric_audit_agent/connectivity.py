"""Phase 2 — single-workspace service-principal connectivity test.

Proves, locally and read-only, that the Entra SP can (1) authenticate (client-credentials)
and (2) read ONE Power BI workspace over the REST API — BEFORE any Databricks wiring. It
isolates the two failure modes so you know exactly which knob is wrong:

  * token step fails        -> SP client id / tenant id / secret wrong (an Entra/AAD problem)
  * workspace-read 401/403  -> tenant setting "Service principals can use Power BI APIs" not
                               enabled for your group, OR the SP isn't in that group, OR the SP
                               isn't a Viewer on this workspace (a Power BI authorization problem)
  * workspace-read OK       -> identity + gate + scope all good; ready for Phase 3 (Databricks)

Run:
    python -m fabric_audit_agent.connectivity <workspaceId | full-url>
with FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET in the environment (needs the
``.[prod]`` extras: requests + msal). Read-only: the only call is a GET of the workspace datasets.
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ
    workspace = argv[0] if argv else env.get("FABRIC_TEST_WORKSPACE")
    url = workspace_probe_url(workspace)
    if not url:
        print("Usage: python -m fabric_audit_agent.connectivity <workspaceId | full-url>\n"
              "  (or set FABRIC_TEST_WORKSPACE). Needs FABRIC_TENANT_ID / FABRIC_CLIENT_ID / "
              "FABRIC_CLIENT_SECRET in the environment.")
        return {"ok": False, "steps": []}

    missing = [k for k in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET") if not env.get(k)]
    if missing:
        print(f"Missing required env: {', '.join(missing)} — set the SP credentials and re-run.")
        return {"ok": False, "steps": []}

    from .adapters.clients import EntraHttp, build_entra_token_provider
    token = build_entra_token_provider(env["FABRIC_TENANT_ID"], env["FABRIC_CLIENT_ID"], env["FABRIC_CLIENT_SECRET"])
    result = check_connectivity(EntraHttp(token), url, token_provider=token)
    print(format_report(result))
    return result


if __name__ == "__main__":
    main()

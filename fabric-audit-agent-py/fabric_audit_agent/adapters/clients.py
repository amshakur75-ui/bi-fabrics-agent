"""Concrete client builders for production deploy (Databricks).

These wrap real SDKs into the shapes the adapters expect:
  - HTTP client  -> ``.get_json(url)`` / ``.post_json(url, body)``  (collector_rest, delivery_teams)
  - Anthropic    -> ``.messages.create(...)`` with ``resp.content[0].text``  (reasoner_claude)

Optional deps (``requests``, ``msal``, ``anthropic``) are imported lazily so this module —
and the whole ``adapters`` package — imports cleanly without them. Tests and offline runs
never touch these builders; they inject fakes instead.

Identity note (from the deployment research): only an **Entra app registration / service
principal** in an allowed security group can call the Power BI / Fabric Admin APIs. A bare
Managed Identity cannot. ``build_entra_token_provider`` uses the client-credentials flow.
"""

POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
ARM_SCOPE = "https://management.azure.com/.default"   # Azure Resource Manager (Fabric capacity List Usages)


class EntraHttp:
    """Blocking JSON HTTP client with Entra bearer auth, for the REST collector / Teams push.

    ``token_provider`` is a zero-arg callable returning a current access token (see
    ``build_entra_token_provider``). ``session`` defaults to a ``requests.Session`` (lazy
    import); inject a fake in tests to avoid the dependency.
    """

    def __init__(self, token_provider, session=None, timeout=30):
        if session is None:
            import requests  # lazy: only needed for a real session
            session = requests.Session()
        self._token = token_provider
        self._session = session
        self._timeout = timeout

    def _headers(self, extra=None):
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        if extra:
            headers.update(extra)
        return headers

    def get_json(self, url):
        r = self._session.get(url, headers=self._headers(), timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def post_json(self, url, body, headers=None):
        r = self._session.post(url, json=body, headers=self._headers(headers), timeout=self._timeout)
        r.raise_for_status()
        # Teams incoming webhooks reply with a bare "1", not JSON — tolerate that.
        try:
            return r.json()
        except Exception:
            return None


class PlainJsonHttp:
    """Unauthenticated JSON HTTP client — for Teams *incoming webhooks*, which take no auth
    (the authed Bot Service path uses ``EntraHttp`` instead). Inject a fake session in tests."""

    def __init__(self, session=None, timeout=30):
        if session is None:
            import requests  # lazy
            session = requests.Session()
        self._session = session
        self._timeout = timeout

    def get_json(self, url):
        r = self._session.get(url, headers={"Accept": "application/json"}, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def post_json(self, url, body):
        r = self._session.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=self._timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return None


def build_entra_token_provider(tenant_id, client_id, client_secret, scope=POWERBI_SCOPE):
    """Client-credentials token provider via MSAL. MSAL caches tokens internally, so the
    returned callable is cheap to call per request."""
    import msal  # lazy

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )

    def get_token():
        result = app.acquire_token_for_client(scopes=[scope])
        if "access_token" not in result:
            raise RuntimeError(
                "Entra token acquisition failed: "
                f"{result.get('error_description') or result.get('error')}"
            )
        return result["access_token"]

    return get_token


def build_user_token_provider(tenant_id, client_id, scope=POWERBI_SCOPE, prompt=print):
    """Delegated (user) token via MSAL **device-code** flow — signs in as YOU, with **no client
    secret** and **no "service principals can use APIs" tenant setting**. The app registration must
    have "Allow public client flows" enabled. Ideal for a hands-on single-workspace test; not for an
    unattended job (it requires interactive sign-in). ``prompt`` receives the device-code message."""
    import msal  # lazy

    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant_id or 'organizations'}"
    )

    def get_token():
        accounts = app.get_accounts()
        if accounts:
            cached = app.acquire_token_silent([scope], account=accounts[0])
            if cached and "access_token" in cached:
                return cached["access_token"]
        flow = app.initiate_device_flow(scopes=[scope])
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow init failed: {flow.get('error_description') or flow}")
        prompt(flow["message"])   # "To sign in, open https://microsoft.com/devicelogin and enter CODE"
        result = app.acquire_token_by_device_flow(flow)   # blocks until you finish signing in
        if "access_token" not in result:
            raise RuntimeError(
                "Device-code sign-in failed: "
                f"{result.get('error_description') or result.get('error')}"
            )
        return result["access_token"]

    return get_token


def build_anthropic_client(api_key=None, base_url=None):
    """Return an Anthropic SDK client (``.messages.create`` already matches the adapter).

    ``base_url`` lets you point at a Databricks-hosted / gateway Claude endpoint; with both
    omitted the SDK reads ``ANTHROPIC_API_KEY`` from the environment.
    """
    import anthropic  # lazy

    kwargs = {}
    if api_key is not None:
        kwargs["api_key"] = api_key
    if base_url is not None:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)


def build_kusto_query(cluster_uri, database, tenant_id, client_id, client_secret,
                       *, timeout_seconds=240, action="query", client=None):
    """Return a ``query(kql) -> list[dict]`` callable against a Fabric Eventhouse / Kusto cluster
    (Workspace Monitoring). Lazy-imports ``azure-kusto-data``; service-principal app-key auth.
    Inject a fake ``client`` in tests — this builder is only used at deploy.

    Every ``execute`` carries read-only-hardline request properties (belt-and-suspenders on top
    of the RBAC Viewer role), a bounded server timeout, and a traceable ``FAA.<action>:<uuid>``
    client request id — the exact shape audited from microsoft/fabric-rti-mcp (MIT). This client
    is always read-only, so there is no destructive-classification path to gate.

    When ``client`` is injected (tests), the real SDK is never imported: a plain dict shaped like
    ``ClientRequestProperties`` (``{"Options": {...}, "ClientRequestId": ...}``) is passed instead
    — fakes only need to inspect the shape.
    """
    real = client is None
    if real:
        from azure.kusto.data import KustoClient, KustoConnectionStringBuilder  # lazy

        kcsb = KustoConnectionStringBuilder.with_aad_application_key_authentication(
            cluster_uri, client_id, client_secret, tenant_id
        )
        client = KustoClient(kcsb)

    def query(kql):
        from uuid import uuid4

        request_id = f"FAA.{action}:{uuid4()}"
        if real:
            from azure.kusto.data import ClientRequestProperties  # lazy
            from datetime import timedelta  # lazy

            crp = ClientRequestProperties()
            crp.set_option("request_readonly", True)
            crp.set_option("request_readonly_hardline", True)
            crp.set_option(ClientRequestProperties.request_timeout_option_name, timedelta(seconds=timeout_seconds))
            crp.client_request_id = request_id
        else:
            crp = {
                "Options": {
                    "request_readonly": True,
                    "request_readonly_hardline": True,
                    "servertimeout": f"{timeout_seconds}s",
                },
                "ClientRequestId": request_id,
            }
        resp = client.execute(database, kql, crp)
        table = resp.primary_results[0]
        cols = [c.column_name for c in table.columns]
        return [dict(zip(cols, row)) for row in table.rows]

    return query


LOGANALYTICS_SCOPE = "https://api.loganalytics.io/.default"   # Logs query API — NOT the ARM scope


def build_log_analytics_query(workspace_id, tenant_id, client_id, client_secret, session=None,
                               *, timeout_seconds=240):
    """Return a ``query(kql) -> list[dict]`` callable against the Azure Monitor Logs query API
    (``api.loganalytics.io``), for the Log Analytics attribution collector. Service-principal
    client-credentials with the ``LOGANALYTICS_SCOPE`` audience (the SP needs the Azure RBAC
    ``Log Analytics Reader`` role on the workspace). Mirrors ``build_kusto_query``; inject a fake
    ``query`` in tests — this builder is only used at deploy.

    Sends ``Prefer: wait=<timeout_seconds>`` (LA server-side wait cap) on every request. The Logs
    query API is read-only by design, so there's no CRP-equivalent to set here."""
    token = build_entra_token_provider(tenant_id, client_id, client_secret, scope=LOGANALYTICS_SCOPE)
    http = EntraHttp(token, session=session)
    url = f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query"

    def query(kql, timespan=None):
        body = {"query": kql}
        if timespan:
            body["timespan"] = timespan
        resp = http.post_json(url, body, headers={"Prefer": f"wait={timeout_seconds}"})
        tables = (resp or {}).get("tables") or []
        if not tables:
            return []
        cols = [c.get("name") for c in (tables[0].get("columns") or [])]
        return [dict(zip(cols, row)) for row in (tables[0].get("rows") or [])]

    return query


def build_databricks_claude_client(endpoint="databricks-claude-opus-4-7", openai_client=None):
    """Use a Databricks-hosted Claude serving endpoint as the reasoner, exposed in the Anthropic
    shape the reasoner expects (``.messages.create(...) -> resp.content[0].text``) so
    ``create_claude_reasoner`` works unchanged. Internally it calls the OpenAI-compatible Databricks
    serving API (``chat.completions``).

    On a Databricks cluster, with no ``openai_client`` passed, it auto-builds one via the Databricks
    SDK (``WorkspaceClient().serving_endpoints.get_open_ai_client()`` — auto-authenticated; needs the
    ``openai`` package). ``endpoint`` is the serving-endpoint name — confirm yours under Serving / the
    AI Playground. Inject a fake ``openai_client`` in tests. Keeps everything in-tenant (no external key).
    """
    if openai_client is None:
        import os
        try:
            from databricks.sdk import WorkspaceClient  # lazy; get_open_ai_client added in SDK 0.28
            openai_client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        except Exception:
            # Fallback: build OpenAI client directly from env vars (Databricks Apps / PAT auth).
            # Set DATABRICKS_TOKEN in app.yaml to a Databricks PAT if this path is needed.
            from openai import OpenAI  # lazy
            host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
            token = os.environ.get("DATABRICKS_TOKEN", "")
            if not token or not host:
                raise RuntimeError(
                    "Databricks Claude client: no credentials found. "
                    "Add DATABRICKS_TOKEN (a Databricks PAT) to app.yaml, or ensure the App "
                    "service principal has serving-endpoint access configured."
                )
            openai_client = OpenAI(api_key=token, base_url=f"{host}/serving-endpoints")

    def _system_text(system):
        if isinstance(system, str):
            return system
        return " ".join(b.get("text", "") for b in (system or []) if isinstance(b, dict)).strip()

    class _Block:
        def __init__(self, text):
            self.text = text

    class _Resp:
        def __init__(self, text):
            self.content = [_Block(text)]

    class _Messages:
        def create(self, model=None, max_tokens=1024, system=None, messages=None):
            chat = []
            sys_text = _system_text(system)
            if sys_text:
                chat.append({"role": "system", "content": sys_text})
            for m in (messages or []):
                chat.append({"role": m.get("role", "user"), "content": m.get("content", "")})
            resp = openai_client.chat.completions.create(model=model or endpoint, messages=chat, max_tokens=max_tokens)
            return _Resp(resp.choices[0].message.content)

    class _Client:
        messages = _Messages()

    return _Client()


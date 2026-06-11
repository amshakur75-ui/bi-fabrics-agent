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

    def _headers(self):
        return {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}

    def get_json(self, url):
        r = self._session.get(url, headers=self._headers(), timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def post_json(self, url, body):
        r = self._session.post(url, json=body, headers=self._headers(), timeout=self._timeout)
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

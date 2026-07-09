"""Identity-aware token resolution + least-privilege scope manifest (Phase 5.3, read-only-safe,
INERT plumbing).

``resolve_identity`` is a thin selection/labeling seam over the *existing* MSAL providers in
``adapters.clients`` -- it does NOT reimplement token acquisition. Today only the ``user_token``
branch (a caller-supplied token, no real OBO exchange behind it yet) and the service-principal
branch (the agent's only real path) are built and tested. A future Agent-Identity (federated
credential / fmi_path) branch is a documented extension point ONLY -- see the comment below --
never a guessed env contract or a mock.

Wiring is label-only (Task 2): callers resolve identity to emit an honest ``runIdentity`` audit
label at the primary sweep path. The resolved ``provider`` here is not threaded into the six
existing SP token-construction sites (``job.py`` x3, ``clients.py`` Log Analytics, ``connectivity.py``,
``tools.py`` run_kql) -- activating any non-SP identity for real requires routing ALL SIX of those
sites through this resolver, or ``runIdentity`` would overstate the identity actually used
elsewhere. That is a Phase-7, admin-gated activation step, not part of this change.
"""
import json
import os


def _require(env, name):
    """Raises the SAME ``RuntimeError`` shape ``job.py``'s ``_require`` already raises on missing
    SP config. Deliberately duplicated (not imported from ``job``) to avoid an import cycle now
    that ``job.py`` calls into this module -- both are tiny and intentionally kept in lockstep."""
    v = env.get(name)
    if not v:
        raise RuntimeError(f"Missing required config: {name} (set via Databricks secret scope / job env).")
    return v


_IDENTITY_NOTE = "identity resolved for the primary data path"


def resolve_identity(env, *, user_token=None, scope=None, sp_provider_factory=None):
    """Return ``{"provider": <zero-arg token callable>, "identity": <label>, "note": <static str>}``.

    Selection priority (first available wins):

      # --- Phase-7 EXTENSION POINT (DO NOT IMPLEMENT): Agent Identity (federated/fmi_path) goes
      #     HERE, above user/SP, returning identity "agentIdentity". Build it against the REAL
      #     grant in Phase 7 -- do not add a guessed env contract or a mock now. ---

      1. ``user_token`` -- if given, a provider that returns it as-is; identity "user".
      2. Service principal (today's only real path) -- ``(sp_provider_factory or
         clients.build_entra_token_provider)(tenant_id, client_id, client_secret, scope or
         clients.POWERBI_SCOPE)`` built from ``env``; identity "servicePrincipal". A missing SP
         env var surfaces the SAME ``RuntimeError`` the SP path already raises -- never masked.

    ``note`` is a fixed, static string -- it never interpolates env, secret, or tenant values.
    """
    # --- Phase-7 EXTENSION POINT (DO NOT IMPLEMENT): Agent Identity (federated/fmi_path) goes
    #     HERE, above user/SP, returning identity "agentIdentity". Build it against the REAL grant
    #     in Phase 7 -- do not add a guessed env contract or a mock now. ---

    if user_token is not None:
        return {
            "provider": lambda: user_token,
            "identity": "user",
            "note": _IDENTITY_NOTE,
        }

    from .adapters import clients  # lazy: only needed for the SP branch (keeps identity.py stdlib-only otherwise)

    factory = sp_provider_factory if sp_provider_factory is not None else clients.build_entra_token_provider
    tenant_id = _require(env, "FABRIC_TENANT_ID")
    client_id = _require(env, "FABRIC_CLIENT_ID")
    client_secret = _require(env, "FABRIC_CLIENT_SECRET")
    provider = factory(tenant_id, client_id, client_secret,
                       scope if scope is not None else clients.POWERBI_SCOPE)
    return {
        "provider": provider,
        "identity": "servicePrincipal",
        "note": _IDENTITY_NOTE,
    }


def load_scope_manifest(path=None):
    """Load the package-adjacent least-privilege scope manifest (``scopes.json``, next to this
    module by default). Each entry: ``{scope, purpose, grantedBy, requiredForSources, tier}``.
    Tolerates a missing or malformed file (returns ``[]``), mirroring
    ``tools._load_query_library`` -- a packaging slip degrades to an empty manifest rather than
    crashing the caller."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scopes.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return []

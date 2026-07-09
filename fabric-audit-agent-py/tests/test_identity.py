"""Phase 5.3 identity plumbing (read-only-safe, inert) -- offline, deterministic.

resolve_identity: SP-only env / user_token / label-honesty / missing-config / no-secret-in-note.
load_scope_manifest: parses scopes.json; missing file -> [].
Read-only guard + PERMISSIONS.md <-> scopes.json consistency (no orphan either direction).
"""
import json
import os
import re

import pytest

from fabric_audit_agent.identity import resolve_identity, load_scope_manifest, emit_identity_audit

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERMISSIONS_PATH = os.path.join(_REPO_ROOT, "PERMISSIONS.md")

_SP_ENV = {
    "FABRIC_TENANT_ID": "tenant-1",
    "FABRIC_CLIENT_ID": "client-1",
    "FABRIC_CLIENT_SECRET": "SENTINEL-SECRET-XYZ",
}


def _fake_sp_factory(calls):
    def factory(tenant_id, client_id, client_secret, scope):
        calls.append((tenant_id, client_id, client_secret, scope))
        return lambda: "SP-TOKEN"
    return factory


def test_sp_only_env_resolves_service_principal_and_delegates_to_injected_factory():
    calls = []
    result = resolve_identity(_SP_ENV, scope="SOME_SCOPE", sp_provider_factory=_fake_sp_factory(calls))
    assert result["identity"] == "servicePrincipal"
    assert calls == [("tenant-1", "client-1", "SENTINEL-SECRET-XYZ", "SOME_SCOPE")]
    assert result["provider"]() == "SP-TOKEN"


def test_sp_default_scope_used_when_scope_not_passed():
    calls = []
    from fabric_audit_agent.adapters import clients
    resolve_identity(_SP_ENV, sp_provider_factory=_fake_sp_factory(calls))
    assert calls[0][3] == clients.POWERBI_SCOPE


def test_user_token_given_resolves_user_and_beats_service_principal():
    calls = []
    result = resolve_identity(_SP_ENV, user_token="USER-TOKEN", sp_provider_factory=_fake_sp_factory(calls))
    assert result["identity"] == "user"
    assert result["provider"]() == "USER-TOKEN"
    assert calls == []   # SP factory never invoked -- user_token takes priority


def test_missing_sp_env_raises_existing_runtime_error_unmasked():
    with pytest.raises(RuntimeError, match="FABRIC_CLIENT_ID"):
        resolve_identity({"FABRIC_TENANT_ID": "tenant-1", "FABRIC_CLIENT_SECRET": "s"},
                          sp_provider_factory=_fake_sp_factory([]))


def test_note_is_static_and_never_leaks_the_secret():
    calls = []
    result = resolve_identity(_SP_ENV, sp_provider_factory=_fake_sp_factory(calls))
    assert "SENTINEL-SECRET-XYZ" not in result["note"]
    assert "tenant-1" not in result["note"]
    assert "client-1" not in result["note"]

    result_user = resolve_identity(_SP_ENV, user_token="USER-TOKEN", sp_provider_factory=_fake_sp_factory(calls))
    assert "SENTINEL-SECRET-XYZ" not in result_user["note"]


def test_label_matches_branch_taken():
    calls = []
    sp_result = resolve_identity(_SP_ENV, sp_provider_factory=_fake_sp_factory(calls))
    assert sp_result["identity"] == "servicePrincipal"

    user_result = resolve_identity(_SP_ENV, user_token="U", sp_provider_factory=_fake_sp_factory(calls))
    assert user_result["identity"] == "user"


def test_load_scope_manifest_parses_default_file():
    manifest = load_scope_manifest()
    assert isinstance(manifest, list)
    assert len(manifest) > 0
    for entry in manifest:
        assert set(entry) >= {"scope", "purpose", "grantedBy", "requiredForSources", "tier"}


def test_load_scope_manifest_missing_file_returns_empty_list():
    assert load_scope_manifest(path="/no/such/path/scopes.json") == []


def test_load_scope_manifest_malformed_file_returns_empty_list(tmp_path):
    bad = tmp_path / "scopes.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert load_scope_manifest(path=str(bad)) == []


# ---- Read-only guard ----

_WRITE_MARKERS = (".Write", ".Manage", ".ReadWrite", "Contributor", "Owner")


def test_manifest_is_non_empty_and_read_only_only():
    manifest = load_scope_manifest()
    assert len(manifest) > 0
    for entry in manifest:
        scope = entry["scope"]
        for marker in _WRITE_MARKERS:
            assert marker not in scope, f"{scope!r} looks write-capable ({marker!r})"


def test_manifest_includes_documented_core_scopes():
    scopes = {entry["scope"] for entry in load_scope_manifest()}
    for core in ("Capacity.Read.All", "Workspace.Read.All", "Dataset.Read.All"):
        assert core in scopes


# ---- PERMISSIONS.md <-> scopes.json consistency ----

_NAMED_RBAC_ROLES = ("Reader", "Monitoring Reader", "Log Analytics Reader", "Storage Blob Data Reader")

# One pattern covers both API-scope style ("Capacity.Read.All") and Graph style
# ("User.ReadBasic.All"): word-chars, dot-separated segments, ending ".Read<word-chars>.All".
_SCOPE_RE = re.compile(r"\b\w+(?:\.\w+)*\.Read\w*\.All\b")


def _scopes_and_roles_from_permissions_md():
    with open(_PERMISSIONS_PATH, encoding="utf-8") as fh:
        text = fh.read()
    found = set(_SCOPE_RE.findall(text))
    for role in _NAMED_RBAC_ROLES:
        if re.search(rf"\b{re.escape(role)}\b", text):
            found.add(role)
    return found


def test_permissions_md_is_committed_and_readable():
    assert os.path.isfile(_PERMISSIONS_PATH), "PERMISSIONS.md must be committed (source of truth for scopes.json)"


def test_scopes_json_matches_permissions_md_with_no_orphan_either_direction():
    expected = _scopes_and_roles_from_permissions_md()
    actual = {entry["scope"] for entry in load_scope_manifest()}
    missing_from_manifest = expected - actual
    orphans_in_manifest = actual - expected
    assert not missing_from_manifest, f"in PERMISSIONS.md but not scopes.json: {missing_from_manifest}"
    assert not orphans_in_manifest, f"in scopes.json but not PERMISSIONS.md: {orphans_in_manifest}"


# ---- emit_identity_audit (Task 2 -- the [identity] stdout line) ----

def test_emit_identity_audit_prints_exactly_one_identity_line(capsys):
    resolved = {"provider": lambda: "SHOULD-NEVER-BE-CALLED", "identity": "servicePrincipal",
                "note": "identity resolved for the primary data path"}
    emit_identity_audit(resolved)

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("[identity] ")
    rec = json.loads(lines[0][len("[identity] "):])
    assert rec == {"runIdentity": "servicePrincipal", "note": "identity resolved for the primary data path"}


def test_emit_identity_audit_never_prints_a_token(capsys):
    resolved = {"provider": lambda: "SUPER-SECRET-TOKEN-XYZ", "identity": "user",
                "note": "identity resolved for the primary data path"}
    emit_identity_audit(resolved)

    captured = capsys.readouterr()
    assert "SUPER-SECRET-TOKEN-XYZ" not in captured.out

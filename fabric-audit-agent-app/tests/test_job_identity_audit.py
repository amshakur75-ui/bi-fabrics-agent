"""Phase 5.3 Task 2 -- label-only identity audit wiring at the primary sweep path.

``run_unified_job`` (the deployed job: pyproject fabric-audit-job -> job_main -> run_unified_job)
emits ONE ``[identity]`` stdout line naming the run's primary-path identity. This is LABEL-ONLY:
the resolved provider is never threaded into the six existing SP token-construction sites, and no
token is ever acquired just to produce the label. Everything here is offline/deterministic --
``clients.build_entra_token_provider`` is monkeypatched to a fake so no real MSAL/network call can
happen even if ``resolve_identity``'s default (non-injected) path is exercised, and the collector
is monkeypatched to a fake so the sweep itself makes no real REST calls either.
"""
import json

import pytest

from fabric_audit_agent import job as job_mod
from fabric_audit_agent.adapters import clients as clients_mod

_SP_ENV = {
    "FABRIC_TENANT_ID": "tenant-1",
    "FABRIC_CLIENT_ID": "client-1",
    "FABRIC_CLIENT_SECRET": "SENTINEL-SECRET-XYZ",
}


def _fake_provider_factory(tenant_id, client_id, client_secret, scope=None):
    return lambda: "FAKE-TOKEN-NEVER-ACQUIRED"


def _fake_collector():
    return {"collect": lambda: {"capacity": {"peakCuPct": 10}}}


def test_run_unified_job_emits_identity_line_with_service_principal(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(clients_mod, "build_entra_token_provider", _fake_provider_factory)
    monkeypatch.setattr(job_mod, "build_collector_from_env", lambda env, window=None: _fake_collector())

    out_dir = tmp_path / "out"
    env = dict(_SP_ENV)
    env.update({"FABRIC_OUT_DIR": str(out_dir), "AUDIT_HISTORY_PATH": str(tmp_path / "h.json")})

    envelope = job_mod.run_unified_job(env=env, delivery={"deliver": lambda e: None})

    assert envelope["success"] is True
    captured = capsys.readouterr()
    identity_lines = [ln for ln in captured.out.splitlines() if ln.startswith("[identity] ")]
    assert len(identity_lines) == 1, f"expected exactly one [identity] line, got: {identity_lines}"
    rec = json.loads(identity_lines[0][len("[identity] "):])
    assert rec["runIdentity"] == "servicePrincipal"
    assert "note" in rec and isinstance(rec["note"], str) and rec["note"]


def test_run_unified_job_identity_line_never_leaks_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(clients_mod, "build_entra_token_provider", _fake_provider_factory)
    monkeypatch.setattr(job_mod, "build_collector_from_env", lambda env, window=None: _fake_collector())

    out_dir = tmp_path / "out"
    env = dict(_SP_ENV)
    env.update({"FABRIC_OUT_DIR": str(out_dir), "AUDIT_HISTORY_PATH": str(tmp_path / "h2.json")})

    job_mod.run_unified_job(env=env, delivery={"deliver": lambda e: None})

    captured = capsys.readouterr()
    identity_lines = [ln for ln in captured.out.splitlines() if ln.startswith("[identity] ")]
    assert len(identity_lines) == 1
    assert "SENTINEL-SECRET-XYZ" not in identity_lines[0]
    assert "tenant-1" not in identity_lines[0]
    assert "FAKE-TOKEN-NEVER-ACQUIRED" not in identity_lines[0]


def test_run_unified_job_csv_only_skips_identity_line_when_sp_unconfigured(tmp_path, capsys):
    # No-permission CSV deployment: SP env absent -- there is no primary-path identity to label
    # yet, so the sweep must not crash and must not emit a false/empty [identity] line.
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,50,F64\n", encoding="utf-8")
    out = tmp_path / "out"
    env = {"FABRIC_CSV_PATHS": str(cap), "FABRIC_OUT_DIR": str(out),
           "AUDIT_HISTORY_PATH": str(tmp_path / "h3.json")}

    envelope = job_mod.run_unified_job(env=env, delivery={"deliver": lambda e: None})

    assert envelope["success"] is True
    captured = capsys.readouterr()
    assert not [ln for ln in captured.out.splitlines() if ln.startswith("[identity] ")]


def test_identity_resolution_helper_swallows_missing_sp_config():
    # Direct unit check of the wiring helper's tolerant behavior (no stdout assertion here).
    assert job_mod._emit_identity_audit({}) is None

"""No-permission CSV Databricks sweep (job.run_csv_job) — offline."""
import json

import pytest

from fabric_audit_agent.job import (
    run_csv_job, _csv_paths_from_env, build_collector_from_env, run_unified_job,
)


def test_csv_paths_from_env_splits_on_comma_and_semicolon():
    assert _csv_paths_from_env({"FABRIC_CSV_PATHS": "a.csv; b.csv ,c.csv"}) == ["a.csv", "b.csv", "c.csv"]
    assert _csv_paths_from_env({}) == []


def test_run_csv_job_requires_paths():
    with pytest.raises(RuntimeError):
        run_csv_job(csv_paths=[], env={})


def test_run_csv_job_writes_outputs_and_delivers(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,96,F64\n", encoding="utf-8")
    out = tmp_path / "out"
    delivered = {}
    env = {"AUDIT_HISTORY_PATH": str(tmp_path / "history.json")}   # keep the store inside tmp

    envelope = run_csv_job(csv_paths=[str(cap)], out_dir=str(out), env=env,
                           delivery={"deliver": lambda e: delivered.update(e)})

    assert envelope["success"] is True
    assert isinstance(envelope["data"]["verdict"]["decision"], str) and envelope["data"]["verdict"]["decision"]
    assert (out / "latest.json").exists() and (out / "report.md").exists()
    saved = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert saved["data"]["healthScore"]["overall"] == envelope["data"]["healthScore"]["overall"]
    assert (out / "report.md").read_text(encoding="utf-8").strip()   # non-empty report
    assert delivered   # delivery port was called


# ---- unified job (config-driven; CSV now, live sources auto-included later) ----
def test_build_collector_from_env_csv_only(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,96,F64\n", encoding="utf-8")
    facts = build_collector_from_env({"FABRIC_CSV_PATHS": str(cap)})["collect"]()
    assert facts["capacity"]["peakCuPct"] == 96


def test_build_collector_from_env_none_configured_raises():
    with pytest.raises(RuntimeError):
        build_collector_from_env({})


def test_run_unified_job_csv_path(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,96,F64\n", encoding="utf-8")
    out = tmp_path / "out"
    delivered = {}
    env = {"FABRIC_CSV_PATHS": str(cap), "FABRIC_OUT_DIR": str(out),
           "AUDIT_HISTORY_PATH": str(tmp_path / "h.json")}
    envelope = run_unified_job(env=env, delivery={"deliver": lambda e: delivered.update(e)})
    assert envelope["success"] is True
    assert (out / "report.md").exists() and (out / "latest.json").exists()
    assert delivered

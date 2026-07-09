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


# ---- _write_outputs egress chokepoint (Phase 5.2 Task 2): written file gated, return stays full ----
def _secret_finding(key="capacity.throttle::TestCap"):
    return {
        "what": "leaked", "where": "w", "when": "", "why": "y", "impact": "i",
        "fix": ["do x"], "score": {"level": "Critical", "value": 90},
        "key": key, "clientSecret": "s3cr3t",
    }


def test_write_outputs_masks_planted_secret_in_written_latest_json(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,50,F64\n", encoding="utf-8")
    out = tmp_path / "out"
    reasoner = {"reason": lambda facts, flags: [_secret_finding()]}
    env = {"AUDIT_HISTORY_PATH": str(tmp_path / "history.json")}

    envelope = run_csv_job(csv_paths=[str(cap)], out_dir=str(out), env=env, reasoner=reasoner,
                           delivery={"deliver": lambda e: None})

    # returned envelope: full/unmasked
    assert envelope["data"]["findings"][0]["clientSecret"] == "s3cr3t"
    # written file: masked
    saved = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert saved["data"]["findings"][0]["clientSecret"] == "***"


def test_write_outputs_caps_over_budget_findings_and_discloses_in_written_summary(tmp_path):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,50,F64\n", encoding="utf-8")
    out = tmp_path / "out"
    findings = [{
        "what": "x", "where": "w", "when": "", "why": "y", "impact": "i",
        "fix": ["do x"], "score": {"level": "Info", "value": 10},
        "key": f"capacity.throttle::T{i}", "blob": "z" * 500,
    } for i in range(30)]
    reasoner = {"reason": lambda facts, flags: findings}
    env = {"AUDIT_HISTORY_PATH": str(tmp_path / "history2.json")}

    envelope = run_csv_job(csv_paths=[str(cap)], out_dir=str(out), env=env, reasoner=reasoner,
                           delivery={"deliver": lambda e: None})

    assert len(envelope["data"]["findings"]) == 30   # returned envelope uncapped/full
    saved = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert len(saved["data"]["findings"]) < 30
    assert "omitted" in saved["summary"]

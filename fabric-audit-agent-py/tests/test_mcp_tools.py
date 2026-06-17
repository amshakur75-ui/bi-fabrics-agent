"""MCP run_audit tool — real-vs-mock handler (offline)."""
from fabric_audit_agent.tools import create_tool_definitions


def test_tool_definition_shape():
    d = create_tool_definitions()[0]
    assert d["name"] == "run_audit" and "input_schema" in d and callable(d["handler"])


def test_run_audit_tool_runs_real_audit_when_csv_configured(tmp_path, monkeypatch):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,96,F64\n", encoding="utf-8")
    monkeypatch.setenv("FABRIC_CSV_PATHS", str(cap))
    monkeypatch.setenv("FABRIC_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("AUDIT_HISTORY_PATH", str(tmp_path / "h.json"))

    out = create_tool_definitions()[0]["handler"]()   # real path: CSV collector -> pipeline
    assert out["summary"] and out["verdict"]["decision"] and isinstance(out["findings"], list)
    assert (tmp_path / "out" / "report.md").exists()

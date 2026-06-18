"""MCP run_audit tool — real-vs-mock handler (offline)."""
from fabric_audit_agent.tools import create_tool_definitions


def test_tool_definition_shape():
    d = create_tool_definitions()[0]
    assert d["name"] == "run_audit" and "input_schema" in d and callable(d["handler"])


def test_run_audit_tool_runs_real_audit_when_csv_configured(tmp_path, monkeypatch):
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,96,F64\n", encoding="utf-8")
    monkeypatch.setenv("FABRIC_CSV_PATHS", str(cap))

    out = create_tool_definitions()[0]["handler"]()   # real path: CSV collector -> pipeline, read-and-return
    assert out["summary"] and out["verdict"]["decision"] and isinstance(out["findings"], list)
    # write-free: the tool returns the envelope and writes no files/Volumes (the App can't write /Volumes)
    assert not (tmp_path / "out").exists()

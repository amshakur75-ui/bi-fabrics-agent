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


def test_both_tools_are_defined():
    names = [d["name"] for d in create_tool_definitions()]
    assert "run_audit" in names and "list_workspaces" in names   # both must be exposed to the agent


def test_list_workspaces_no_source_is_explicit_not_mock(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    lw = next(d for d in create_tool_definitions() if d["name"] == "list_workspaces")
    out = lw["handler"]()
    # an inventory tool with no live source must say so — never invent a mock estate
    assert out["source"] == "none" and out["workspaces"] == [] and "note" in out


def test_investigation_tools_defined_with_schemas():
    by_name = {d["name"]: d for d in create_tool_definitions()}
    assert {"run_audit", "list_workspaces", "user_activity", "investigate_user",
            "investigate_capacity_spike"} <= set(by_name)
    assert by_name["investigate_user"]["input_schema"]["properties"]["user"]["type"] == "string"


def test_investigate_user_handler_abstains_offline(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "investigate_user")["handler"]
    out = h({"user": "anyone@co", "days": 30})   # no live source -> mock estate, user absent -> abstain
    assert out["abstained"] is True and "coverage" in out

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


def test_user_activity_labels_mock_source_offline(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_activity")["handler"]
    out = h()   # no live source -> mock estate; must label it mock, not pass it off as real
    assert out["source"] == "mock" and "coverage" in out


# ---------------------------------------------------------------------------
# Phase-3 Task-7: user_spike_history, spike_events, capacity_patterns tools
# ---------------------------------------------------------------------------

def _no_live(monkeypatch):
    """Clear all live-source env vars so the mock path is exercised."""
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)


def test_phase3_tools_are_defined():
    """All 3 new tools must be registered with correct input_schema."""
    by_name = {d["name"]: d for d in create_tool_definitions()}
    assert "user_spike_history" in by_name
    assert "spike_events" in by_name
    assert "capacity_patterns" in by_name

    ush = by_name["user_spike_history"]
    assert ush["input_schema"]["properties"]["user"]["type"] == "string"
    assert ush["input_schema"]["properties"]["days"]["type"] == "integer"
    assert "user" in ush["input_schema"]["required"]

    se = by_name["spike_events"]
    assert se["input_schema"]["properties"]["days"]["type"] == "integer"
    assert se["input_schema"]["properties"]["topN"]["type"] == "integer"

    cp = by_name["capacity_patterns"]
    assert cp["input_schema"]["properties"]["days"]["type"] == "integer"


def test_user_spike_history_handler_offline_shape(monkeypatch):
    """Offline: handler returns shaped output for a user in the mock events; source is 'mock'."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    out = h({"user": "alice@co", "days": 30})
    assert out["source"] == "mock"
    assert "user" in out
    assert "spikeCount" in out
    assert "spikes" in out
    assert isinstance(out["spikes"], list)
    assert "topItems" in out
    assert "byHour" in out
    assert "interactiveVsRefresh" in out


def test_user_spike_history_handler_abstains_unknown_user(monkeypatch):
    """Unknown user returns spikeCount=0 and an empty spikes list."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    out = h({"user": "nobody@nowhere.invalid", "days": 30})
    assert out["spikeCount"] == 0
    assert out["spikes"] == []


def test_spike_events_handler_offline_shape(monkeypatch):
    """Offline: spike_events returns a list of {user,item,ts,cuSeconds} dicts, labeled mock."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30, "topN": 3})
    assert out["source"] == "mock"
    assert "events" in out
    assert isinstance(out["events"], list)
    # Each event must carry the 4 required fields
    for ev in out["events"]:
        assert "user" in ev and "item" in ev and "ts" in ev and "cuSeconds" in ev
    # topN respected: at most 3
    assert len(out["events"]) <= 3


def test_spike_events_default_topN(monkeypatch):
    """Omitting topN defaults to 5."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30})
    assert len(out["events"]) <= 5


def test_spike_events_sorted_by_cu_desc(monkeypatch):
    """Events are ranked by cuSeconds descending (most expensive first)."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30, "topN": 10})
    evs = out["events"]
    if len(evs) >= 2:
        for i in range(len(evs) - 1):
            assert evs[i]["cuSeconds"] >= evs[i + 1]["cuSeconds"]


def test_capacity_patterns_handler_offline_shape(monkeypatch):
    """Offline: capacity_patterns returns a list of pattern dicts, labeled mock."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 30})
    assert out["source"] == "mock"
    assert "patterns" in out
    assert isinstance(out["patterns"], list)
    # Each pattern (if any) must have the expected fields
    for p in out["patterns"]:
        assert "windowStart" in p
        assert "activeUsers" in p
        assert "cuPeakPct" in p
        assert "narrative" in p


def test_existing_tools_unaffected_by_phase3(monkeypatch):
    """All pre-Phase-3 tools are still present and have correct schemas."""
    _no_live(monkeypatch)
    by_name = {d["name"]: d for d in create_tool_definitions()}
    pre_phase3 = {"run_audit", "list_workspaces", "user_activity",
                  "investigate_user", "investigate_capacity_spike"}
    assert pre_phase3 <= set(by_name)
    # investigate_user still requires "user"
    assert "user" in by_name["investigate_user"]["input_schema"]["required"]

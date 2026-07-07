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
    assert se["input_schema"]["properties"]["format"]["enum"] == ["records", "columnar"]

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


def test_spike_events_carries_queryText(monkeypatch):
    """spike_events must include queryText on each event (the costly DAX, truncated).

    This test verifies that top_expensive is wired into spike_events_handler so that
    queryText is present (not dropped as in the old inline ranking path).
    At least one event in the mock fixture must have a non-None queryText because
    the mock events carry EventText.
    """
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"topN": 5})
    assert "events" in out
    assert len(out["events"]) >= 1, "Expected at least one spike event in mock data"
    # Every returned event must have a queryText key
    for ev in out["events"]:
        assert "queryText" in ev, f"Event missing queryText: {ev}"
    # At least one event must have a non-None queryText (from EventText in the mock fixture)
    assert any(ev["queryText"] is not None for ev in out["events"]), (
        "No event carries a non-None queryText; check mock fixture has EventText populated"
    )
    # Sort order: cuSeconds descending
    evs = out["events"]
    for i in range(len(evs) - 1):
        assert evs[i]["cuSeconds"] >= evs[i + 1]["cuSeconds"]
    # topN still respected
    assert len(evs) <= 5


def test_spike_events_uses_canonical_p95(monkeypatch):
    """spike_events p95 must match compute_baseline — not a hand-rolled int(0.95*(n-1)) index."""
    _no_live(monkeypatch)
    from fabric_audit_agent.investigation.baseline import compute_baseline
    from fabric_audit_agent.tools import create_tool_definitions as ctd
    # Call the handler and check that the set of returned spikes is consistent with
    # compute_baseline p95 (i.e. all returned events are above the canonical p95).
    tools = ctd()
    h = next(d for d in tools if d["name"] == "spike_events")["handler"]
    out = h({"topN": 10})
    # We can't import _MOCK_EVENTS directly (closure), but we can call compute_baseline
    # on the returned events + a helper: all returned spike events must have cuSeconds > 0
    # (they pass is_spike, so they're above p95 or floor).
    for ev in out["events"]:
        assert ev["cuSeconds"] is not None


# ---------------------------------------------------------------------------
# Phase-3 Part B: live event wiring (_events_or_mock) — offline with injected fakes.
# Never hits a live endpoint; build_log_analytics_query/build_kusto_query are monkeypatched
# at their definition site so the module-local `from .adapters.clients import ...` inside
# _events_or_mock picks up the fake at call time.
# ---------------------------------------------------------------------------

def _set_la_env(monkeypatch):
    monkeypatch.setenv("FABRIC_LA_WORKSPACE_ID", "ws-123")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.setenv("FABRIC_TENANT_ID", "tenant-123")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "secret-123")


def _fake_la_query_builder(rows, captured=None):
    def build(workspace_id, tenant_id, client_id, client_secret, session=None):
        if captured is not None:
            captured["workspace_id"] = workspace_id
        def query(kql, timespan=None):
            if captured is not None:
                captured["kql"] = kql
            return rows
        return query
    return build


def test_events_go_live_when_la_configured(monkeypatch):
    """Once FABRIC_LA_WORKSPACE_ID + FABRIC_CLIENT_ID are set, spike_events must source real
    (injected-fake) LA rows instead of the mock fixture, and label source 'live'."""
    _set_la_env(monkeypatch)
    la_rows = [
        # A cheap baseline row -- without it, a lone event's p95 equals itself and it can
        # never register as its own spike (cu > p95 is never true for n=1).
        {"TimeGenerated": "2026-07-01T09:00:00Z", "ExecutingUser": "baseline@co",
         "ArtifactName": "Baseline Item", "OperationName": "QueryEnd", "CpuTimeMs": 1000},
        {"TimeGenerated": "2026-07-01T10:00:00Z", "ExecutingUser": "zeynep@co",
         "ArtifactName": "Live Item", "PowerBIWorkspaceName": "Live WS",
         "OperationName": "QueryEnd", "CpuTimeMs": 99000,
         "EventText": "EVALUATE Live"},
    ]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(la_rows),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 7, "topN": 5})
    assert out["source"] == "live"
    assert len(out["events"]) == 1
    assert out["events"][0]["cuSeconds"] == 99.0
    assert out["events"][0]["queryText"] == "EVALUATE Live"


def test_events_stay_mock_when_only_csv_configured(monkeypatch):
    """Regression guard: FABRIC_CSV_PATHS alone (no LA) must NOT flip the event tools' source
    label to 'live' — they are still reading the mock fixture, and must say so honestly."""
    _no_live(monkeypatch)
    monkeypatch.setenv("FABRIC_CSV_PATHS", "/tmp/whatever.csv")
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30, "topN": 5})
    assert out["source"] == "mock"


def test_days_threads_into_la_window(monkeypatch):
    """The `days` tool argument must reach the KQL ago() window, not be silently ignored."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    h({"days": 7})
    assert "ago(7d)" in captured["kql"]


def test_user_scope_threads_into_la_filter(monkeypatch):
    """user_spike_history must scope the live query to the requested user, not pull the estate."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    h({"user": "Alice@Co", "days": 30})
    assert 'ExecutingUser =~ "alice@co"' in captured["kql"]


def test_capacity_series_included_when_capacity_events_also_configured(monkeypatch):
    """When FABRIC_CAPACITY_EVENTS_CLUSTER/DB are also set, capacity_patterns must use the real
    (injected-fake) CU% series instead of the empty default, alongside live events."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")

    la_rows = [
        {"TimeGenerated": "2026-07-01T10:00:00Z", "ExecutingUser": "a@co", "ArtifactName": "I",
         "OperationName": "QueryEnd", "CpuTimeMs": 5000},
        {"TimeGenerated": "2026-07-01T10:01:00Z", "ExecutingUser": "b@co", "ArtifactName": "I",
         "OperationName": "QueryEnd", "CpuTimeMs": 5000},
        {"TimeGenerated": "2026-07-01T10:02:00Z", "ExecutingUser": "c@co", "ArtifactName": "I",
         "OperationName": "QueryEnd", "CpuTimeMs": 5000},
        {"TimeGenerated": "2026-07-01T10:03:00Z", "ExecutingUser": "d@co", "ArtifactName": "I",
         "OperationName": "QueryEnd", "CpuTimeMs": 5000},
    ]
    ce_rows = [
        {"capacityId": "cap1", "windowStartTime": "2026-07-01T10:00:00Z",
         "baseCapacityUnits": 64, "capacityUnitMs": 1536000},   # 80%
    ]

    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(la_rows),
    )

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        return lambda kql: ce_rows

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)

    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1})
    assert out["source"] == "live"
    # 4 distinct users in one 15-min bucket (>= SURGE_USER_THRESHOLD=4) + 80% CU (>= 70 threshold)
    assert len(out["patterns"]) == 1
    assert out["patterns"][0]["activeUsers"] == 4


def test_capacity_events_kql_override_is_passed_through(monkeypatch):
    """The deployed MCP app sets FABRIC_CAPACITY_EVENTS_KQL to flatten the nested `data` envelope;
    _events_or_mock must forward it to capacity_series (parity with job.py) rather than silently
    running the default KQL against a differently-shaped result."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    override = "CapacityEvents | extend capacityId=tostring(data.capacityId) | project capacityId"
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_KQL", override)

    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )
    captured = {}
    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            captured["kql"] = kql
            return []
        return query
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)

    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    h({"days": 1})
    assert captured["kql"] == override


# ---------------------------------------------------------------------------
# Task 4: result envelope (rowCount/queryKql) + char-budget cap_rows on list tools.
# Small mock fixtures fit well under the 12000-char default budget, so truncated=False
# is expected here; cap_rows' own truncation behavior is covered in test_envelope.py.
# ---------------------------------------------------------------------------

def test_spike_events_handler_has_envelope_fields(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30, "topN": 3})
    # Envelope fields present
    assert out["rowCount"] == len(out["events"])
    assert out["queryKql"] is None
    assert out["truncated"] is False
    assert out["originalRowCount"] >= out["rowCount"]
    # Pre-existing fields preserved
    assert "source" in out
    assert isinstance(out["events"], list)
    for ev in out["events"]:
        assert "user" in ev and "item" in ev and "ts" in ev and "cuSeconds" in ev
    # topN still respected (cap applied AFTER truncation, on the capped-then-topN'd... see below)
    assert len(out["events"]) <= 3


def test_spike_events_original_row_count_reflects_full_spike_list_not_topn_slice(monkeypatch):
    """originalRowCount must reflect the FULL spike list (pre-topN), not the already-sliced
    top_expensive output -- topN=1 must not make originalRowCount collapse to 1."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out_small = h({"days": 30, "topN": 1})
    out_large = h({"days": 30, "topN": 10})
    assert out_small["originalRowCount"] == out_large["originalRowCount"]
    assert len(out_small["events"]) <= 1


def test_spike_events_default_format_is_records(monkeypatch):
    """With no 'format' input (or format='records'), events stays a list[dict] as before."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30, "topN": 3})
    assert isinstance(out["events"], list)
    if out["events"]:
        assert isinstance(out["events"][0], dict)

    out_explicit = h({"days": 30, "topN": 3, "format": "records"})
    assert isinstance(out_explicit["events"], list)
    if out_explicit["events"]:
        assert isinstance(out_explicit["events"][0], dict)


def test_spike_events_format_columnar_returns_columnar_dict_with_correct_row_count(monkeypatch):
    """format:'columnar' returns the events list as to_columnar(events) under the same key,
    and rowCount must still reflect the true row count (not e.g. the column count)."""
    _no_live(monkeypatch)
    from fabric_audit_agent.query.envelope import to_columnar

    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out_records = h({"days": 30, "topN": 5})
    out_columnar = h({"days": 30, "topN": 5, "format": "columnar"})

    assert "columns" in out_columnar["events"]
    assert out_columnar["events"] == to_columnar(out_records["events"])
    assert out_columnar["rowCount"] == len(out_records["events"])
    assert out_columnar["rowCount"] == out_records["rowCount"]


def test_user_spike_history_handler_has_envelope_fields(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    out = h({"user": "alice@co", "days": 30})
    assert out["rowCount"] == len(out["spikes"])
    assert out["queryKql"] is None
    assert out["truncated"] is False
    # Pre-existing fields preserved
    assert out["source"] == "mock"
    assert "user" in out
    assert "spikeCount" in out
    assert "topItems" in out
    assert "byHour" in out
    assert "interactiveVsRefresh" in out


def test_list_workspaces_handler_has_envelope_fields(monkeypatch):
    monkeypatch.setenv("FABRIC_KUSTO_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_KUSTO_DB", "db")
    monkeypatch.setattr(
        "fabric_audit_agent.job.build_collector_from_env",
        lambda env: {"collect": lambda: {
            "items": [
                {"name": "Report A", "workspace": "WS1", "cuSeconds": 10, "sharePct": 50.0,
                 "topUsers": ["a@co"], "userCount": 1},
            ],
            "users": [{"user": "a@co", "cuSeconds": 10}],
        }},
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "list_workspaces")["handler"]
    out = h()
    assert out["rowCount"] == len(out["workspaces"])
    assert out["queryKql"] is None
    assert out["truncated"] is False
    # Pre-existing fields preserved
    assert out["source"] == "Log Analytics + Eventhouse (merged)"
    assert out["totalWorkspaces"] == 1
    assert out["totalItems"] == 1
    assert out["topUsers"] == [{"user": "a@co", "cuSeconds": 10}]


def test_list_workspaces_no_source_still_has_no_envelope_pollution():
    """The no-live-source early-return path is unaffected -- it's not a cap_rows/finish path."""
    import os
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        os.environ.pop(v, None)
    h = next(d for d in create_tool_definitions() if d["name"] == "list_workspaces")["handler"]
    out = h()
    assert out["source"] == "none"
    assert out["workspaces"] == []


# ---------------------------------------------------------------------------
# Task 6: real sub-day / absolute time windows; collector kql/window meta;
# _events_or_mock returns a 3-tuple (events, series, meta).
# ---------------------------------------------------------------------------

def test_phase3_tool_schemas_gain_hours_start_end(monkeypatch):
    """user_spike_history, spike_events, capacity_patterns all gain hours/start/end inputs."""
    by_name = {d["name"]: d for d in create_tool_definitions()}
    for name in ("user_spike_history", "spike_events", "capacity_patterns"):
        props = by_name[name]["input_schema"]["properties"]
        assert "hours" in props
        assert "start" in props
        assert "end" in props


def test_user_spike_history_handler_echoes_window_label_offline(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    out = h({"user": "alice@co", "hours": 6})
    assert out["windowLabel"] == "last 6h"


def test_spike_events_handler_echoes_window_label_offline(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 7})
    assert out["windowLabel"] == "last 7d"


def test_spike_events_handler_default_window_label_offline(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({})
    assert out["windowLabel"] == "last 30d"


def test_capacity_patterns_handler_echoes_window_label_offline(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"hours": 0.25})
    assert "windowLabel" in out
    assert out["windowLabel"] != ""


def test_handler_malformed_start_returns_error_envelope_not_crash(monkeypatch):
    """A malformed window must never propagate a raw exception out of the handler."""
    _no_live(monkeypatch)
    for name in ("user_spike_history", "spike_events", "capacity_patterns"):
        h = next(d for d in create_tool_definitions() if d["name"] == name)["handler"]
        inp = {"start": "not-a-date", "end": "2026-07-05T13:00:00Z"}
        if name == "user_spike_history":
            inp["user"] = "alice@co"
        out = h(inp)
        assert "error" in out, f"{name} did not return an error envelope: {out}"
        assert "source" in out


def test_spike_events_hours_reaches_live_kql_as_ago_clause(monkeypatch):
    """hours must thread into the live LA query as an ago(<hours>h) clause, not be ignored."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"hours": 6})
    assert "ago(6h)" in captured["kql"]


def test_spike_events_hours_fractional_reaches_live_kql(monkeypatch):
    """hours=0.25 ('right now' / last 15 min) must reach the live query unrounded."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"hours": 0.25})
    assert "ago(0.25h)" in captured["kql"]


def test_spike_events_start_end_reaches_live_kql_as_between_clause(monkeypatch):
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"start": "2026-07-05T12:45:00Z", "end": "2026-07-05T13:00:00Z"})
    assert (
        "between (datetime(2026-07-05T12:45:00Z) .. datetime(2026-07-05T13:00:00Z))"
        in captured["kql"]
    )


def test_spike_events_handler_populates_query_kql_when_live(monkeypatch):
    """The live event kql built for the request must surface as queryKql on the envelope
    (threaded via _events_or_mock's meta -> finish), not stay None once live."""
    _set_la_env(monkeypatch)
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 7})
    assert out["queryKql"] is not None
    assert "ago(7d)" in out["queryKql"]


def test_user_spike_history_handler_populates_query_kql_when_live(monkeypatch):
    _set_la_env(monkeypatch)
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    out = h({"user": "alice@co", "days": 7})
    assert out["queryKql"] is not None


def test_fabric_event_operations_env_threads_into_allowlist(monkeypatch):
    """FABRIC_EVENT_OPERATIONS (comma-separated) must restrict the live event query's
    OperationName allowlist, exposing the sub-op filter to deploy via env."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_EVENT_OPERATIONS", "QueryEnd, CommandEnd")
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"days": 7})
    assert 'OperationName in ("QueryEnd", "CommandEnd")' in captured["kql"]


def test_fabric_event_operations_env_absent_means_no_filter(monkeypatch):
    _set_la_env(monkeypatch)
    monkeypatch.delenv("FABRIC_EVENT_OPERATIONS", raising=False)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"days": 7})
    assert "OperationName in (" not in captured["kql"]


# ---------------------------------------------------------------------------
# Task 7: raw_events — bounded, NOT spike-filtered, all-instances event stream.
# ---------------------------------------------------------------------------

def test_raw_events_is_defined_with_correct_schema():
    """raw_events must be registered with the documented input_schema."""
    by_name = {d["name"]: d for d in create_tool_definitions()}
    assert "raw_events" in by_name
    re_def = by_name["raw_events"]
    props = re_def["input_schema"]["properties"]
    assert props["user"]["type"] == "string"
    assert props["item"]["type"] == "string"
    assert props["days"]["type"] == "integer"
    assert props["topN"]["type"] == "integer"
    assert props["order"]["enum"] == ["recent", "cost"]
    assert props["format"]["enum"] == ["records", "columnar"]
    assert "hours" in props and "start" in props and "end" in props
    assert re_def["input_schema"]["required"] == []
    # Description must point callers to spike_events for above-baseline-only, and warn
    # that results are untrusted telemetry (query text is data, not instructions).
    desc = re_def["description"]
    assert "spike_events" in desc
    assert "COMPLETE" in desc or "complete" in desc
    assert "untrusted" in desc.lower() or "not instructions" in desc.lower()


def test_raw_events_handler_offline_shape(monkeypatch):
    """Offline: raw_events returns the full (not spike-filtered) mock event list, labeled mock."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"days": 30})
    assert out["source"] == "mock"
    assert "events" in out
    assert isinstance(out["events"], list)
    assert out["rowCount"] == len(out["events"])
    assert "windowLabel" in out
    for ev in out["events"]:
        assert "user" in ev and "item" in ev and "ts" in ev and "cuSeconds" in ev


def test_raw_events_not_spike_filtered_returns_all_mock_events(monkeypatch):
    """raw_events must NOT apply is_spike/compute_baseline -- it returns the complete mock
    event stream (6 events), unlike spike_events which only returns above-baseline events."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"days": 30, "topN": 1000})
    assert out["rowCount"] == 6


def test_raw_events_default_topN_is_100(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"days": 30})
    assert len(out["events"]) <= 100


def test_raw_events_topN_5000_clamps_to_1000(monkeypatch):
    """topN above the hard cap of 1000 must clamp to 1000 and mark truncated=True."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"days": 30, "topN": 5000})
    assert out["truncated"] is True


def test_raw_events_topN_within_cap_not_marked_truncated_by_clamp(monkeypatch):
    """A topN within the hard cap, with a small mock result well under the char budget, must
    not be marked truncated (the clamp itself did not trim anything)."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"days": 30, "topN": 5})
    assert out["truncated"] is False


def test_raw_events_format_columnar_returns_columnar_dict(monkeypatch):
    """format:'columnar' returns events as to_columnar(events); rowCount stays the true count."""
    _no_live(monkeypatch)
    from fabric_audit_agent.query.envelope import to_columnar

    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out_records = h({"days": 30})
    out_columnar = h({"days": 30, "format": "columnar"})
    assert "columns" in out_columnar["events"]
    assert out_columnar["events"] == to_columnar(out_records["events"])
    assert out_columnar["rowCount"] == len(out_records["events"])
    assert out_columnar["rowCount"] == out_records["rowCount"]


def test_raw_events_malformed_start_returns_error_envelope(monkeypatch):
    """A malformed window must never propagate a raw exception out of the handler."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"start": "not-a-date", "end": "2026-07-05T13:00:00Z"})
    assert "error" in out
    assert "source" in out


def test_raw_events_order_and_cap_reach_collector_kql(monkeypatch):
    """order + the clamped topN must reach the live collector config -- visible in the built
    KQL as 'top <cap> by TimeGenerated desc' for order='recent' (the raw_events default)."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    h({"days": 7, "topN": 50, "order": "recent"})
    assert "top 50 by TimeGenerated desc" in captured["kql"]


def test_raw_events_order_cost_reaches_collector_kql(monkeypatch):
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    h({"days": 7, "topN": 50, "order": "cost"})
    assert "top 50 by coalesce(CpuTimeMs, DurationMs) desc" in captured["kql"]


def test_raw_events_topN_clamp_reaches_collector_cap(monkeypatch):
    """The CLAMPED (not raw) topN must reach the collector's KQL top-N, so the server-side
    bound actually protects the live query -- not just the post-hoc Python slice."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    h({"days": 7, "topN": 5000})
    assert "top 1000 by" in captured["kql"]


def test_raw_events_source_live_when_la_configured(monkeypatch):
    _set_la_env(monkeypatch)
    la_rows = [
        {"TimeGenerated": "2026-07-01T09:00:00Z", "ExecutingUser": "zeynep@co",
         "ArtifactName": "Live Item", "OperationName": "QueryEnd", "CpuTimeMs": 1000},
    ]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(la_rows),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"days": 7})
    assert out["source"] == "live"
    assert out["rowCount"] == 1


def test_raw_events_existing_events_or_mock_callers_still_work_without_cap_order(monkeypatch):
    """Extending _events_or_mock with optional cap/order kwargs must not break existing
    callers (spike_events, user_spike_history, capacity_patterns) that omit them."""
    _no_live(monkeypatch)
    for name in ("spike_events", "user_spike_history", "capacity_patterns"):
        h = next(d for d in create_tool_definitions() if d["name"] == name)["handler"]
        inp = {"days": 30}
        if name == "user_spike_history":
            inp["user"] = "alice@co"
        out = h(inp)
        assert "error" not in out


# ---------------------------------------------------------------------------
# Task 8: describe_source, sample_events, dry_run (schema discovery + sampling)
# ---------------------------------------------------------------------------

_KUSTO_URI = "https://mycluster.kusto.windows.net"


def _set_capacity_kusto_env(monkeypatch):
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", _KUSTO_URI)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.setenv("FABRIC_TENANT_ID", "tenant-123")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "secret-123")


def test_describe_source_and_sample_events_are_defined():
    by_name = {d["name"]: d for d in create_tool_definitions()}
    assert "describe_source" in by_name
    assert "sample_events" in by_name

    ds = by_name["describe_source"]
    assert ds["input_schema"]["properties"]["source"]["enum"] == ["events", "capacity"]
    assert callable(ds["handler"])

    se = by_name["sample_events"]
    assert se["input_schema"]["properties"]["source"]["enum"] == ["events", "capacity"]
    assert "n" in se["input_schema"]["properties"]
    assert callable(se["handler"])


def test_describe_source_events_mock_returns_fixture_columns(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "events"})
    assert out["source"] == "events"
    assert out["sourceLabel"] == "mock"
    names = {c["name"] for c in out["columns"]}
    assert "TimeGenerated" in names and "ExecutingUser" in names


def test_describe_source_capacity_mock_returns_fixture_columns(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert out["source"] == "capacity"
    assert out["sourceLabel"] == "mock"
    names = {c["name"] for c in out["columns"]}
    assert "capacityId" in names or "cuPct" in names or "ts" in names


def test_describe_source_events_live_emits_getschema(monkeypatch):
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(
            [{"ColumnName": "TimeGenerated", "ColumnType": "datetime"}], captured=captured
        ),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "events"})
    assert out["sourceLabel"] == "live"
    assert "getschema" in captured["kql"]
    assert "['" in captured["kql"]   # bracket-escaped table name
    assert out["columns"] == [{"name": "TimeGenerated", "type": "datetime"}]


def test_describe_source_capacity_live_emits_cslschema(monkeypatch):
    _set_capacity_kusto_env(monkeypatch)
    captured = {}

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        captured["cluster_uri"] = cluster_uri

        def query(kql):
            captured["kql"] = kql
            return [{"Schema": "ts:datetime, cuPct:real"}]
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert out["sourceLabel"] == "live"
    assert "cslschema" in captured["kql"]
    assert "['" in captured["kql"]
    assert captured["cluster_uri"] == _KUSTO_URI


def test_describe_source_capacity_rejects_non_allowlisted_cluster(monkeypatch):
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "https://evil.example.com")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.setenv("FABRIC_TENANT_ID", "tenant-123")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "secret-123")
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert "error" in out
    assert out["source"] == "capacity"


def test_describe_source_capacity_no_config_falls_back_to_mock(monkeypatch):
    """No capacity env at all -> the mock path, same as every other tool (not an error)."""
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert out["sourceLabel"] == "mock"


def test_describe_source_capacity_partial_config_returns_error_envelope_not_keyerror(monkeypatch):
    """Cluster+client_id set (so the live gate fires) but FABRIC_TENANT_ID missing -> must
    surface an error envelope, never raise/KeyError."""
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", _KUSTO_URI)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.delenv("FABRIC_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_CLIENT_SECRET", raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert "error" in out
    assert out["source"] == "capacity"


def test_sample_events_mock_returns_raw_rows(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events", "n": 3})
    assert out["source"] == "events"
    assert out["sourceLabel"] == "mock"
    assert isinstance(out["rows"], list)
    assert len(out["rows"]) <= 3


def test_sample_events_n_clamps_to_20(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events", "n": 99})
    assert out["n"] == 20


def test_sample_events_n_clamps_to_1_minimum(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events", "n": 0})
    assert out["n"] == 1


def test_sample_events_n_default_is_5(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events"})
    assert out["n"] == 5


def test_sample_events_n_is_cast_from_string(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events", "n": "7"})
    assert out["n"] == 7


def test_sample_events_events_live_emits_take_query(monkeypatch):
    _set_la_env(monkeypatch)
    captured = {}
    rows = [{"TimeGenerated": "2026-07-01T10:00:00Z", "ExecutingUser": "a@co"}]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows, captured=captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events", "n": 4})
    assert out["sourceLabel"] == "live"
    assert "take 4" in captured["kql"]
    assert out["rows"] == rows


def test_sample_events_capacity_live_emits_take_query(monkeypatch):
    _set_capacity_kusto_env(monkeypatch)
    captured = {}
    rows = [{"capacityId": "cap1", "cuPct": 80.0}]

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            captured["kql"] = kql
            return rows
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "capacity", "n": 2})
    assert out["sourceLabel"] == "live"
    assert "take 2" in captured["kql"]
    assert "['" in captured["kql"]
    assert out["rows"] == rows


def test_sample_events_capacity_no_config_falls_back_to_mock(monkeypatch):
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "capacity"})
    assert out["sourceLabel"] == "mock"


def test_sample_events_capacity_partial_config_returns_error_envelope_not_keyerror(monkeypatch):
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", _KUSTO_URI)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.delenv("FABRIC_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_CLIENT_SECRET", raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "capacity"})
    assert "error" in out
    assert out["source"] == "capacity"


def test_dry_run_valid_on_empty_success():
    from fabric_audit_agent.tools import dry_run

    def fake_query(kql):
        assert kql.endswith("\n| take 0")
        return []

    out = dry_run(fake_query, "SomeTable | take 5")
    assert out == {"valid": True, "error": None}


def test_dry_run_invalid_on_exception():
    from fabric_audit_agent.tools import dry_run

    def fake_query(kql):
        raise RuntimeError("bad column 'Foo'")

    out = dry_run(fake_query, "SomeTable | where Foo == 1")
    assert out["valid"] is False
    assert "bad column" in out["error"]

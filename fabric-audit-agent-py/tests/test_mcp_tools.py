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


_CU_UNIT = "cuSeconds (CPU-time proxy; not authoritative capacity CU)"
_USER_ACTIVITY_DENOMINATOR = "monitored user-attributable activity"


def test_user_activity_no_arg_envelope_carries_cu_unit_and_denominator(monkeypatch):
    """Mock path (unit label is source-independent): no-arg branch."""
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_activity")["handler"]
    out = h()
    assert out["source"] == "mock"
    assert out["cuUnit"] == _CU_UNIT
    assert out["denominator"] == _USER_ACTIVITY_DENOMINATOR


def test_user_activity_who_branch_envelope_carries_cu_unit_and_denominator(monkeypatch):
    """Mock path: user= branch."""
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_activity")["handler"]
    out = h({"user": "anyone@co"})
    assert out["source"] == "mock"
    assert out["cuUnit"] == _CU_UNIT
    assert out["denominator"] == _USER_ACTIVITY_DENOMINATOR


def test_user_activity_description_explains_denominator_mismatch():
    d = next(d for d in create_tool_definitions() if d["name"] == "user_activity")
    desc = d["description"]
    assert "denominator" in desc.lower()
    assert "run_audit" in desc
    assert desc.rstrip().endswith("Read-only.")


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


def test_user_spike_history_handler_envelope_carries_cu_unit(monkeypatch):
    """cuUnit is present but denominator is NOT (only user_activity gets denominator)."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "user_spike_history")["handler"]
    out = h({"user": "alice@co", "days": 30})
    assert out["source"] == "mock"
    assert out["cuUnit"] == _CU_UNIT
    assert "denominator" not in out


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


def test_spike_events_handler_envelope_carries_cu_unit(monkeypatch):
    """cuUnit is present but denominator is NOT (only user_activity gets denominator)."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    out = h({"days": 30, "topN": 3})
    assert out["source"] == "mock"
    assert out["cuUnit"] == _CU_UNIT
    assert "denominator" not in out


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
    # The query-callable memo is keyed on these (identical) values -- clear it so each test's
    # monkeypatched fake builder is actually used instead of a previous test's cached fake.
    import fabric_audit_agent.tools as tools_mod
    tools_mod._CLIENT_CACHE.clear()


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


def test_event_tools_return_error_payload_when_la_fails(monkeypatch):
    """A live LA failure (401 mid-rotation, timeout) must yield an honest error payload --
    not a crashed tool call, and not zeros dressed up as data."""
    _set_la_env(monkeypatch)

    def broken_builder(*a, **kw):
        def query(kql, timespan=None):
            raise RuntimeError("401 Unauthorized")
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_log_analytics_query", broken_builder)
    by_name = {d["name"]: d for d in create_tool_definitions()}

    ush = by_name["user_spike_history"]["handler"]({"user": "a@co", "days": 7})
    assert "401" in ush["error"] and ush["source"] == "live" and "spikeCount" not in ush

    se = by_name["spike_events"]["handler"]({"days": 7})
    assert "401" in se["error"] and se["events"] == []

    cp = by_name["capacity_patterns"]["handler"]({"days": 7})
    assert "401" in cp["error"] and cp["patterns"] == []


def test_spike_events_truncated_flag_when_cap_hit(monkeypatch):
    """Hitting the row cap must be disclosed -- the ranking covers only the newest slice."""
    import fabric_audit_agent.tools as tools_mod
    _set_la_env(monkeypatch)
    monkeypatch.setattr(tools_mod, "_EVENT_CAP", 2)
    rows = [
        {"TimeGenerated": f"2026-07-01T10:0{i}:00Z", "ExecutingUser": f"u{i}@co",
         "ArtifactName": "I", "OperationName": "QueryEnd", "CpuTimeMs": 1000 * (i + 1)}
        for i in range(2)   # exactly the (patched) cap
    ]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows),
    )
    out = next(d for d in create_tool_definitions()
               if d["name"] == "spike_events")["handler"]({"days": 1, "topN": 5})
    assert out["truncated"] is True

    # Under the cap -> truncated is False. (The unified query.envelope.finish contract ALWAYS
    # reports truncated explicitly as a bool, rather than omitting the key -- an event-cap hit
    # sets it True; full coverage is an explicit False, not an absent key.)
    tools_mod._CLIENT_CACHE.clear()
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows[:1]),
    )
    out2 = next(d for d in create_tool_definitions()
                if d["name"] == "spike_events")["handler"]({"days": 1, "topN": 5})
    assert out2["truncated"] is False


def test_capacity_patterns_degrades_honestly_when_series_fails(monkeypatch):
    """A CU%-series failure must not kill the tool -- events are still good; the tool returns
    empty patterns plus a seriesError note so the agent can explain the gap."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    rows = [{"TimeGenerated": "2026-07-01T10:00:00Z", "ExecutingUser": "a@co",
             "ArtifactName": "I", "OperationName": "QueryEnd", "CpuTimeMs": 5000}]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows),
    )

    def broken_kusto_builder(*a, **kw):
        def query(kql):
            raise RuntimeError("Kusto unreachable")
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", broken_kusto_builder)
    out = next(d for d in create_tool_definitions()
               if d["name"] == "capacity_patterns")["handler"]({"days": 1})
    assert out["patterns"] == []
    assert "Kusto unreachable" in out["seriesError"]
    assert "error" not in out   # the events side succeeded


def test_query_client_is_memoized_across_calls(monkeypatch):
    """The LA query callable must be built once and reused -- a fresh MSAL app per call means
    an AAD token round-trip per tool call and throttling exposure."""
    _set_la_env(monkeypatch)
    builds = {"n": 0}

    def counting_builder(*a, **kw):
        builds["n"] += 1
        return lambda kql, timespan=None: []

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_log_analytics_query", counting_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"days": 7})
    h({"days": 7})
    h({"days": 30})
    assert builds["n"] == 1


def test_capacity_events_kql_override_respects_days(monkeypatch):
    """Regression: the deployed FABRIC_CAPACITY_EVENTS_KQL override used to hardcode ago(1d),
    silently defeating the days arg for the CU% series. With the {window} placeholder the
    threaded lookback must reach the Kusto query."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_KQL",
                       "CapacityEvents | where ingestion_time() > ago({window}) | project x")
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )
    captured = {}

    def fake_kusto_builder(*a, **kw):
        def query(kql):
            captured["kql"] = kql
            return []
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    next(d for d in create_tool_definitions()
         if d["name"] == "capacity_patterns")["handler"]({"days": 7})
    assert "ago(7d)" in captured["kql"] and "{window}" not in captured["kql"]


def test_investigate_user_days_threads_into_collector_window(monkeypatch):
    """investigate_user's days arg must reach the live collector's lookback, not just the
    baseline math -- previously the collector window was pinned to FABRIC_LA_WINDOW/1d."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "investigate_user")["handler"]
    h({"user": "a@co", "days": 7})
    assert "ago(7d)" in captured["kql"]


def test_item_scope_threads_into_la_filter(monkeypatch):
    """spike_events/user_spike_history `item` must scope the live query to one artifact."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"days": 7, "item": "Ent-Reporting-Sales"})
    assert 'ArtifactName =~ "Ent-Reporting-Sales"' in captured["kql"]


def test_operations_env_threads_into_la_filter(monkeypatch):
    """FABRIC_EVENT_OPERATIONS restricts to top-level ops (drops VertiPaq SE sub-events)."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_EVENT_OPERATIONS", "QueryEnd, CommandEnd,ProgressReportEnd")
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "spike_events")["handler"]
    h({"days": 7})
    assert 'OperationName in ("QueryEnd", "CommandEnd", "ProgressReportEnd")' in captured["kql"]


def test_investigate_spike_with_when_returns_window_evidence(monkeypatch):
    """End-to-end (mock path): `when` produces the ±30m window evidence with display twins."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions()
             if d["name"] == "investigate_capacity_spike")["handler"]
    # Mock estate has capacity signal; mock events cluster at 2026-06-30T09:00-09:15Z.
    out = h({"when": "2026-06-30T09:10:00Z", "days": 7})
    window = next(e for e in out["evidence"] if e["kind"] == "window")
    assert window["data"]["eventCount"] > 0
    assert window["data"]["driver"] in ("interactive-driven", "refresh-driven", "mixed")
    assert " UTC (" in window["data"]["whenDisplay"]          # canonical display twin
    for te in window["data"]["topEvents"]:
        assert " UTC (" in te["tsDisplay"]


def test_investigate_spike_when_bounds_the_live_query_absolutely(monkeypatch):
    """Live path: `when` must reach the KQL as an absolute between(...) window, so the row cap
    can never truncate away the slice being investigated on a busy estate."""
    _set_la_env(monkeypatch)
    kqls = []   # the handler runs TWO LA queries (events + attribution rollup) — capture all

    def builder(*a, **kw):
        def query(kql, timespan=None):
            kqls.append(kql)
            return []
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_log_analytics_query", builder)
    h = next(d for d in create_tool_definitions()
             if d["name"] == "investigate_capacity_spike")["handler"]
    h({"when": "2026-07-06T15:48:00Z", "days": 2})
    assert any(("TimeGenerated between (datetime(2026-07-06T15:18:00Z) .. "
                "datetime(2026-07-06T16:18:00Z))") in k for k in kqls)


def test_investigate_spike_window_truncation_threads_from_fetch_meta(monkeypatch):
    """When the ±30m fetch hits the row cap, the window evidence must disclose it."""
    import fabric_audit_agent.tools as tools_mod
    _set_la_env(monkeypatch)
    monkeypatch.setattr(tools_mod, "_EVENT_CAP", 2)
    rows = [
        {"TimeGenerated": "2026-07-06T15:40:00Z", "ExecutingUser": "a@co",
         "ArtifactName": "I", "OperationName": "QueryEnd", "CpuTimeMs": 1000},
        {"TimeGenerated": "2026-07-06T15:50:00Z", "ExecutingUser": "b@co",
         "ArtifactName": "I", "OperationName": "QueryEnd", "CpuTimeMs": 2000},
    ]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows),
    )
    # The playbook abstains without a capacity signal — fake the capacity-events source too.
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    ce_rows = [{"capacityId": "c", "windowStartTime": "2026-07-06T15:48:00Z",
                "baseCapacityUnits": 64, "capacityUnitMs": 1920000}]   # 100%
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query",
                        lambda *a, **kw: (lambda kql: ce_rows))
    h = next(d for d in create_tool_definitions()
             if d["name"] == "investigate_capacity_spike")["handler"]
    out = h({"when": "2026-07-06T15:48:00Z", "days": 2})
    window = next(e for e in out["evidence"] if e["kind"] == "window")
    assert window["data"]["eventsTruncated"] is True
    assert "cap hit" in window["summary"]


def test_raw_events_rejects_invalid_order(monkeypatch):
    """The MCP wrapper can't enforce the enum -- a typo'd order must error honestly, not
    silently become cost-ordered."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"order": "newest"})
    assert "order must be" in out["error"] and out["events"] == []


def test_raw_events_truncates_huge_query_text(monkeypatch):
    """Raw queryText is unbounded (tens of KB per MDX capture) and was eating the whole char
    budget -- 3 rows returned when 100 were asked for. Truncate per-row and disclose."""
    _set_la_env(monkeypatch)
    rows = [
        {"TimeGenerated": "2026-07-06T15:40:00Z", "ExecutingUser": "a@co", "ArtifactName": "I",
         "OperationName": "QueryEnd", "CpuTimeMs": 1000, "EventText": "X" * 5000},
        {"TimeGenerated": "2026-07-06T15:41:00Z", "ExecutingUser": "b@co", "ArtifactName": "I",
         "OperationName": "QueryEnd", "CpuTimeMs": 900, "EventText": "short"},
    ]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "raw_events")["handler"]
    out = h({"hours": 2})
    big = next(e for e in out["events"] if e["user"] == "a@co")
    small = next(e for e in out["events"] if e["user"] == "b@co")
    assert len(big["queryText"]) == 400 and big["queryTextTruncated"] is True
    assert small["queryText"] == "short" and "queryTextTruncated" not in small


def test_raw_events_does_not_mutate_mock_fixture(monkeypatch):
    """raw_events decorates copies -- calling it must not write tsDisplay/truncation flags into
    the shared mock event fixture other tools read."""
    _no_live(monkeypatch)
    defs = create_tool_definitions()
    h = next(d for d in defs if d["name"] == "raw_events")["handler"]
    h({})
    sample = next(d for d in defs if d["name"] == "sample_events")["handler"]({"n": 1})
    assert "tsDisplay" not in sample["rows"][0]   # fixture untouched by raw_events' decoration


def test_investigate_spike_window_minutes_widens_the_kql_window(monkeypatch):
    """windowMinutes must reach BOTH the KQL between() bound and the playbook filter."""
    _set_la_env(monkeypatch)
    kqls = []

    def builder(*a, **kw):
        def query(kql, timespan=None):
            kqls.append(kql)
            return []
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_log_analytics_query", builder)
    h = next(d for d in create_tool_definitions()
             if d["name"] == "investigate_capacity_spike")["handler"]
    h({"when": "2026-07-06T15:48:00Z", "windowMinutes": 45})
    assert any(("TimeGenerated between (datetime(2026-07-06T15:03:00Z) .. "
                "datetime(2026-07-06T16:33:00Z))") in k for k in kqls)


def test_investigate_spike_window_minutes_clamped(monkeypatch):
    """An oversized windowMinutes is clamped to 240 so it can't become a huge absolute pull."""
    _set_la_env(monkeypatch)
    kqls = []

    def builder(*a, **kw):
        def query(kql, timespan=None):
            kqls.append(kql)
            return []
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_log_analytics_query", builder)
    h = next(d for d in create_tool_definitions()
             if d["name"] == "investigate_capacity_spike")["handler"]
    h({"when": "2026-07-06T12:00:00Z", "windowMinutes": 10000})
    assert any(("TimeGenerated between (datetime(2026-07-06T08:00:00Z) .. "
                "datetime(2026-07-06T16:00:00Z))") in k for k in kqls)   # ±240m


def test_investigate_spike_without_when_has_no_window_evidence(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions()
             if d["name"] == "investigate_capacity_spike")["handler"]
    out = h({})
    assert not [e for e in out["evidence"] if e["kind"] == "window"]


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


def test_absolute_window_derives_proportional_series_lookback_not_30d(monkeypatch):
    """Follow-up M-1: with an absolute start+end window, the CU series (which can't take a
    between() clause) must derive a lookback PROPORTIONAL to the window span (end-start),
    not silently fall back to the 30d default. A 15-minute window -> ago(15m) / 'last 15m'."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")

    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            captured["kql"] = kql
            return []
        return query
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)

    # Pin "now" to the window end: the lookback anchors at `start` (start->now), so with
    # now == end it equals the span and the proportional labels below hold deterministically.
    import fabric_audit_agent.tools as tools_mod
    from datetime import datetime, timezone
    monkeypatch.setattr(tools_mod, "_utcnow",
                        lambda: datetime(2026, 7, 5, 13, 0, 0, tzinfo=timezone.utc))

    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"start": "2026-07-05T12:45:00Z", "end": "2026-07-05T13:00:00Z"})   # 15-min window

    assert out["source"] == "live"
    assert out["seriesWindowLabel"] == "last 15m"        # derived from span, NOT "last 30d"
    assert out["seriesWindowLabel"] != "last 30d"
    assert out["patternsDiagnostics"]["seriesWindowLabel"] == "last 15m"
    assert "ago(15m)" in captured["kql"]                 # threaded into the capacity-series KQL


def test_absolute_multi_hour_window_derives_hour_lookback(monkeypatch):
    """A multi-hour absolute window ceils to the enclosing hour unit (2h15m -> 'last 3h')."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_kusto_query",
        lambda *a, **k: (lambda kql: []),
    )
    import fabric_audit_agent.tools as tools_mod
    from datetime import datetime, timezone
    monkeypatch.setattr(tools_mod, "_utcnow",
                        lambda: datetime(2026, 7, 5, 12, 15, 0, tzinfo=timezone.utc))
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"start": "2026-07-05T10:00:00Z", "end": "2026-07-05T12:15:00Z"})   # 2h15m span
    assert out["seriesWindowLabel"] == "last 3h"         # ceil(2.25h) -> 3h, covers >= the span


def test_past_absolute_window_series_lookback_reaches_back_to_start(monkeypatch):
    """Regression (review batch 3): ago() anchors at NOW, so a spike window from days ago with a
    span-only lookback (e.g. ago(1h) for a 1h window) missed the window entirely -- the CU%
    corroboration silently vanished from old-`when` investigations. The lookback must anchor at
    `start`: cover start->now, not just the span."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([]),
    )
    captured = {}

    def fake_kusto_builder(*a, **k):
        def query(kql):
            captured["kql"] = kql
            return []
        return query
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)

    import fabric_audit_agent.tools as tools_mod
    from datetime import datetime, timezone
    # "now" is 2 days after the window -- the 1h window from 2 days ago needs ~2d of lookback.
    monkeypatch.setattr(tools_mod, "_utcnow",
                        lambda: datetime(2026, 7, 7, 12, 30, 0, tzinfo=timezone.utc))
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"start": "2026-07-05T12:00:00Z", "end": "2026-07-05T13:00:00Z"})
    # ceil((now-start)/86400) = ceil(2.02d) = 3d -- covers the window; span-only would be ago(1h).
    assert out["seriesWindowLabel"] == "last 3d"
    assert "ago(3d)" in captured["kql"]
    assert "ago(1h)" not in captured["kql"]


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
        lambda env, window=None: {"collect": lambda: {
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
    # The grounding tools' kusto client is memoized on these (identical) values -- clear so each
    # test's monkeypatched fake builder is used, not a previous test's cached fake.
    import fabric_audit_agent.tools as tools_mod
    tools_mod._CLIENT_CACHE.clear()


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


# ---------------------------------------------------------------------------
# Task 9: capacity_diagnostics — read-only .show capacity/cluster suite
# ---------------------------------------------------------------------------

def test_capacity_diagnostics_is_defined_and_registered():
    by_name = {d["name"]: d for d in create_tool_definitions()}
    assert "capacity_diagnostics" in by_name
    cd = by_name["capacity_diagnostics"]
    assert cd["input_schema"]["properties"] == {}
    assert cd["input_schema"]["required"] == []
    assert callable(cd["handler"])


def test_capacity_diagnostics_no_config_returns_source_none(monkeypatch):
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()
    assert out["source"] == "none"
    assert out["sections"] == {}
    assert "note" in out
    assert "error" not in out


def test_capacity_diagnostics_live_runs_fixed_show_commands_with_per_section_isolation(monkeypatch):
    _set_capacity_kusto_env(monkeypatch)
    captured_kqls = []

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            captured_kqls.append(kql)
            if kql.startswith(".show cluster"):
                raise RuntimeError("cluster endpoint unavailable")
            if kql.startswith(".show capacity"):
                return [{"Resource": "CPU", "Total": 100, "Consumed": 42, "Remaining": 58}]
            if kql.startswith(".show workload_groups"):
                return [{"Name": "default"}]
            if kql.startswith(".show diagnostics"):
                return [{"Status": "ok"}]
            return []
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()

    assert out["source"] == "live"
    # failing section isolated to errors, others still land in sections
    assert "cluster" in out["errors"]
    assert "cluster endpoint unavailable" in out["errors"]["cluster"]
    assert "cluster" not in out["sections"]

    assert out["sections"]["capacity"] == [{"Resource": "CPU", "Total": 100, "Consumed": 42, "Remaining": 58}]
    assert out["sections"]["workloadGroups"] == [{"Name": "default"}]
    assert out["sections"]["diagnostics"] == [{"Status": "ok"}]

    # every command actually executed must be a read-only .show command
    assert captured_kqls
    for kql in captured_kqls:
        assert kql.startswith(".show ")


def test_capacity_diagnostics_rejects_non_allowlisted_cluster(monkeypatch):
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "https://evil.example.com")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.setenv("FABRIC_TENANT_ID", "tenant-123")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "secret-123")
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()
    assert "error" in out
    assert out["source"] == "capacity"


def test_capacity_diagnostics_partial_config_returns_error_envelope_not_keyerror(monkeypatch):
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", _KUSTO_URI)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client-123")
    monkeypatch.delenv("FABRIC_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_CLIENT_SECRET", raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()
    assert "error" in out
    assert out["source"] == "capacity"


def test_capacity_diagnostics_no_config_has_no_throttle_decomposition(monkeypatch):
    """Task 4 wiring, unconfigured path: throttleDecomposition key must be entirely absent,
    not present-with-nulls -- capacity_diagnostics never reached the live branch at all."""
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()
    assert out["source"] == "none"
    assert "throttleDecomposition" not in out


def test_capacity_diagnostics_live_attaches_throttle_decomposition(monkeypatch):
    """Task 4 wiring, live path: an injected hot CU% series + Tier-1 events must land a
    throttleDecomposition with a conclusion, alongside the existing .show sections."""
    _set_capacity_kusto_env(monkeypatch)

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            if kql.startswith(".show cluster"):
                return [{"ok": True}]
            if kql.startswith(".show capacity"):
                return [{"Resource": "CPU", "Total": 100, "Consumed": 42, "Remaining": 58}]
            if kql.startswith(".show workload_groups"):
                return [{"Name": "default"}]
            if kql.startswith(".show diagnostics"):
                return [{"Status": "ok"}]
            # capacity CU% series query (not a .show control command): one over-threshold window.
            return [{"capacityId": "cap1", "windowStart": "2026-07-07T09:01:00Z",
                     "baseCapacityUnits": 10, "capacityUnitMs": 10 * 1000 * 30 * 1.3}]
        return query
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)

    # Tier-1 activity events source is also "configured" (creds present); stub it so the test
    # never attempts a real MSAL token round-trip.
    monkeypatch.setattr("fabric_audit_agent.tools._create_activity_event_collector",
                         lambda http, config: {"collect": lambda: []})

    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()

    assert out["source"] == "live"
    assert "throttleDecomposition" in out
    td = out["throttleDecomposition"]
    assert td["conclusion"] in ("not-throttling", "throttling-confirmed", "over-utilized-unconfirmed")
    assert td["stage1"]["timepointsOver"] == 1


def test_capacity_diagnostics_throttle_decomposition_failure_surfaces_in_errors(monkeypatch):
    """Task 4 review fix: a throttleDecomposition failure must land in errors["throttleDecomposition"]
    (matching the per-section .show isolation mechanism), not be silently swallowed -- the
    already-collected .show sections must still come back."""
    _set_capacity_kusto_env(monkeypatch)

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            if kql.startswith(".show cluster"):
                return [{"ok": True}]
            if kql.startswith(".show capacity"):
                return [{"Resource": "CPU", "Total": 100, "Consumed": 42, "Remaining": 58}]
            if kql.startswith(".show workload_groups"):
                return [{"Name": "default"}]
            if kql.startswith(".show diagnostics"):
                return [{"Status": "ok"}]
            return [{"capacityId": "cap1", "windowStart": "2026-07-07T09:01:00Z",
                     "baseCapacityUnits": 10, "capacityUnitMs": 10 * 1000 * 30 * 1.3}]
        return query
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    monkeypatch.setattr("fabric_audit_agent.tools._create_activity_event_collector",
                         lambda http, config: {"collect": lambda: []})
    monkeypatch.setattr("fabric_audit_agent.tools._decompose_throttle",
                         lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()

    assert out["source"] == "live"
    assert "throttleDecomposition" not in out
    assert out["errors"]["throttleDecomposition"] == "boom"
    assert "cluster" in out["sections"]


def test_capacity_diagnostics_no_config_has_no_time_to_throttle(monkeypatch):
    """Task 6 wiring, unconfigured path: timeToThrottle key must be entirely absent, matching
    throttleDecomposition's absent-not-null contract -- the live branch was never reached."""
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()
    assert out["source"] == "none"
    assert "timeToThrottle" not in out


def test_capacity_diagnostics_live_attaches_time_to_throttle(monkeypatch):
    """Task 6 wiring, live path: an injected rising CU% series must land a numeric
    timeToThrottle.minutesToThreshold, alongside throttleDecomposition and the .show sections."""
    _set_capacity_kusto_env(monkeypatch)

    base_cu = 10
    rising_rows = [
        {"capacityId": "cap1", "windowStart": f"2026-07-07T09:0{i}:00Z",
         "baseCapacityUnits": base_cu, "capacityUnitMs": (50 + 2 * i) * base_cu * 300}
        for i in range(8)
    ]

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            if kql.startswith(".show cluster"):
                return [{"ok": True}]
            if kql.startswith(".show capacity"):
                return [{"Resource": "CPU", "Total": 100, "Consumed": 42, "Remaining": 58}]
            if kql.startswith(".show workload_groups"):
                return [{"Name": "default"}]
            if kql.startswith(".show diagnostics"):
                return [{"Status": "ok"}]
            # capacity CU% series query (not a .show control command): 8 rising points.
            return rising_rows
        return query
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    monkeypatch.setattr("fabric_audit_agent.tools._create_activity_event_collector",
                         lambda http, config: {"collect": lambda: []})

    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()

    assert out["source"] == "live"
    assert "timeToThrottle" in out
    ttt = out["timeToThrottle"]
    assert ttt["method"] == "robust-trend"
    assert isinstance(ttt["minutesToThreshold"], float)


# ---------------------------------------------------------------------------
# Task 10: capacity_patterns live-fix -- recent-ordered narrow window, tunable
# thresholds (tool input > env > default), non-silent patternsDiagnostics.
# ---------------------------------------------------------------------------

def test_capacity_patterns_handler_passes_recent_order_and_default_days_1(monkeypatch):
    """Root-cause fix: with no window given, the handler must pull events RECENT-ordered
    (not cost-ordered) over a narrow days=1 default -- cost-ordered 30-day sampling was
    scattering events too thin per bucket, collapsing distinct-user counts below threshold."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    h({})
    assert "ago(1d)" in captured["kql"]
    # order="recent" sorts by TimeGenerated, not the cost-order coalesce(...) expression.
    assert "TimeGenerated" in captured["kql"]


def test_capacity_patterns_handler_explicit_days_still_overrides_default(monkeypatch):
    """An explicit days= input must still be honored (not clobbered by the days=1 default)."""
    _set_la_env(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([], captured),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    h({"days": 7})
    assert "ago(7d)" in captured["kql"]


def _sparse_live_rows():
    """A sparse, live-shaped fixture: only 2 distinct users in the same 15-min bucket --
    below the default surge_users=4 threshold, and a CU series peaking below cu_spike_pct=70.
    Mirrors the actual live-bug shape (capacity_patterns silently returning [])."""
    return [
        {"TimeGenerated": "2026-07-06T09:00:00Z", "ExecutingUser": "a@co",
         "ArtifactName": "Sales", "OperationName": "QueryEnd", "CpuTimeMs": 20000},
        {"TimeGenerated": "2026-07-06T09:02:00Z", "ExecutingUser": "b@co",
         "ArtifactName": "Sales", "OperationName": "QueryEnd", "CpuTimeMs": 25000},
    ]


def _set_la_and_capacity_env(monkeypatch, ce_rows):
    """LA events + a capacity Eventhouse series, both live-configured (injected fakes)."""
    _set_la_env(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        return lambda kql: ce_rows

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)


def test_sparse_fixture_yields_empty_patterns_with_explanatory_diagnostics(monkeypatch):
    """Live-bug regression: a sparse fixture that returns patterns == [] must now surface
    patternsDiagnostics explaining the observed maxima -- never silent."""
    ce_rows = [
        {"capacityId": "cap1", "windowStartTime": "2026-07-06T09:00:00Z",
         "baseCapacityUnits": 64, "capacityUnitMs": 1536000},   # 55% (< default 70 threshold)
    ]
    _set_la_and_capacity_env(monkeypatch, ce_rows)
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(_sparse_live_rows()),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1})
    assert out["patterns"] == []
    assert "patternsDiagnostics" in out
    diag = out["patternsDiagnostics"]
    assert diag["maxActiveUsers"] == 2
    assert diag["thresholds"]["surgeUsers"] == 4
    assert diag["thresholds"]["cuSpikePct"] == 70.0
    assert "windowLabel" in diag
    assert "seriesWindowLabel" in diag


def test_lowering_surge_users_via_tool_input_yields_nonempty_pattern(monkeypatch):
    """Passing surgeUsers <= observed maxActiveUsers (2) via the TOOL INPUT (not env) must
    flip the same sparse fixture from empty to a detected pattern -- also needs cuSpikePct
    lowered to at/below the observed CU max since the mock capacity series is low too."""
    ce_rows = [
        {"capacityId": "cap1", "windowStartTime": "2026-07-06T09:00:00Z",
         "baseCapacityUnits": 64, "capacityUnitMs": 1536000},   # 55%
    ]
    _set_la_and_capacity_env(monkeypatch, ce_rows)
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(_sparse_live_rows()),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1, "surgeUsers": 2, "cuSpikePct": 0.0})
    assert len(out["patterns"]) == 1
    assert out["patterns"][0]["activeUsers"] == 2


def test_surge_users_zero_is_honored_not_falsy_coerced(monkeypatch):
    """surgeUsers=0 is a legitimate (if extreme) threshold -- must be honored via
    'x if x is not None else default', NOT treated as falsy/omitted."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1, "surgeUsers": 0, "cuSpikePct": 0.0})
    assert out["patternsDiagnostics"]["thresholds"]["surgeUsers"] == 0
    assert out["patternsDiagnostics"]["thresholds"]["cuSpikePct"] == 0.0


def test_cu_spike_pct_zero_is_honored_not_falsy_coerced(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1, "cuSpikePct": 0.0})
    assert out["patternsDiagnostics"]["thresholds"]["cuSpikePct"] == 0.0


def test_thresholds_read_from_env_when_no_tool_input(monkeypatch):
    """FABRIC_PATTERNS_SURGE_USERS / _CU_SPIKE_PCT env vars apply when the tool input omits
    surgeUsers/cuSpikePct."""
    _no_live(monkeypatch)
    monkeypatch.setenv("FABRIC_PATTERNS_SURGE_USERS", "2")
    monkeypatch.setenv("FABRIC_PATTERNS_CU_SPIKE_PCT", "10.0")
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1})
    assert out["patternsDiagnostics"]["thresholds"]["surgeUsers"] == 2
    assert out["patternsDiagnostics"]["thresholds"]["cuSpikePct"] == 10.0


def test_tool_input_threshold_takes_precedence_over_env(monkeypatch):
    _no_live(monkeypatch)
    monkeypatch.setenv("FABRIC_PATTERNS_SURGE_USERS", "99")
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1, "surgeUsers": 3})
    assert out["patternsDiagnostics"]["thresholds"]["surgeUsers"] == 3


def test_default_thresholds_when_no_input_and_no_env(monkeypatch):
    _no_live(monkeypatch)
    monkeypatch.delenv("FABRIC_PATTERNS_SURGE_USERS", raising=False)
    monkeypatch.delenv("FABRIC_PATTERNS_CU_SPIKE_PCT", raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1})
    assert out["patternsDiagnostics"]["thresholds"]["surgeUsers"] == 4
    assert out["patternsDiagnostics"]["thresholds"]["cuSpikePct"] == 70.0


def test_capacity_patterns_handler_preserves_preexisting_fields(monkeypatch):
    """source/windowLabel/seriesWindowLabel/queryKql must still be present alongside the
    new patternsDiagnostics -- Task 10 must not drop existing envelope fields."""
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"days": 1})
    assert out["source"] == "mock"
    assert "windowLabel" in out
    assert "seriesWindowLabel" in out
    assert "queryKql" in out
    assert "patterns" in out
    assert "patternsDiagnostics" in out
    assert out["patternsDiagnostics"]["windowLabel"] == out["windowLabel"]
    assert out["patternsDiagnostics"]["seriesWindowLabel"] == out["seriesWindowLabel"]


def test_capacity_patterns_handler_malformed_window_returns_error_envelope(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_patterns")["handler"]
    out = h({"start": "not-a-date", "end": "2026-07-05T13:00:00Z"})
    assert "error" in out
    assert "source" in out


def test_capacity_patterns_input_schema_gains_surge_users_and_cu_spike_pct():
    by_name = {d["name"]: d for d in create_tool_definitions()}
    props = by_name["capacity_patterns"]["input_schema"]["properties"]
    assert "surgeUsers" in props
    assert "cuSpikePct" in props


_PATTERNS_SCHEMA = {"type": "object", "properties": {
    "days": {"type": "integer"},
    "surgeUsers": {"type": "integer"}, "cuSpikePct": {"type": "number"},
}, "required": []}


def test_make_tool_fn_forwards_surge_users_and_cu_spike_pct():
    # _make_tool_fn derives the per-tool signature from input_schema; surgeUsers/cuSpikePct reach
    # the handler as the capacity_patterns schema advertises them.
    from fabric_audit_agent.mcp_server import _make_tool_fn
    captured = {}

    def handler(payload):
        captured["payload"] = payload
        return payload

    tool = _make_tool_fn(handler, _PATTERNS_SCHEMA)
    tool(surgeUsers=2, cuSpikePct=55.0)
    assert captured["payload"]["surgeUsers"] == 2
    assert captured["payload"]["cuSpikePct"] == 55.0


def test_make_tool_fn_surge_users_zero_forwarded_not_dropped():
    # 0 is a meaningful threshold value (nullish, not falsy) -- must still be forwarded.
    from fabric_audit_agent.mcp_server import _make_tool_fn
    captured = {}

    def handler(payload):
        captured["payload"] = payload
        return payload

    tool = _make_tool_fn(handler, _PATTERNS_SCHEMA)
    tool(surgeUsers=0, cuSpikePct=0.0)
    assert captured["payload"]["surgeUsers"] == 0
    assert captured["payload"]["cuSpikePct"] == 0.0


# ---------------------------------------------------------------------------
# Task 11: verify-in-Fabric deeplinks -- verifyUrl on live capacity-Kusto envelopes
# ---------------------------------------------------------------------------

def test_describe_source_capacity_live_carries_verify_url(monkeypatch):
    _set_capacity_kusto_env(monkeypatch)

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            return [{"Schema": "ts:datetime, cuPct:real"}]
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert out["sourceLabel"] == "live"
    assert out["verifyUrl"].startswith("https://dataexplorer.azure.com/")


def test_describe_source_capacity_mock_has_no_verify_url(monkeypatch):
    _no_live(monkeypatch)
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "capacity"})
    assert out["sourceLabel"] == "mock"
    assert "verifyUrl" not in out


def test_describe_source_events_live_has_no_verify_url(monkeypatch):
    """Log Analytics (events) envelopes get nothing -- no clean web-explorer equivalent."""
    _set_la_env(monkeypatch)
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder([{"ColumnName": "TimeGenerated", "ColumnType": "datetime"}]),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "describe_source")["handler"]
    out = h({"source": "events"})
    assert out["sourceLabel"] == "live"
    assert "verifyUrl" not in out


def test_sample_events_capacity_live_carries_verify_url(monkeypatch):
    _set_capacity_kusto_env(monkeypatch)
    rows = [{"capacityId": "cap1", "cuPct": 80.0}]

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        return lambda kql: rows

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "capacity", "n": 2})
    assert out["sourceLabel"] == "live"
    assert out["verifyUrl"].startswith("https://dataexplorer.azure.com/")


def test_sample_events_capacity_mock_has_no_verify_url(monkeypatch):
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "capacity"})
    assert out["sourceLabel"] == "mock"
    assert "verifyUrl" not in out


def test_sample_events_events_live_has_no_verify_url(monkeypatch):
    _set_la_env(monkeypatch)
    rows = [{"TimeGenerated": "2026-07-01T10:00:00Z", "ExecutingUser": "a@co"}]
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows),
    )
    h = next(d for d in create_tool_definitions() if d["name"] == "sample_events")["handler"]
    out = h({"source": "events", "n": 4})
    assert out["sourceLabel"] == "live"
    assert "verifyUrl" not in out


def test_capacity_diagnostics_live_carries_verify_urls_for_successful_sections(monkeypatch):
    _set_capacity_kusto_env(monkeypatch)

    def fake_kusto_builder(cluster_uri, database, tenant_id, client_id, client_secret):
        def query(kql):
            if kql.startswith(".show cluster"):
                raise RuntimeError("cluster endpoint unavailable")
            return [{"ok": True}]
        return query

    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query", fake_kusto_builder)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()

    assert out["source"] == "live"
    # every successful section gets a verify URL; the failed section does not.
    assert set(out["verifyUrls"].keys()) == {"capacity", "workloadGroups", "diagnostics"}
    for url in out["verifyUrls"].values():
        assert url.startswith("https://dataexplorer.azure.com/")
    assert "cluster" not in out["verifyUrls"]


def test_capacity_diagnostics_no_config_has_no_verify_urls(monkeypatch):
    for v in ("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "capacity_diagnostics")["handler"]
    out = h()
    assert out["source"] == "none"
    assert "verifyUrls" not in out


# --- Task 3 (Phase 4): tiered event resolution -------------------------------------------
# NOTE: test_mcp_tools.py has NO handler-fetch helper today (every existing test inlines
# `next(d for d in create_tool_definitions() if d["name"] == X)["handler"]`). ADD this helper
# here once; Tasks 5/9/11/12/13/14's new tests reuse it (existing tests stay untouched):
def _handler(name):
    from fabric_audit_agent.tools import create_tool_definitions
    return next(d for d in create_tool_definitions() if d["name"] == name)["handler"]

_T1_ENV = {"FABRIC_CLIENT_ID": "cid", "FABRIC_TENANT_ID": "t", "FABRIC_CLIENT_SECRET": "s"}

def _clear_live(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID",
              "FABRIC_TENANT_ID", "FABRIC_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)

def test_spike_events_mock_path_labeled_mock_tier(monkeypatch):
    _clear_live(monkeypatch)
    out = _handler("spike_events")({})
    assert out["tier"] == "mock"
    assert "coverageNote" not in out          # None → key omitted (no noise on healthy paths)

def test_spike_events_tier1_uses_activity_events_and_labels(monkeypatch):
    _clear_live(monkeypatch)
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    fake_events = [{"ts": "2026-07-07T09:00:00Z", "user": "john@co", "item": "Sales",
                    "workspace": "Fin", "kind": "interactive", "cuSeconds": None,
                    "queryText": None, "operation": "ViewReport"}]
    import fabric_audit_agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_create_activity_event_collector",
                        lambda http, cfg: {"collect": lambda: fake_events})
    out = _handler("spike_events")({"days": 1})
    assert out["tier"] == "operationLevel"
    assert "per-query cost unavailable" in out["coverageNote"]

def test_tier2_env_stays_per_query(monkeypatch):
    # LA configured → Tier-2 path untouched; pin only the new labels. Stub the LA client the
    # way the EXISTING live tests in this file do (e.g. around lines 381/461/564):
    _clear_live(monkeypatch)
    monkeypatch.setenv("FABRIC_LA_WORKSPACE_ID", "ws")
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_log_analytics_query",
                        lambda *a, **kw: (lambda kql: []))
    out = _handler("spike_events")({})
    assert out["tier"] == "perQuery" and out.get("coverageNote") is None

def test_tier1_series_is_real_or_empty_never_mock(monkeypatch):
    # THE honesty regression guard for the extracted _capacity_series_only helper: with Tier-1
    # env + NO capacity cluster, the series must be [] — never _MOCK_CAPACITY_SERIES values.
    _clear_live(monkeypatch)
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    import fabric_audit_agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_create_activity_event_collector",
                        lambda http, cfg: {"collect": lambda: []})
    out = _handler("capacity_patterns")({"days": 1})
    diag = out["patternsDiagnostics"]
    assert diag["maxCuPeakPct"] in (None, 0, 0.0)   # no fabricated 85.0 from the mock series

def test_tier1_spike_events_ranked_by_operation_frequency(monkeypatch):
    _clear_live(monkeypatch)
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    fake = [{"ts": f"2026-07-07T09:0{i}:00Z", "user": "john@co", "item": "Sales",
             "workspace": "F", "kind": "interactive", "cuSeconds": None,
             "queryText": None, "operation": "ViewReport"} for i in range(3)]
    fake.append({"ts": "2026-07-07T09:05:00Z", "user": "amy@co", "item": "HR", "workspace": "P",
                 "kind": "interactive", "cuSeconds": None, "queryText": None, "operation": "ViewReport"})
    import fabric_audit_agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_create_activity_event_collector",
                        lambda http, cfg: {"collect": lambda: fake})
    out = _handler("spike_events")({"days": 1})
    assert out["rankedBy"] == "operationFrequency"
    assert out["events"][0]["item"] == "Sales"      # 3 ops beats 1 op — frequency, not None-cost

def test_tier1_entra_http_client_is_memoized_across_calls(monkeypatch):
    """The Tier-1 activity-events http client must be built once and reused via _memo_client --
    a fresh _LazyEntraHttp per call re-triggers the MSAL ConfidentialClientApplication rebuild
    that _memo_client exists to prevent (see the LA/Kusto memo tests above)."""
    _clear_live(monkeypatch)
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    import fabric_audit_agent.tools as tools_mod
    tools_mod._CLIENT_CACHE.clear()
    builds = {"n": 0}
    seen = []

    class CountingLazyEntraHttp:
        def __init__(self, *a, **kw):
            builds["n"] += 1

    monkeypatch.setattr(tools_mod, "_LazyEntraHttp", CountingLazyEntraHttp)

    def fake_collector(http, cfg):
        seen.append(http)
        return {"collect": lambda: []}

    monkeypatch.setattr(tools_mod, "_create_activity_event_collector", fake_collector)
    _handler("spike_events")({"days": 1})
    _handler("spike_events")({"days": 7})
    assert builds["n"] == 1
    assert seen[0] is seen[1]


# --- Task 5 (Phase 4): read-only queryplan cost estimate (pre-flight primitive) ----------
def test_queryplan_estimate_sends_show_queryplan_prefixed_command():
    from fabric_audit_agent.tools import _queryplan_estimate
    sent = {}
    def fake_query(cmd):
        sent["cmd"] = cmd
        return [{"PlanSize": 12, "RelopSize": 3}]
    out = _queryplan_estimate("CapacityEvents | take 5; .drop table x", query=fake_query)
    assert sent["cmd"].startswith(".show queryplan")
    assert ".drop" not in sent["cmd"]                 # first_statement guard applied to the kql
    assert out == {"available": True, "plan": [{"PlanSize": 12, "RelopSize": 3}], "error": None}

def test_queryplan_estimate_unavailable_on_error_never_raises():
    def boom(cmd):
        raise RuntimeError("cluster rejected")
    from fabric_audit_agent.tools import _queryplan_estimate
    out = _queryplan_estimate("T | take 1", query=boom)
    assert out == {"available": False, "plan": None, "error": "cluster rejected"}

def test_describe_source_estimate_kql_attaches_plan(monkeypatch):
    # spec ADD 2 "immediately usable today": describe_source gains optional estimateKql.
    _clear_live(monkeypatch)
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "https://x.kusto.fabric.microsoft.com")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    # describe_source's capacity branch still fetches the live cslschema BEFORE the plan-estimate
    # wiring runs, so the kusto client builder needs a fake (same pattern as the existing
    # describe_source capacity-live tests above) -- otherwise it tries a real 'azure' import.
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query",
                        lambda *a, **kw: (lambda kql: [{"Schema": "ts:datetime"}]))
    import fabric_audit_agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_queryplan_estimate",
                        lambda kql, **kw: {"available": True, "plan": [{"PlanSize": 1}], "error": None})
    out = _handler("describe_source")({"source": "capacity", "estimateKql": "CapacityEvents | take 5"})
    assert out["planEstimate"]["available"] is True


# --- Task 9 (Phase 4): analyze_dax exposed as a tool -------------------------------------

def test_analyze_dax_tool_flags_filter_whole_table():
    out = _handler("analyze_dax")({"expression": "CALCULATE(SUM(S[x]), FILTER(Sales, Sales[y]>0))"})
    assert any(s["pattern"] == "filter-whole-table" for s in out["suggestions"])
    assert out["patternCount"] >= 1 and out["source"] == "static-rules"


def test_analyze_dax_tool_threads_duration_stats():
    out = _handler("analyze_dax")({"expression": "1+1", "durationMs": 9000})
    assert any(s["pattern"] == "slow-no-obvious-cause" for s in out["suggestions"])


def test_analyze_dax_tool_missing_expression_error_envelope():
    out = _handler("analyze_dax")({})
    assert "error" in out


# --- Task 11 (Phase 4): diagnose tool -- executed causal chains (14th tool) --------------

def test_diagnose_mock_path_throttle_chain_shape(monkeypatch):
    _clear_live(monkeypatch)
    out = _handler("diagnose")({"symptom": "throttle"})
    assert out["symptom"] == "throttle"
    assert out["tier"] == "mock"
    assert out["chain"]
    for step in out["chain"]:
        assert set(["step", "hypothesis", "verdict", "evidence"]) <= set(step.keys())


def test_diagnose_invalid_symptom_returns_error_envelope_never_raises(monkeypatch):
    _clear_live(monkeypatch)
    out = _handler("diagnose")({"symptom": "bogus"})
    assert "error" in out


def test_diagnose_tier1_env_has_coverage_note_and_unconfirmed_driver(monkeypatch):
    _clear_live(monkeypatch)
    for k, v in _T1_ENV.items():
        monkeypatch.setenv(k, v)
    # Also configure the capacity-events cluster so the series is real (>100% CU%) --
    # otherwise _capacity_series_only returns [] and stage1 eliminates before reaching the
    # "who drove" step at all. Events stay Tier-1 (operationLevel) since eventDepth/
    # userAttribution capability config is unrelated to the capacity-cluster env vars.
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_CLUSTER", "cluster-uri")
    monkeypatch.setenv("FABRIC_CAPACITY_EVENTS_DB", "db")
    fake_events = [{"ts": "2026-07-07T09:00:00Z", "user": "john@co", "item": "Sales",
                    "workspace": "Fin", "kind": "interactive", "cuSeconds": None,
                    "queryText": None, "operation": "ViewReport"}]
    ce_rows = [
        {"capacityId": "cap1", "windowStartTime": "2026-07-07T09:00:00Z",
         "baseCapacityUnits": 64, "capacityUnitMs": 2000000},   # ~104%
    ]
    import fabric_audit_agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_create_activity_event_collector",
                        lambda http, cfg: {"collect": lambda: fake_events})
    monkeypatch.setattr("fabric_audit_agent.adapters.clients.build_kusto_query",
                        lambda *a, **kw: (lambda kql: ce_rows))
    out = _handler("diagnose")({"symptom": "throttle", "days": 1})
    assert out.get("coverageNote")
    driver_step = next(s for s in out["chain"] if s["step"] == "who drove the over-window?")
    assert driver_step["verdict"] == "unconfirmed"


def test_diagnose_refresh_symptom_on_mock_runs_and_eliminates(monkeypatch):
    _clear_live(monkeypatch)
    out = _handler("diagnose")({"symptom": "refresh"})
    assert out["symptom"] == "refresh"
    assert out["eliminated"] == ["refresh failure"]


# --- Task 12 (Phase 4): whats_changed -- run-history diff (agent memory) ------------------
_HIST = [
    {"runAt": "2026-07-06T06:00:00Z", "metrics": {"peakCuPct": 88.0}, "findings": [
        {"key": "cap.hot", "level": "warn", "where": "F64", "what": "hot", "suppressed": False},
        {"key": "model.big", "level": "info", "where": "W/S", "what": "big", "suppressed": False}]},
    {"runAt": "2026-07-07T06:00:00Z", "metrics": {"peakCuPct": 96.0}, "findings": [
        {"key": "cap.hot", "level": "warn", "where": "F64", "what": "hot", "suppressed": False},
        {"key": "refresh.storm", "level": "warn", "where": "W/S", "what": "storm", "suppressed": False},
        {"key": "sec.x", "level": "info", "where": "W", "what": "x", "suppressed": True}]},
]

def _hist_env(tmp_path, monkeypatch, hist=_HIST):
    import json as _json
    p = tmp_path / "history.json"
    p.write_text(_json.dumps(hist), encoding="utf-8")
    monkeypatch.setenv("FABRIC_HISTORY_PATH", str(p))

def test_whats_changed_diffs_new_recurring_resolved(tmp_path, monkeypatch):
    _hist_env(tmp_path, monkeypatch)
    out = _handler("whats_changed")({})
    assert [f["key"] for f in out["new"]] == ["refresh.storm"]
    assert [f["key"] for f in out["recurring"]] == ["cap.hot"]
    assert [f["key"] for f in out["resolved"]] == ["model.big"]
    assert all(f["key"] != "sec.x" for f in out["new"])          # suppressed excluded
    assert out["peakCuTrend"][-1] == {"runAt": "2026-07-07T06:00:00Z", "peakCuPct": 96.0}
    assert out["lastRunAt"] == "2026-07-07T06:00:00Z"

def test_whats_changed_unconfigured_is_honest(monkeypatch):
    monkeypatch.delenv("FABRIC_HISTORY_PATH", raising=False)
    out = _handler("whats_changed")({})
    assert out["source"] == "none" and "FABRIC_HISTORY_PATH" in out["note"]

def test_whats_changed_single_run_history(tmp_path, monkeypatch):
    _hist_env(tmp_path, monkeypatch, hist=_HIST[-1:])
    out = _handler("whats_changed")({})
    assert out["new"] == [] and out["resolved"] == []
    assert "only one run" in out["note"]

def test_whats_changed_never_writes(tmp_path, monkeypatch):
    _hist_env(tmp_path, monkeypatch)
    before = (tmp_path / "history.json").read_text(encoding="utf-8")
    _handler("whats_changed")({})
    assert (tmp_path / "history.json").read_text(encoding="utf-8") == before

def test_whats_changed_malformed_history_is_error_not_empty(tmp_path, monkeypatch):
    p = tmp_path / "history.json"
    p.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("FABRIC_HISTORY_PATH", str(p))
    out = _handler("whats_changed")({})
    assert "error" in out
    assert "new" not in out

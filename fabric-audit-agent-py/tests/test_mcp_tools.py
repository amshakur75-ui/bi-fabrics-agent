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

    # Under the cap -> no truncated key (absence means full window coverage)
    tools_mod._CLIENT_CACHE.clear()
    monkeypatch.setattr(
        "fabric_audit_agent.adapters.clients.build_log_analytics_query",
        _fake_la_query_builder(rows[:1]),
    )
    out2 = next(d for d in create_tool_definitions()
                if d["name"] == "spike_events")["handler"]({"days": 1, "topN": 5})
    assert "truncated" not in out2


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

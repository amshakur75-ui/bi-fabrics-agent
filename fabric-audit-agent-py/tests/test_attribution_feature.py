"""A2b + A3 — the 30% concentration feature: live user-attribution collector and the Teams
two-way conversation surface. Built on the ported attribution + concentration engine."""
from fabric_audit_agent.adapters.collector_activity import (
    map_activity_event, fetch_activity_events, map_log_analytics_rows, group_events_by_item,
    create_activity_collector,
)
from fabric_audit_agent.detectors.concentration import detect_concentration
from fabric_audit_agent.conversation import build_concentration_alert, answer_question


class _Http:
    def __init__(self, by_url=None, default=None):
        self.by_url = by_url or {}
        self.default = default if default is not None else {}
        self.calls = []

    def get_json(self, url):
        self.calls.append(url)
        for frag, resp in self.by_url.items():
            if frag in url:
                return resp
        return self.default

    def post_json(self, url, body):
        self.calls.append((url, body))
        return self.by_url.get("__post__", {})


# ---------- A2b: activity collector ----------
def test_map_activity_event_interactive_vs_background():
    view = map_activity_event({"Operation": "ViewReport", "UserId": "a@x", "ArtifactName": "Sales", "WorkspaceName": "Fin"})
    assert view == {"user": "a@x", "item": "Sales", "workspace": "Fin", "operation": "ViewReport", "interactive": True, "time": None}
    refresh = map_activity_event({"Operation": "RefreshDataset", "UserId": "svc@x", "DatasetName": "Sales"})
    assert refresh["interactive"] is False and refresh["item"] == "Sales"


def test_fetch_activity_events_pages_continuation():
    http = _Http(by_url={
        "startDateTime": {"activityEventEntities": [{"Operation": "ViewReport", "UserId": "a", "ArtifactName": "X"}],
                          "continuationUri": "https://cont/page2"},
        "page2": {"activityEventEntities": [{"Operation": "RefreshDataset", "UserId": "b", "DatasetName": "X"}]},
    })
    events = fetch_activity_events(http, "2026-06-11T00:00:00Z", "2026-06-11T23:59:59Z", "https://act")
    assert len(events) == 2 and events[0]["interactive"] is True and events[1]["interactive"] is False
    assert len(http.calls) == 2


def test_map_log_analytics_rows():
    resp = {"tables": [{"columns": [{"name": "ExecutingUser"}, {"name": "DatasetName"}, {"name": "CpuTimeMs"}],
                        "rows": [["alice", "Sales", 1200], ["bob", "Sales", 300]]}]}
    ev = map_log_analytics_rows(resp)
    assert ev[0] == {"user": "alice", "item": "Sales", "cpuMs": 1200, "durationMs": None, "interactive": True}
    assert map_log_analytics_rows({}) == []


def test_group_events_by_item_merges_sources():
    by = group_events_by_item([{"item": "A", "user": "u"}], [{"item": "A", "user": "v", "cpuMs": 5}])
    assert len(by["A"]) == 2


def test_activity_collector_names_user_driving_concentration():
    # base estate: one item over the 30% threshold but with NO attribution yet
    base = {"collect": lambda: {"items": [{"name": "Sales", "workspace": "Fin", "sharePct": 35, "observedAt": "t"}]}}
    http = _Http(default={"activityEventEntities": [
        {"Operation": "ViewReport", "UserId": "alice@x", "ArtifactName": "Sales"},
        {"Operation": "ViewReport", "UserId": "alice@x", "ArtifactName": "Sales"},
        {"Operation": "ViewReport", "UserId": "bob@x", "ArtifactName": "Sales"},
    ]})
    collector = create_activity_collector(
        http, config={"windowStart": "2026-06-11T00:00:00Z", "windowEnd": "2026-06-11T23:59:59Z", "activityUrl": "https://act"},
        base_collector=base,
    )
    facts = collector["collect"]()
    item = facts["items"][0]
    assert [u["user"] for u in item["topUsers"]] == ["alice@x", "bob@x"]   # alice (2 ops) ranked first
    assert item["userCount"] == 2 and item["attributionMode"] == "frequency"
    # the detector now NAMES the user instead of "pending correlation"
    flags = detect_concentration(facts)
    assert len(flags) == 1
    assert "alice@x" in flags[0]["what"] and "35%" in flags[0]["what"]
    assert "pending" not in flags[0]["what"]


def test_activity_collector_cost_mode_from_log_analytics():
    base = {"collect": lambda: {"items": [{"name": "Sales", "workspace": "Fin", "sharePct": 40, "observedAt": "t"}]}}
    http = _Http(default={"activityEventEntities": []})
    collector = create_activity_collector(http, config={
        "logAnalyticsEvents": [{"item": "Sales", "user": "heavy@x", "cpuMs": 9000, "interactive": False},
                               {"item": "Sales", "user": "light@x", "cpuMs": 100, "interactive": True}],
    }, base_collector=base)
    item = collector["collect"]()["items"][0]
    assert item["attributionMode"] == "cost" and item["topUsers"][0]["user"] == "heavy@x"
    assert item["background"] is True   # cost-weighted: the heavy non-interactive load dominates


def test_activity_collector_leaves_items_without_activity_untouched():
    base = {"collect": lambda: {"items": [{"name": "Quiet", "workspace": "W", "sharePct": 33}]}}
    http = _Http(default={"activityEventEntities": []})
    item = create_activity_collector(http, config={}, base_collector=base)["collect"]()["items"][0]
    assert "topUsers" not in item   # untouched -> detector falls back to "pending correlation"


# ---------- A3: conversation ----------
def test_build_concentration_alert_user_first_with_actions():
    finding = {"key": "capacity.concentration::Fin / Sales", "what": "alice@x is driving 35% of capacity CU via \"Sales\" (Fin).",
               "evidence": {"sharePct": 35, "topUsers": [{"user": "alice@x"}], "owner": None, "attributionMode": "frequency"}}
    card = build_concentration_alert(finding)
    assert card["type"] == "message" and card["sections"][0]["text"] == finding["what"]
    titles = [a["title"] for a in card["actions"]]
    assert any("Acknowledge" in t for t in titles) and any("Contact alice@x" in t for t in titles)
    facts = {f["name"]: f["value"] for f in card["sections"][0]["facts"]}
    assert facts["Driver"] == "alice@x" and facts["Share of CU"] == "35%"


def test_build_concentration_alert_owner_when_background():
    finding = {"key": "k", "what": "background-driven", "evidence": {"sharePct": 50, "topUsers": None, "owner": "svc@x"}}
    card = build_concentration_alert(finding)
    assert any("Contact svc@x" in a["title"] for a in card["actions"])


def _env():
    return {"summary": "Audit complete: 3 findings.", "data": {
        "verdict": {"decision": "optimize", "reason": "fix these first"},
        "healthScore": {"overall": 62},
        "roadmap": [{"rank": 1, "level": "Critical", "what": "Fix GL refresh"}],
        "findings": [{"key": "capacity.concentration::Fin / Sales",
                      "what": "alice@x is driving 35% of capacity CU via \"Sales\" (Fin).",
                      "evidence": {"topUsers": [{"user": "alice@x"}], "attributionMode": "frequency"}}],
    }}


def test_answer_question_routes_intents():
    env = _env()
    assert "OPTIMIZE" in answer_question("what's the verdict — should we buy more?", env)
    assert "alice@x" in answer_question("who is driving capacity?", env)
    assert "Fix GL refresh" in answer_question("what are the top fixes?", env)
    assert "62/100" in answer_question("health score?", env)
    assert "verdict" in answer_question("help", env).lower()
    assert "verdict" in answer_question("", env).lower()   # empty -> help


def test_answer_question_who_with_no_concentration():
    assert "threshold" in answer_question("who is the noisy neighbor", {"data": {"findings": []}})


def test_answer_question_unknown_falls_back_to_summary():
    assert answer_question("tell me a joke", _env()) == "Audit complete: 3 findings."

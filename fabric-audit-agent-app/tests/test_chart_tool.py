"""agent_server.chart_tool — the DIRECT render_chart tool given to the agent.

render_chart wasn't in the agent's live toolset, so the model wrote chart JSON as text instead of
calling it. This tool makes render_chart callable; render_chart_spec is the pure validate-and-wrap
handler. Mirrors the canonical contract (chartType/title/series/axisLabels/sourceScope/isProxy)."""
from agent_server.chart_tool import render_chart_spec, chart_tool_and_dispatch


def _series():
    return [{"name": "CU%", "data": [{"x": "00:00", "y": 18.8}, {"x": "01:00", "y": 14.5}]}]


def test_valid_spec_returns_chart_with_canonical_fields():
    out = render_chart_spec({"chartType": "line", "title": "CU% today", "series": _series(),
                             "axisLabels": {"x": "Hour", "y": "CU %"}, "sourceScope": "capacity"})
    assert "chart" in out
    c = out["chart"]
    assert c["chartType"] == "line" and c["sourceScope"] == "capacity"
    assert c["axisLabels"] == {"x": "Hour", "y": "CU %"}
    assert c["isProxy"] is False and "proxyCaveat" not in c   # capacity = true CU


def test_donut_is_accepted():
    out = render_chart_spec({"chartType": "donut", "title": "share", "series": _series(),
                             "sourceScope": "capacity"})
    assert out["chart"]["chartType"] == "donut"


def test_user_scope_defaults_to_proxy_with_caveat():
    out = render_chart_spec({"chartType": "bar", "title": "by user", "series": _series(),
                             "sourceScope": "user"})
    assert out["chart"]["isProxy"] is True and out["chart"]["proxyCaveat"]


def test_bad_chart_type_and_scope_error():
    assert "error" in render_chart_spec({"chartType": "pie3d", "title": "t", "series": _series(),
                                         "sourceScope": "capacity"})
    assert "error" in render_chart_spec({"chartType": "bar", "title": "t", "series": _series(),
                                         "sourceScope": "galaxy"})


def test_thin_data_falls_back_to_text():
    out = render_chart_spec({"chartType": "bar", "title": "one point",
                             "series": [{"name": "s", "data": [{"x": "a", "y": 1}]}],
                             "sourceScope": "capacity"})
    assert out.get("fallback") is True and out["totalPoints"] == 1


def test_accepts_label_value_points_for_donut():
    # models naturally emit {label, value} for pie/donut — coerce to {x, y}
    out = render_chart_spec({"chartType": "donut", "title": "share", "sourceScope": "capacity",
                             "series": [{"name": "Hourly share",
                                         "data": [{"label": "00:00", "value": 18.8},
                                                  {"label": "01:00", "value": 14.5}]}]})
    assert "chart" in out
    pts = out["chart"]["series"][0]["data"]
    assert pts[0] == {"x": "00:00", "y": 18.8} and pts[1]["x"] == "01:00"


def test_coerces_percent_and_number_strings_and_pairs():
    out = render_chart_spec({"chartType": "bar", "title": "CU%", "sourceScope": "capacity",
                             "series": [{"name": "CU%",
                                         "data": [{"x": "07:00", "y": "26.5%"},
                                                  ["08:00", "25.8"], {"x": "09:00", "y": 23.6}]}]})
    ys = [p["y"] for p in out["chart"]["series"][0]["data"]]
    assert ys == [26.5, 25.8, 23.6]


def test_missing_source_scope_is_rejected_not_assumed_to_be_capacity():
    """This asserted the lenient default, which was the middle link in a three-part failure.

    tools.py's canonical handler already REJECTS a missing sourceScope, but agent.py strips the MCP
    render_chart and registers this looser one, so the lenient default was the only behaviour that
    ran in production. Combined with the frontend ignoring sourceScope entirely, the shipped "Item
    concentration (donut)" suggested action produced a per-item CpuTimeMs donut carrying a green
    "true CU" pill and no caveat — the claim gates.true_cu_per_user_gate marks permanently blocked.
    """
    out = render_chart_spec({"chartType": "line", "title": "t", "series": _series()})
    assert "error" in out
    assert "sourceScope is required" in out["error"]


def test_an_item_scoped_chart_defaults_to_proxy():
    """Per-item cost comes from the same CpuTimeMs/DurationMs telemetry as per-user cost, so an
    omitted isProxy must err toward the WEAKER claim. `(scope == "user")` declared every per-item
    chart authoritative billed CU."""
    out = render_chart_spec({"chartType": "bar", "title": "t", "sourceScope": "item",
                             "series": _series()})
    assert out["chart"]["isProxy"] is True
    assert out["chart"]["proxyCaveat"]


def test_a_capacity_scoped_chart_is_still_not_a_proxy():
    out = render_chart_spec({"chartType": "line", "title": "t", "sourceScope": "capacity",
                             "series": _series()})
    assert out["chart"]["isProxy"] is False
    assert "proxyCaveat" not in out["chart"]


def test_uncoercible_point_is_rejected():
    out = render_chart_spec({"chartType": "bar", "title": "t", "sourceScope": "capacity",
                             "series": [{"name": "s", "data": [{"x": "a", "y": "not-a-number"},
                                                               {"x": "b", "y": 2}]}]})
    assert "error" in out


def test_tool_registration_exposes_render_chart():
    tools, dispatch = chart_tool_and_dispatch()
    assert tools[0]["name"] == "render_chart" and "render_chart" in dispatch
    assert tools[0]["input_schema"]["required"] == ["chartType", "title", "series", "sourceScope"]

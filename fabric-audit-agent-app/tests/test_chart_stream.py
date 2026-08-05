"""agent_server.chart_stream — surface render_chart tool calls to the chat UI as Responses items.

The agentic tool loop consumes tool results internally, so a render_chart call's chart spec never
reaches the client and the UI's render_chart tool-part path (which renders <Chart>) never fires.
chart_output_items builds the function_call + function_call_output items that carry the spec out.
Pure/stdlib — importable without mlflow."""
import json

from agent_server.chart_stream import chart_output_items


def _tr(tool, result, call_id="c1", inp=None):
    return {"tool": tool, "callId": call_id, "input": inp or {}, "result": result}


def test_render_chart_emits_paired_function_call_and_output():
    chart = {"chart": {"chartType": "bar", "title": "t", "series": [], "sourceScope": "capacity",
                       "isProxy": False}}
    items = chart_output_items([_tr("render_chart", chart, call_id="abc", inp={"chartType": "bar"})])
    assert len(items) == 2
    call, out = items
    assert call["type"] == "function_call" and call["name"] == "render_chart"
    assert call["call_id"] == "abc" and json.loads(call["arguments"]) == {"chartType": "bar"}
    assert out["type"] == "function_call_output" and out["call_id"] == "abc"
    # the output the frontend parses back into RenderChartOutput carries the chart verbatim
    assert json.loads(out["output"]) == chart


def test_fallback_result_is_still_surfaced():
    fb = {"fallback": True, "text": "Only one data point.", "reason": "thin", "totalPoints": 1}
    items = chart_output_items([_tr("render_chart", fb)])
    assert len(items) == 2 and json.loads(items[1]["output"]) == fb


def test_non_render_chart_tools_are_ignored():
    assert chart_output_items([_tr("capacity_peaks", {"peaks": []})]) == []


def test_render_chart_without_chart_or_fallback_is_ignored():
    # e.g. a validation error result — nothing renderable, so don't emit a broken tool part
    assert chart_output_items([_tr("render_chart", {"error": "bad chartType"})]) == []


def test_multiple_charts_get_distinct_paired_call_ids():
    c = {"chart": {"chartType": "pie", "title": "p", "series": [], "sourceScope": "user",
                   "isProxy": True}}
    items = chart_output_items([_tr("render_chart", c, call_id="a"),
                                _tr("render_chart", c, call_id="b")])
    assert [it["call_id"] for it in items] == ["a", "a", "b", "b"]

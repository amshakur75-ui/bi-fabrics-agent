"""agent_server.chart_stream — carry render_chart specs to the UI as ```fabric-chart fences.

The agentic tool loop consumes tool results internally, so a render_chart call's spec never reaches
the client and the UI's <Chart> path never fires. chart_fences/append_chart_fences embed the spec in
the answer text so message.tsx can split it out and render the real chart. Pure/stdlib."""
import json

from agent_server.chart_stream import chart_fences, append_chart_fences, CHART_FENCE_LANG


def _tr(tool, result):
    return {"tool": tool, "callId": "c1", "input": {}, "result": result}


def _chart():
    return {"chart": {"chartType": "bar", "title": "t", "series": [], "axisLabels": {"x": "", "y": ""},
                      "sourceScope": "capacity", "isProxy": False}}


def test_render_chart_becomes_a_fabric_chart_fence():
    out = chart_fences([_tr("render_chart", _chart())])
    assert out.startswith("```" + CHART_FENCE_LANG + "\n") and out.rstrip().endswith("```")
    body = out.split("\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(body) == _chart()          # the fence body is the render_chart output verbatim


def test_fallback_result_is_still_fenced():
    fb = {"fallback": True, "text": "Only one point.", "reason": "thin", "totalPoints": 1}
    out = chart_fences([_tr("render_chart", fb)])
    body = out.split("\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(body) == fb


def test_non_render_chart_and_error_results_emit_nothing():
    assert chart_fences([_tr("capacity_peaks", {"peaks": []})]) == ""
    assert chart_fences([_tr("render_chart", {"error": "bad chartType"})]) == ""
    assert chart_fences([]) == ""


def test_multiple_charts_separated_by_blank_line():
    out = chart_fences([_tr("render_chart", _chart()), _tr("render_chart", _chart())])
    assert out.count("```" + CHART_FENCE_LANG) == 2 and "\n\n```" in out


def test_append_leaves_text_untouched_when_no_charts():
    assert append_chart_fences("the answer", [_tr("capacity_peaks", {})]) == "the answer"


def test_append_adds_fence_after_the_answer_text():
    out = append_chart_fences("The peak was 28%.", [_tr("render_chart", _chart())])
    assert out.startswith("The peak was 28%.\n\n```" + CHART_FENCE_LANG)

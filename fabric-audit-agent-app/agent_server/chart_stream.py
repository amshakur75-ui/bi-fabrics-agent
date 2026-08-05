"""Surface render_chart tool calls to the chat UI as Responses stream items.

The agentic tool loop (agent.py::_run_tool_loop) consumes each tool's result INTERNALLY — it feeds
the result back to the model and only the final assistant text is streamed. So a render_chart call's
chart spec never reaches the client, the UI's render_chart tool-part path (message.tsx -> <Chart>)
never fires, and the agent's answer falls back to a text table (see GAPS 'Phase 8 render path').

This builds the OpenAI-Responses ``function_call`` + ``function_call_output`` items that carry the
chart spec out. The stream/invoke handlers emit them as ``response.output_item.done`` events; the
Databricks AI SDK provider (``useRemoteToolCalling: true``) then surfaces them as a render_chart tool
part with ``output``, and the frontend renders the <Chart>. Pure/stdlib — no mlflow import, so it is
unit-testable and a malformed result can never abort the SSE stream.
"""
import json


def chart_output_items(tool_results):
    """Return the Responses stream items (plain dicts) for every render_chart call in
    ``tool_results`` (entries shaped ``{"tool","callId","input","result"}``).

    One ``function_call`` + one ``function_call_output`` per render_chart call whose result actually
    carries a renderable ``chart`` or ``fallback`` (a validation-error result emits nothing, so the UI
    never gets a broken tool part). The two items are paired by ``call_id``; the output is the chart
    spec JSON the frontend parses back into ``RenderChartOutput``.
    """
    items = []
    for i, tr in enumerate(tool_results or []):
        if (tr or {}).get("tool") != "render_chart":
            continue
        result = (tr or {}).get("result")
        if not isinstance(result, dict) or not ("chart" in result or "fallback" in result):
            continue
        call_id = str(tr.get("callId") or f"render_chart_{i}")
        items.append({
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": "render_chart",
            "arguments": json.dumps(tr.get("input") or {}, ensure_ascii=False),
        })
        items.append({
            "type": "function_call_output",
            "id": f"fco_{call_id}",
            "call_id": call_id,
            "output": json.dumps(result, ensure_ascii=False),
        })
    return items

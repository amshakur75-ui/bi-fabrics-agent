"""Carry render_chart specs to the chat UI by appending them to the answer text as fenced blocks.

The agentic tool loop (agent.py::_run_tool_loop) consumes each tool's result INTERNALLY (feeds it
back to the model); only the final assistant text is streamed. So a render_chart call's chart spec
never reaches the client and the UI's <Chart> path never fires — the agent's answer falls back to a
hand-drawn ASCII table (see GAPS 'Phase 8 render path').

Rather than depend on the provider's tool-part mapping (which we can't observe), we append the chart
spec to the answer text inside a ```fabric-chart fenced block. The chat UI (message.tsx) splits those
blocks out of the markdown and renders the real recharts <Chart> from the JSON. The text stream is
guaranteed to reach the client, so this path is deterministic and unit-testable. Pure/stdlib.
"""
import json

CHART_FENCE_LANG = "fabric-chart"


def chart_fences(tool_results):
    """Return the ```fabric-chart fenced markdown blocks for every render_chart call in
    ``tool_results`` (entries shaped ``{"tool","result",...}``), joined by blank lines — or ``""``.

    Only render_chart results carrying a renderable ``chart`` or ``fallback`` are emitted (a
    validation-error result yields nothing, so the UI never shows a broken chart). The fence body is
    the render_chart output dict verbatim — exactly what the frontend <Chart> expects.
    """
    blocks = []
    for tr in tool_results or []:
        if (tr or {}).get("tool") != "render_chart":
            continue
        result = (tr or {}).get("result")
        if not isinstance(result, dict) or not ("chart" in result or "fallback" in result):
            continue
        blocks.append("```" + CHART_FENCE_LANG + "\n"
                      + json.dumps(result, ensure_ascii=False) + "\n```")
    return "\n\n".join(blocks)


def append_chart_fences(text, tool_results):
    """Append any chart fences to ``text`` (blank-line separated). No render_chart calls -> unchanged."""
    fences = chart_fences(tool_results)
    if not fences:
        return text
    return (text or "").rstrip() + "\n\n" + fences

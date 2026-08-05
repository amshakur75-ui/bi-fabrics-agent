"""render_chart as a DIRECT tool for the agent — the visualization data contract.

render_chart is defined in fabric_audit_agent.tools (served via MCP), but the agent's live toolset
did not include it — so when asked for a chart the model IMPROVISED chart JSON as text (wrong schema:
`type`/`xLabel` instead of `chartType`/`axisLabels`) and the UI never rendered a chart. This registers
render_chart DIRECTLY with the agent (pure validate-and-wrap, no I/O), so a chart request produces a
real render_chart tool call; its result is then fenced into the answer text
(chart_stream.append_chart_fences) and the chat UI renders the recharts <Chart>.

Self-contained: no fabric_audit_agent import (the agent app is deliberately decoupled). Mirrors the
canonical handler's validation + thin-data fallback + spec shape.
"""
import asyncio

_CHART_TYPES = ("line", "bar", "grouped-bar", "stacked-bar", "pie", "donut")
_CHART_SCOPES = ("capacity", "item", "user")
_PROXY_CAVEAT = (
    "Proxy-attributed: per-user / per-item figures are a CPU-time proxy (CpuTimeMs / DurationMs), "
    "not authoritative capacity CU."
)


def render_chart_spec(inp):
    """Validate a chart spec and return the render_chart output dict: ``{chart}`` | ``{fallback}`` |
    ``{error}``. Pure — the exact contract the frontend <Chart> consumes."""
    inp = inp or {}
    chart_type = inp.get("chartType")
    if chart_type not in _CHART_TYPES:
        return {"error": f"chartType must be one of {list(_CHART_TYPES)}, got {chart_type!r}"}
    title = inp.get("title")
    if not title or not str(title).strip():
        return {"error": "title is required"}
    series = inp.get("series")
    if not series or not isinstance(series, list):
        return {"error": "series must be a non-empty list of {name, data:[{x,y}]}"}
    for i, s in enumerate(series):
        if not isinstance(s, dict) or not s.get("name"):
            return {"error": f"series[{i}] must be a dict with a name"}
        data = s.get("data")
        if not isinstance(data, list):
            return {"error": f"series[{i}].data must be a list of {{x, y}}"}
        for j, pt in enumerate(data):
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                return {"error": f"series[{i}].data[{j}] must have both x and y"}

    axis = inp.get("axisLabels")
    if not isinstance(axis, dict):
        axis = {"x": "", "y": ""}
    scope = inp.get("sourceScope")
    if scope not in _CHART_SCOPES:
        return {"error": f"sourceScope must be one of {list(_CHART_SCOPES)}, got {scope!r}"}

    is_proxy = inp.get("isProxy")
    is_proxy = (scope == "user") if is_proxy is None else bool(is_proxy)

    total_points = sum(len(s.get("data") or []) for s in series)
    if total_points <= 1:
        if total_points == 0:
            text = f"{title}: no data points available to chart."
        else:
            pt = next((p for s in series for p in (s.get("data") or [])), {})
            text = f"{title}: {pt.get('x')} = {pt.get('y')} (single data point; chart not rendered)."
        return {"fallback": True, "text": text,
                "reason": "too few data points to render a meaningful chart",
                "totalPoints": total_points}

    spec = {
        "chartType": chart_type,
        "title": str(title).strip(),
        "series": series,
        "axisLabels": {"x": str(axis.get("x", "")), "y": str(axis.get("y", ""))},
        "sourceScope": scope,
        "isProxy": is_proxy,
    }
    if is_proxy and scope in ("user", "item"):
        spec["proxyCaveat"] = _PROXY_CAVEAT
    return {"chart": spec}


_TOOL = {
    "name": "render_chart",
    "description": (
        "Render query results as an interactive chart in the chat UI. Call this AFTER obtaining data "
        "from another tool (run_kql, spike_events, capacity_peaks, etc.) to visualize the results. "
        "This is how a chart is shown — do NOT write chart JSON or ASCII into your text answer. "
        "Validates the data contract and scope consistency; returns a chart spec the frontend renders. "
        "Falls back to plain text when data is empty or has only 1 point. Read-only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "chartType": {
                "type": "string",
                "enum": list(_CHART_TYPES),
                "description": "Chart type to render ('donut' is a pie with a hollow center).",
            },
            "title": {"type": "string", "description": "Chart title (displayed above the chart)."},
            "series": {
                "type": "array",
                "description": (
                    "Data series to chart. Each entry: {name: string, data: [{x, y}]}. For pie/donut "
                    "use a single series with category labels as x values."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Series name (shown in legend)."},
                        "data": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "x": {"description": "X value (category label, date, or number)."},
                                    "y": {"type": "number", "description": "Y value (numeric)."},
                                },
                                "required": ["x", "y"],
                            },
                        },
                    },
                    "required": ["name", "data"],
                },
            },
            "axisLabels": {
                "type": "object",
                "description": "Axis labels.",
                "properties": {
                    "x": {"type": "string", "description": "X-axis label."},
                    "y": {"type": "string", "description": "Y-axis label."},
                },
            },
            "sourceScope": {
                "type": "string",
                "enum": list(_CHART_SCOPES),
                "description": (
                    "The scope of ALL data in this chart — 'capacity' (capacity-level true CU%), "
                    "'item' (per-item), or 'user' (per-user). Mixing scopes in one chart is rejected."
                ),
            },
            "isProxy": {
                "type": "boolean",
                "description": (
                    "Whether the data is a CPU-time proxy (defaults true for user scope). True renders "
                    "a visible proxy caveat. Capacity-scoped CU% is true CU -> false."
                ),
            },
        },
        "required": ["chartType", "title", "series", "sourceScope"],
    },
}


def chart_tool_and_dispatch():
    """Return ``([tool], {name: async handler})`` for the direct render_chart tool."""
    async def handler(inp):
        return render_chart_spec(inp)
    return [_TOOL], {"render_chart": handler}

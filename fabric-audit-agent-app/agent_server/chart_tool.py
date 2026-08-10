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


def _num(v):
    """Coerce a y-value to a float: accept numbers, and strings like '26.5%' / '1,024' / '18.8'.
    Returns None if it can't be a number (so the caller can reject that point)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().rstrip("%").strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _coerce_point(pt):
    """Normalize a data point to ``{"x", "y"}`` — tolerating the shapes models naturally emit:
    ``{x,y}``, ``{label,value}`` / ``{name,value}`` / ``{category,count}`` (pie/donut), and ``[x, y]``
    pairs. ``y`` is coerced to a number. Returns None if it can't form a valid {x, numeric y}."""
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        x, y = pt[0], _num(pt[1])
    elif isinstance(pt, dict):
        x = pt.get("x")
        if x is None:
            x = pt.get("label", pt.get("name", pt.get("category")))
        y = _num(pt.get("y") if pt.get("y") is not None else
                 pt.get("value", pt.get("count")))
    else:
        return None
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _reject(inp, message):
    """Return a render_chart error AND log the offending payload shape server-side, so a rejection is
    diagnosable without guessing. Logs only keys + a 2-point sample, never the full payload."""
    try:
        s0 = ((inp or {}).get("series") or [{}])[0]
        sample = (s0.get("data") or [])[:2] if isinstance(s0, dict) else None
        print(f"[render_chart] rejected: {message} | keys={sorted((inp or {}).keys())} "
              f"chartType={(inp or {}).get('chartType')!r} sampleData={sample!r}", flush=True)
    except Exception:
        pass
    return {"error": message}


def render_chart_spec(inp):
    """Validate a chart spec and return the render_chart output dict: ``{chart}`` | ``{fallback}`` |
    ``{error}``. Pure — the exact contract the frontend <Chart> consumes. Tolerant of the point
    shapes models emit (label/value, [x,y]) and coerces percent/number strings."""
    inp = inp or {}
    chart_type = inp.get("chartType")
    if chart_type not in _CHART_TYPES:
        return _reject(inp, f"chartType must be one of {list(_CHART_TYPES)}, got {chart_type!r}")
    title = inp.get("title")
    if not title or not str(title).strip():
        return _reject(inp, "title is required")
    series = inp.get("series")
    if not series or not isinstance(series, list):
        return _reject(inp, "series must be a non-empty list of {name, data:[{x,y}]}")
    # Normalize each series' data IN PLACE to {x, numeric y}, tolerating label/value & [x,y] shapes.
    norm_series = []
    for i, s in enumerate(series):
        if not isinstance(s, dict):
            return _reject(inp, f"series[{i}] must be a dict with name + data")
        name = s.get("name") or s.get("label") or f"Series {i + 1}"
        data = s.get("data")
        if not isinstance(data, list):
            return _reject(inp, f"series[{i}].data must be a list of points")
        norm_data = []
        for j, pt in enumerate(data):
            cp = _coerce_point(pt)
            if cp is None:
                return _reject(inp, f"series[{i}].data[{j}] needs a label/x and a numeric value/y "
                                    f"(got {pt!r})")
            norm_data.append(cp)
        norm_series.append({"name": str(name), "data": norm_data})
    series = norm_series

    axis = inp.get("axisLabels")
    if not isinstance(axis, dict):
        axis = {"x": "", "y": ""}
    scope = inp.get("sourceScope")
    if scope is None:
        # REJECT, do not assume. The canonical handler in tools.py already rejects a missing
        # sourceScope; agent.py deliberately strips the MCP render_chart and registers THIS looser
        # one, so the strict version is unreachable in production and this lenient default was the
        # only behaviour that ran. Defaulting to "capacity" meant a per-item cuSeconds chart -- the
        # shipped "Item concentration (donut)" suggested action does exactly this -- was declared
        # capacity-level TRUE CU, which is the claim gates.true_cu_per_user_gate marks PERMANENTLY
        # BLOCKED. An un-scoped chart is a caller bug; the model retries with the scope.
        return _reject(inp, "sourceScope is required: one of "
                            f"{list(_CHART_SCOPES)} — a chart of per-user or per-item cost must not "
                            "be presented as capacity-level true CU")
    if scope not in _CHART_SCOPES:
        return _reject(inp, f"sourceScope must be one of {list(_CHART_SCOPES)}, got {scope!r}")

    is_proxy = inp.get("isProxy")
    # ITEM data is a proxy too, not just user data. Per-item cost comes from the same CpuTimeMs /
    # DurationMs telemetry as per-user cost -- `(scope == "user")` silently declared every per-item
    # chart authoritative billed CU. Default to proxy for BOTH, so an omitted flag errs toward the
    # weaker claim rather than the stronger one.
    is_proxy = (scope in ("user", "item")) if is_proxy is None else bool(is_proxy)

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

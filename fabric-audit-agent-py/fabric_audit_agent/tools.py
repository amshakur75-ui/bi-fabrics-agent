"""Tool definitions (Anthropic/MCP format) exposing the read-only audit as ``run_audit``.

Port of ``tools.js``. Each tool carries a ``handler(input)`` the host invokes; the audit is
READ-ONLY — the handler only reads (mock) telemetry and writes findings to local files, never
mutating any estate. ``data_agent.build_data_agent_manifest`` strips the handler for the
published manifest (keeps name/description/input_schema).
"""
import json
import os

from .adapters import create_mock_collector, create_stub_reasoner
from .pipeline import run_audit
from .investigation.evidence import build_coverage
from .investigation.playbooks import investigate_user as _iu, investigate_capacity_spike as _ics
from .adapters.reasoner_investigation import create_investigation_reasoner
from .investigation import events as _events_mod
from .investigation.baseline import compute_baseline as _compute_baseline
from .investigation.expensive import top_expensive as _top_expensive
from .investigation.spike_history import user_spike_history as _user_spike_history
from .investigation.patterns import capacity_patterns as _capacity_patterns
from .adapters.collector_capacity_events import capacity_series as _capacity_cu_series
from .query.envelope import cap_rows as _cap_rows, finish as _finish, to_columnar as _to_columnar
from .query.windows import resolve_window as _resolve_window

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Any of these means a real telemetry source is wired; otherwise the offline mock is used.
_LIVE_SOURCE_VARS = ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
                     "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID")


def _has_live_source(env):
    """True if any real source is configured (CSV / REST / Eventhouse / Log Analytics).

    Single source of truth so ``run_audit`` and ``list_workspaces`` can never disagree about
    whether to go live or fall back to the mock."""
    return any(env.get(v) for v in _LIVE_SOURCE_VARS)


def _has_live_event_source(env):
    """True only if the RAW per-event LA source (events_or_mock's actual live branch) is
    configured. Narrower than ``_has_live_source`` on purpose: the Phase-3 event tools
    (user_spike_history / spike_events / capacity_patterns) must not label their data "live"
    just because some OTHER source (e.g. FABRIC_CSV_PATHS) is configured while events themselves
    are still coming from the mock fixture -- that would be a real mislabel, not a cosmetic one."""
    return bool(env.get("FABRIC_LA_WORKSPACE_ID") and env.get("FABRIC_CLIENT_ID"))


def _run_real_or_mock(base, env):
    """Run the audit and RETURN the envelope — read-only and **write-free**. A Databricks App
    container can't write to /Volumes, and the interactive tool doesn't need to persist: history
    and report files are the scheduled Job's role. Uses live sources when configured
    (FABRIC_CSV_PATHS / FABRIC_CLIENT_ID / FABRIC_KUSTO_CLUSTER /
    FABRIC_CAPACITY_EVENTS_CLUSTER / FABRIC_LA_WORKSPACE_ID), else the offline mock."""
    from .config import DEFAULT_CONFIG, merge_config
    raw = env.get("FABRIC_AUDIT_CONFIG")
    config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG

    if _has_live_source(env):
        from .job import build_collector_from_env, _default_reasoner, _wants_llm
        collector = build_collector_from_env(env)
        reasoner = _default_reasoner(env, config) if _wants_llm(env) else create_stub_reasoner(config)
    else:
        collector = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))
        reasoner = create_stub_reasoner(config)

    return run_audit(collector, reasoner, {"deliver": lambda e: None}, store=None,
                     config=config, agent_id="fabric-audit-agent")


def _build_collector(env):
    """Return a live collector if any source is configured, else None."""
    if not _has_live_source(env):
        return None
    from .job import build_collector_from_env
    return build_collector_from_env(env)


def create_tool_definitions(base_dir=None):
    base = base_dir if base_dir is not None else _BASE

    def _collector_or_mock():
        """Return a live collector if any source is configured, else the offline mock estate."""
        col = _build_collector(os.environ)
        if col is None:
            col = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))
        return col

    def run_audit_handler(_input=None):
        envelope = _run_real_or_mock(base, os.environ)
        d = envelope["data"]
        result = {
            "summary": envelope["summary"],
            "verdict": d["verdict"],
            "findings": d["findings"],
        }
        for key in ("digest", "narrative", "roadmap", "healthScore", "staggerPlan", "correlations", "forecast"):
            if d.get(key):
                result[key] = d[key]
        return result

    def list_workspaces_handler(_input=None):
        """Return all workspaces, items, and users from live sources (LA + Eventhouse).
        Use this to answer questions about workspace inventory, activity, or top users
        across the full estate without running the full audit pipeline."""
        collector = _build_collector(os.environ)
        if collector is None:
            # No live source — do NOT return mock workspaces as if they were real (an inventory tool
            # that invents an estate is worse than one that says it can't see the estate).
            return {"workspaces": [], "topUsers": [], "totalWorkspaces": 0, "totalItems": 0,
                    "note": ("No live telemetry source configured. Set FABRIC_LA_WORKSPACE_ID "
                             "(tenant-wide Log Analytics) or FABRIC_KUSTO_CLUSTER + FABRIC_KUSTO_DB "
                             "(per-workspace Eventhouse) to inventory real workspaces."),
                    "source": "none"}
        facts = collector["collect"]()

        items = facts.get("items") or []
        users = facts.get("users") or []

        # Group items by workspace
        ws_map = {}
        for item in items:
            ws = item.get("workspace") or "Unknown"
            entry = ws_map.setdefault(ws, {"workspace": ws, "items": [], "totalCuSeconds": 0})
            entry["items"].append({
                "name": item.get("name"),
                "cuSeconds": item.get("cuSeconds", 0),
                "sharePct": round(item.get("sharePct", 0), 1),
                "topUsers": item.get("topUsers", []),
                "userCount": item.get("userCount", 0),
            })
            entry["totalCuSeconds"] += item.get("cuSeconds", 0)

        workspaces = sorted(ws_map.values(), key=lambda x: -x["totalCuSeconds"])
        capped_workspaces, cap_meta = _cap_rows(workspaces)
        return _finish({
            "workspaces": capped_workspaces,
            "topUsers": users[:10],
            "totalWorkspaces": len(workspaces),
            "totalItems": len(items),
            "source": "Log Analytics + Eventhouse (merged)",
        }, rows_key="workspaces", extra=cap_meta)

    def user_activity_handler(_input=None):
        """Return ranked top users (no arg) or a specific user's detail (user arg).
        Falls back to the offline mock estate when no live source is configured — labeled
        ``source: "mock"`` so callers never mistake fixture data for the real estate."""
        facts = _collector_or_mock()["collect"]()
        cov = build_coverage(facts)
        # Authoritative live-vs-mock signal is whether a real source is CONFIGURED — not the
        # data shape (the mock fixture has data, so coverage.mode alone would read "live").
        source = "live" if _has_live_source(os.environ) else "mock"
        users = facts.get("users") or []
        who = (_input or {}).get("user")
        if who:
            u = next((x for x in users if (x.get("user") or "").lower() == who.lower()), None)
            return {"user": who, "found": u is not None, "detail": u,
                    "source": source, "coverage": cov}
        return {"topUsers": users[:10], "userCount": len(users),
                "source": source, "coverage": cov}

    def investigate_user_handler(_input=None):
        """Investigate a specific user's contribution to capacity: assembles evidence, baselines,
        and returns a grounded explanation. Abstains when the user is not in the collected data."""
        inp = _input or {}
        result = _iu(_collector_or_mock(), create_investigation_reasoner(),
                     inp.get("user"), days=inp.get("days", 30))
        result["source"] = "live" if _has_live_source(os.environ) else "mock"
        return result

    def investigate_spike_handler(_input=None):
        """Investigate a capacity spike: identifies top-consuming items/users and explains
        the spike with evidence. Abstains when no capacity signal is available."""
        inp = _input or {}
        result = _ics(_collector_or_mock(), create_investigation_reasoner(), inp.get("when"))
        result["source"] = "live" if _has_live_source(os.environ) else "mock"
        return result

    # ------------------------------------------------------------------
    # Phase-3 event helpers
    # ------------------------------------------------------------------
    # Small mock event fixture — a handful of normalize_event-shaped dicts plus
    # a tiny capacity_series used when no live event collector is configured.
    _MOCK_EVENTS = [
        _events_mod.normalize_event({
            "TimeGenerated": "2026-06-30T09:00:00Z", "ExecutingUser": "alice@co",
            "ArtifactName": "Sales", "OperationName": "QueryEnd", "CpuTimeMs": 8000,
            "EventText": "EVALUATE TOPN(100, Sales, [Revenue])",
        }),
        _events_mod.normalize_event({
            "TimeGenerated": "2026-06-30T09:05:00Z", "ExecutingUser": "alice@co",
            "ArtifactName": "Sales", "OperationName": "QueryEnd", "CpuTimeMs": 12000,
            "EventText": "EVALUATE CALCULATETABLE(Sales, DATESINPERIOD(Sales[Date], TODAY(), -90, DAY))",
        }),
        _events_mod.normalize_event({
            "TimeGenerated": "2026-06-30T09:10:00Z", "ExecutingUser": "bob@co",
            "ArtifactName": "Inventory", "OperationName": "QueryEnd", "CpuTimeMs": 5000,
        }),
        _events_mod.normalize_event({
            "TimeGenerated": "2026-06-30T09:12:00Z", "ExecutingUser": "carol@co",
            "ArtifactName": "Inventory", "OperationName": "QueryEnd", "CpuTimeMs": 6000,
        }),
        _events_mod.normalize_event({
            "TimeGenerated": "2026-06-30T09:14:00Z", "ExecutingUser": "dave@co",
            "ArtifactName": "HR", "OperationName": "CommandEnd", "DurationMs": 20000,
        }),
        _events_mod.normalize_event({
            "TimeGenerated": "2026-06-30T09:14:30Z", "ExecutingUser": "eve@co",
            "ArtifactName": "Finance", "OperationName": "QueryEnd", "CpuTimeMs": 30000,
            "EventText": "EVALUATE CALCULATETABLE(Transactions, DATESINPERIOD(Transactions[Date], TODAY(), -365, DAY))",
        }),
    ]
    _MOCK_CAPACITY_SERIES = [
        {"ts": "2026-06-30T09:00:00Z", "cuPct": 55.0},
        {"ts": "2026-06-30T09:10:00Z", "cuPct": 85.0},
        {"ts": "2026-06-30T09:15:00Z", "cuPct": 72.0},
    ]

    def _series_window(days, hours):
        """Bare KQL lookback string (e.g. "7d"/"6h") for the capacity-series collector, which
        interpolates it directly into ``ago(...)`` (unlike the event collector, it does not take
        a full WHERE clause / absolute between() window -- see collector_capacity_events._default_kql).
        Mirrors resolve_window's own hours-over-days precedence, defaulting to "30d"."""
        if hours is not None:
            return f"{hours}h"
        if days is not None:
            return f"{days}d"
        return "30d"

    def _events_or_mock(*, days=None, hours=None, start=None, end=None, user=None, item=None):
        """Yield ``(events, capacity_series, meta)``. Live LA event collector + capacity CU% series
        when ``FABRIC_LA_WORKSPACE_ID`` + ``FABRIC_CLIENT_ID`` are configured; else the small
        offline mock. Live requests are bounded (window from ``days``/``hours``/``start``+``end``,
        capped row count) and scoped to ``user``/``item`` when given -- never an unbounded
        whole-estate pull from a live request; that mining belongs in the scheduled Job.

        ``meta`` = ``{"eventKql": <built event kql, live only>, "windowLabel": <resolve_window
        label>, "seriesWindowLabel": <capacity-series window label>}``. On the mock path
        ``eventKql`` is None but ``windowLabel`` still reflects what was actually asked, so a
        caller can see the requested window even when it fell back to the fixture.

        Raises ``ValueError`` on a malformed ``start``/``end`` (propagated from resolve_window);
        callers wrap this in a try/except to return an error envelope instead of crashing.
        """
        window = _resolve_window(days=days, hours=hours, start=start, end=end)
        env = os.environ
        if not _has_live_event_source(env):
            meta = {"eventKql": None, "windowLabel": window["label"], "seriesWindowLabel": window["label"]}
            return _MOCK_EVENTS, _MOCK_CAPACITY_SERIES, meta

        from .job import _require
        from .adapters.clients import build_log_analytics_query
        from .adapters.collector_events_la import create_event_collector

        la_query = build_log_analytics_query(
            env["FABRIC_LA_WORKSPACE_ID"], _require(env, "FABRIC_TENANT_ID"),
            env["FABRIC_CLIENT_ID"], _require(env, "FABRIC_CLIENT_SECRET"),
        )
        event_cfg = {"window": window["clause"], "cap": 5000}
        if user:
            event_cfg["user"] = user
        if item:
            event_cfg["item"] = item
        if env.get("FABRIC_EVENT_OPERATIONS"):
            event_cfg["operations"] = [
                op.strip() for op in env["FABRIC_EVENT_OPERATIONS"].split(",") if op.strip()
            ]
        collector = create_event_collector(la_query, event_cfg)
        events = collector["collect"]()

        series_window_label = window["label"]
        series = []
        if env.get("FABRIC_CAPACITY_EVENTS_CLUSTER") and env.get("FABRIC_CAPACITY_EVENTS_DB"):
            from .adapters.clients import build_kusto_query
            ce_query = build_kusto_query(
                env["FABRIC_CAPACITY_EVENTS_CLUSTER"], env["FABRIC_CAPACITY_EVENTS_DB"],
                _require(env, "FABRIC_TENANT_ID"), env["FABRIC_CLIENT_ID"], _require(env, "FABRIC_CLIENT_SECRET"),
            )
            series_window = _series_window(days, hours)
            ce_cfg = {"window": series_window}
            if env.get("FABRIC_CAPACITY_EVENTS_TABLE"):
                ce_cfg["table"] = env["FABRIC_CAPACITY_EVENTS_TABLE"]
            # Honor the same KQL override job.py passes -- the deployed MCP app uses it to flatten
            # the nested ``data`` envelope; skipping it here would diverge from the known-good path.
            if env.get("FABRIC_CAPACITY_EVENTS_KQL"):
                ce_cfg["kql"] = env["FABRIC_CAPACITY_EVENTS_KQL"]
            series = _capacity_cu_series(ce_query, ce_cfg)
            series_window_label = f"last {series_window}"

        meta = {
            "eventKql": collector["kql"],
            "windowLabel": window["label"],
            "seriesWindowLabel": series_window_label,
        }
        return events, series, meta

    def user_spike_history_handler(_input=None):
        """Per-user spike history: every high-cost event, counts, time-of-day, workload split."""
        inp = _input or {}
        try:
            user = inp.get("user") or ""
            events, _series, meta = _events_or_mock(
                days=inp.get("days"), hours=inp.get("hours"),
                start=inp.get("start"), end=inp.get("end"),
                user=user.lower() or None,
            )
            result = _user_spike_history(events, user.lower())
            result["source"] = "live" if _has_live_event_source(os.environ) else "mock"
            capped_spikes, cap_meta = _cap_rows(result["spikes"])
            result["spikes"] = capped_spikes
            cap_meta["windowLabel"] = meta["windowLabel"]
            return _finish(result, rows_key="spikes", kql=meta["eventKql"], extra=cap_meta)
        except ValueError as exc:
            return {"error": str(exc), "source": "live" if _has_live_event_source(os.environ) else "mock"}

    def spike_events_handler(_input=None):
        """Ranked spike events across the estate: top-N by cuSeconds, each with
        {user, item, ts, cuSeconds, queryText}.  queryText carries the truncated
        DAX/query text from the raw event (None when absent).  Uses the canonical
        compute_baseline p95 (not a hand-rolled percentile index).  ``format`` selects
        "records" (default, list[dict]) or "columnar" (token-cheaper column-major shape)."""
        inp = _input or {}
        try:
            top_n = inp.get("topN") if inp.get("topN") is not None else 5
            events, _series, meta = _events_or_mock(
                days=inp.get("days"), hours=inp.get("hours"),
                start=inp.get("start"), end=inp.get("end"),
            )
            baseline = _compute_baseline(events)
            p95_all = baseline.get("p95") if baseline.get("p95") is not None else 0
            spike_list = [
                e for e in events
                if _events_mod.is_spike(e, p95=p95_all, floor_cu=None)
            ]
            capped_spike_list, cap_meta = _cap_rows(spike_list)
            result_events = _top_expensive(capped_spike_list, n=top_n)
            cap_meta["windowLabel"] = meta["windowLabel"]
            out = _finish({
                "events": result_events,
                "source": "live" if _has_live_event_source(os.environ) else "mock",
            }, rows_key="events", kql=meta["eventKql"], extra=cap_meta)
            if inp.get("format") == "columnar":
                # rowCount must stay the TRUE row count (finish already computed it above from the
                # records list) -- only the events value itself becomes column-major.
                out["events"] = _to_columnar(result_events)
            return out
        except ValueError as exc:
            return {"error": str(exc), "source": "live" if _has_live_event_source(os.environ) else "mock"}

    def capacity_patterns_handler(_input=None):
        """Temporal activity-surge ↔ CU-spike patterns across the estate."""
        inp = _input or {}
        try:
            events, capacity_series, meta = _events_or_mock(
                days=inp.get("days"), hours=inp.get("hours"),
                start=inp.get("start"), end=inp.get("end"),
            )
            patterns = _capacity_patterns(events, capacity_series)
            return {
                "patterns": patterns,
                "source": "live" if _has_live_event_source(os.environ) else "mock",
                "windowLabel": meta["windowLabel"],
                "seriesWindowLabel": meta["seriesWindowLabel"],
                "queryKql": meta["eventKql"],
            }
        except ValueError as exc:
            return {"error": str(exc), "source": "live" if _has_live_event_source(os.environ) else "mock"}

    # Shared sub-day / absolute time-window properties for the 3 event tools (user_spike_history,
    # spike_events, capacity_patterns) -- merged into each tool's "days"-carrying input_schema so
    # a caller can ask for "last 6 hours" or an absolute "12:45pm-1pm yesterday" window, not just
    # a whole-days lookback. Precedence (see query.windows.resolve_window): start+end > hours > days.
    _WINDOW_PROPS = {
        "hours": {
            "type": "number",
            "description": (
                "Lookback window in hours, overrides 'days' when given. Fractional values are "
                "supported (e.g. 0.25 = last 15 minutes, for a 'right now' query)."
            ),
        },
        "start": {
            "type": "string",
            "description": (
                "Absolute window start, ISO-8601 (e.g. '2026-07-05T12:45:00Z'). Requires 'end'; "
                "when both are given they override 'hours'/'days'."
            ),
        },
        "end": {
            "type": "string",
            "description": "Absolute window end, ISO-8601. Requires 'start'.",
        },
    }

    return [
        {
            "name": "run_audit",
            "description": (
                "Run a read-only Fabric/Power BI capacity audit and return prioritized findings, "
                "capacity verdict (optimize vs size-up), health score, and per-user attribution. "
                "Use this for capacity health questions, throttling analysis, and optimization advice. "
                "Read-only: never modifies anything."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": run_audit_handler,
        },
        {
            "name": "list_workspaces",
            "description": (
                "List all workspaces, their items, and top users from live sources (Log Analytics "
                "and/or Workspace Monitoring Eventhouse). Use this to answer questions about workspace "
                "inventory, activity across the estate, who is using which workspace, or to find a "
                "specific workspace before drilling into it with run_audit."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": list_workspaces_handler,
        },
        {
            "name": "user_activity",
            "description": (
                "Return per-user activity data. With no arguments, returns the ranked top users "
                "by monitored CU (a CPU-time proxy, not authoritative capacity CU). With a 'user' "
                "argument, returns that user's detail (items, "
                "sharePct, cuSeconds). Falls back to the offline mock estate when no live source "
                "is configured. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Optional user UPN/email to look up."},
                },
                "required": [],
            },
            "handler": user_activity_handler,
        },
        {
            "name": "investigate_user",
            "description": (
                "Investigate a specific user's contribution to capacity: assembles evidence from "
                "collectors + detectors, computes coverage and confidence, and returns a grounded "
                "explanation. Abstains (abstained: true) when the user is not present in the "
                "collected data rather than guessing. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "User UPN/email to investigate (required)."},
                    "days": {"type": "integer", "description": "Lookback window in days (default 30)."},
                },
                "required": ["user"],
            },
            "handler": investigate_user_handler,
        },
        {
            "name": "investigate_capacity_spike",
            "description": (
                "Investigate a capacity spike: identifies the top-consuming items and users, "
                "assembles capacity evidence, and returns a grounded explanation with confidence "
                "rating. Abstains when no capacity signal (peakCuPct) is available. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "Optional ISO timestamp or label for the spike window."},
                },
                "required": [],
            },
            "handler": investigate_spike_handler,
        },
        {
            "name": "user_spike_history",
            "description": (
                "Return per-user spike history: every high-cost event above the user's own p95 baseline, "
                "with counts, timestamps, items, time-of-day distribution, and interactive-vs-refresh split. "
                "Falls back to a small offline mock when no live event collector is configured. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "User UPN/email to look up (required)."},
                    "days": {"type": "integer", "description": "Lookback window in days (default 30)."},
                    **_WINDOW_PROPS,
                },
                "required": ["user"],
            },
            "handler": user_spike_history_handler,
        },
        {
            "name": "spike_events",
            "description": (
                "Return the top-N most expensive spike events across the estate, ranked by cuSeconds "
                "descending. Each entry carries user, item, ts, and cuSeconds — not averages. "
                "Use this to find which specific operations drove CU spikes. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Lookback window in days (default 30)."},
                    "topN": {"type": "integer", "description": "Maximum events to return (default 5)."},
                    "format": {
                        "type": "string",
                        "enum": ["records", "columnar"],
                        "description": (
                            "Output shape for 'events': 'records' (default, list of row dicts) or "
                            "'columnar' (token-cheaper column-major {columns: {name: [values...]}})."
                        ),
                    },
                    **_WINDOW_PROPS,
                },
                "required": [],
            },
            "handler": spike_events_handler,
        },
        {
            "name": "capacity_patterns",
            "description": (
                "Identify temporal patterns coupling activity surges with CU% spikes. "
                "Returns one pattern per detected surge-spike pair with the driving item, user, "
                "peak CU%, and a plain-English narrative. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Lookback window in days (default 30)."},
                    **_WINDOW_PROPS,
                },
                "required": [],
            },
            "handler": capacity_patterns_handler,
        },
    ]

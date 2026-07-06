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
from .timefmt import add_display_time

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


# Max raw events per live query. The KQL is deterministic ``top`` (by cost by default, by
# recency for time-bucketed analysis); handlers surface ``truncated: true`` when the cap is
# hit so callers know the window was not fully covered.
_EVENT_CAP = 5000

# query-callable memo — building a client per call creates a fresh MSAL ConfidentialClientApplication
# each time, so its internal token cache never helps (an AAD round-trip per tool call, plus
# throttling exposure). Keyed on the full credential tuple so a rotated secret naturally misses.
_CLIENT_CACHE = {}


def _memo_client(key, builder):
    if key not in _CLIENT_CACHE:
        _CLIENT_CACHE[key] = builder()
    return _CLIENT_CACHE[key]


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


def _build_collector(env, window=None):
    """Return a live collector if any source is configured, else None. ``window`` (e.g. "7d")
    overrides every source's lookback -- used by tools that thread a ``days`` argument."""
    if not _has_live_source(env):
        return None
    from .job import build_collector_from_env
    return build_collector_from_env(env, window=window)


def create_tool_definitions(base_dir=None):
    base = base_dir if base_dir is not None else _BASE

    def _collector_or_mock(days=None):
        """Return a live collector if any source is configured, else the offline mock estate.
        ``days`` threads into every live source's lookback window (ignored on the mock path)."""
        window = f"{int(days)}d" if days else None
        col = _build_collector(os.environ, window=window)
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
        # Raw `when` stays UTC ISO for machine use; whenDisplay is the canonical display twin
        # ("<UTC> (<Eastern>)") so the agent quotes one consistent format and never does its
        # own timezone math.
        for f in result["findings"]:
            add_display_time(f, "when", "whenDisplay")
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
        return {
            "workspaces": workspaces,
            "topUsers": users[:10],
            "totalWorkspaces": len(workspaces),
            "totalItems": len(items),
            "source": "Log Analytics + Eventhouse (merged)",
        }

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
        result = _iu(_collector_or_mock(days=inp.get("days")), create_investigation_reasoner(),
                     inp.get("user"), days=inp.get("days", 30))
        result["source"] = "live" if _has_live_source(os.environ) else "mock"
        return result

    def investigate_spike_handler(_input=None):
        """Investigate a capacity spike: identifies top-consuming items/users and explains
        the spike with evidence. With `when`, additionally scopes per-event telemetry to the
        ±30-minute window around that moment (refresh-vs-interactive attribution of THE peak).
        Abstains when no capacity signal is available."""
        inp = _input or {}
        when = inp.get("when")
        events = series = None
        if when:
            ev_events, ev_series, ev_meta = _events_or_mock(days=inp.get("days", 7), order="recent")
            if not ev_meta["error"]:
                events, series = ev_events, ev_series
        result = _ics(_collector_or_mock(days=inp.get("days")), create_investigation_reasoner(),
                      when, events=events, capacity_series=series)
        result["source"] = "live" if _has_live_source(os.environ) else "mock"
        # Decorate the window evidence's top events with the canonical display twin.
        for e_item in result.get("evidence") or []:
            if e_item.get("kind") == "window":
                add_display_time(e_item.get("data") or {}, "when", "whenDisplay")
                for te in (e_item.get("data") or {}).get("topEvents") or []:
                    add_display_time(te, "ts", "tsDisplay")
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

    def _events_or_mock(*, days=None, user=None, item=None, order=None):
        """Yield ``(events, capacity_series, meta)``. Live LA event collector + capacity CU% series
        when ``FABRIC_LA_WORKSPACE_ID`` + ``FABRIC_CLIENT_ID`` are configured; else the small
        offline mock. Live requests are bounded (window from ``days``, capped row count) and scoped
        to ``user``/``item`` when given -- never an unbounded whole-estate pull from a live request;
        that mining belongs in the scheduled Job.

        ``meta``: ``error`` (str -- the LA event query failed; events/series are empty and handlers
        must return an honest error payload, not zeros dressed as data), ``seriesError`` (str -- the
        CU% series query failed; events are still good, patterns degrade), ``truncated`` (bool --
        the event cap was hit, so the window is only partially covered by the newest events)."""
        env = os.environ
        meta = {"truncated": False, "error": None, "seriesError": None}
        if not _has_live_event_source(env):
            return _MOCK_EVENTS, _MOCK_CAPACITY_SERIES, meta

        from .job import _require
        from .adapters.clients import build_log_analytics_query
        from .adapters.collector_events_la import create_event_collector

        window = f"{int(days)}d" if days else "30d"
        tenant = _require(env, "FABRIC_TENANT_ID")
        secret = _require(env, "FABRIC_CLIENT_SECRET")
        la_query = _memo_client(
            ("la", env["FABRIC_LA_WORKSPACE_ID"], tenant, env["FABRIC_CLIENT_ID"], secret),
            lambda: build_log_analytics_query(
                env["FABRIC_LA_WORKSPACE_ID"], tenant, env["FABRIC_CLIENT_ID"], secret),
        )
        event_cfg = {"window": window, "cap": _EVENT_CAP}
        if user:
            event_cfg["user"] = user
        if item:
            event_cfg["item"] = item
        if order:
            event_cfg["order"] = order   # "recent" for time-bucketed analysis; default "cost"
        # Optional OperationName allowlist (comma-separated env) — restrict to top-level ops
        # (QueryEnd/CommandEnd/ProgressReportEnd) AFTER verifying live op names, to drop VertiPaq
        # SE sub-query children that double-count cost. Off by default: an unverified allowlist
        # on a tenant with different op names would silently return nothing.
        ops = env.get("FABRIC_EVENT_OPERATIONS")
        if ops:
            event_cfg["operations"] = [o.strip() for o in ops.split(",") if o.strip()]
        try:
            events = create_event_collector(la_query, event_cfg)["collect"]()
        except Exception as exc:   # auth/timeout/transient -- surface honestly, don't crash the tool
            meta["error"] = f"Log Analytics event query failed: {exc}"
            return [], [], meta
        meta["truncated"] = len(events) >= _EVENT_CAP

        series = []
        if env.get("FABRIC_CAPACITY_EVENTS_CLUSTER") and env.get("FABRIC_CAPACITY_EVENTS_DB"):
            from .adapters.clients import build_kusto_query
            ce_query = _memo_client(
                ("kusto", env["FABRIC_CAPACITY_EVENTS_CLUSTER"], env["FABRIC_CAPACITY_EVENTS_DB"],
                 tenant, env["FABRIC_CLIENT_ID"], secret),
                lambda: build_kusto_query(
                    env["FABRIC_CAPACITY_EVENTS_CLUSTER"], env["FABRIC_CAPACITY_EVENTS_DB"],
                    tenant, env["FABRIC_CLIENT_ID"], secret),
            )
            ce_cfg = {"window": window}
            if env.get("FABRIC_CAPACITY_EVENTS_TABLE"):
                ce_cfg["table"] = env["FABRIC_CAPACITY_EVENTS_TABLE"]
            # Honor the same KQL override job.py passes -- the deployed MCP app uses it to flatten
            # the nested ``data`` envelope. The collector substitutes {window} in the override, so
            # the threaded lookback is respected (a hardcoded ago(...) used to defeat ``days``).
            if env.get("FABRIC_CAPACITY_EVENTS_KQL"):
                ce_cfg["kql"] = env["FABRIC_CAPACITY_EVENTS_KQL"]
            try:
                series = _capacity_cu_series(ce_query, ce_cfg)
            except Exception as exc:   # events are still good; only patterns degrade
                meta["seriesError"] = f"capacity CU% series query failed: {exc}"

        return events, series, meta

    def _event_source_label():
        return "live" if _has_live_event_source(os.environ) else "mock"

    def user_spike_history_handler(_input=None):
        """Per-user spike history: every high-cost event, counts, time-of-day, workload split."""
        inp = _input or {}
        user = inp.get("user") or ""
        events, _, meta = _events_or_mock(days=inp.get("days"), user=user.lower() or None,
                                          item=inp.get("item"))
        if meta["error"]:
            return {"user": user, "error": meta["error"], "source": _event_source_label()}
        result = _user_spike_history(events, user.lower())
        result["source"] = _event_source_label()
        if meta["truncated"]:
            result["truncated"] = True   # cap hit: costliest events only, counts are a floor
        for s in result.get("spikes") or []:
            add_display_time(s, "ts", "tsDisplay")
        return result

    def spike_events_handler(_input=None):
        """Ranked spike events across the estate: top-N by cuSeconds, each with
        {user, item, ts, cuSeconds, queryText}.  queryText carries the truncated
        DAX/query text from the raw event (None when absent).  Uses the canonical
        compute_baseline p95 (not a hand-rolled percentile index)."""
        inp = _input or {}
        top_n = inp.get("topN") if inp.get("topN") is not None else 5
        events, _, meta = _events_or_mock(days=inp.get("days"), item=inp.get("item"))
        if meta["error"]:
            return {"events": [], "error": meta["error"], "source": _event_source_label()}
        baseline = _compute_baseline(events)
        p95_all = baseline.get("p95") if baseline.get("p95") is not None else 0
        spike_list = [
            e for e in events
            if _events_mod.is_spike(e, p95=p95_all, floor_cu=None)
        ]
        result = {
            "events": _top_expensive(spike_list, n=top_n),
            "source": _event_source_label(),
        }
        if meta["truncated"]:
            result["truncated"] = True   # ranking covers the costliest _EVENT_CAP events only
        for e in result["events"]:
            add_display_time(e, "ts", "tsDisplay")
        return result

    def capacity_patterns_handler(_input=None):
        """Temporal activity-surge ↔ CU-spike patterns across the estate."""
        inp = _input or {}
        # order="recent": bucketed surge detection needs CONTIGUOUS time coverage under the cap;
        # the default cost-order would leave time gaps and fabricate/miss surges when truncated.
        events, capacity_series, meta = _events_or_mock(days=inp.get("days"), order="recent")
        if meta["error"]:
            return {"patterns": [], "error": meta["error"], "source": _event_source_label()}
        result = {
            "patterns": _capacity_patterns(events, capacity_series),
            "source": _event_source_label(),
        }
        if meta["seriesError"]:
            result["seriesError"] = meta["seriesError"]   # events fine; CU% coupling unavailable
        if meta["truncated"]:
            result["truncated"] = True
        for p in result["patterns"]:
            add_display_time(p, "windowStart", "windowStartDisplay")
        return result

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
                "rating. Pass `when` (the spike's timestamp) to additionally analyze the ±30-minute "
                "window around that exact moment from per-event telemetry: interactive-vs-refresh CU "
                "split, distinct users, and the top driving events — answers whether THAT peak was a "
                "refresh or interactive load. Abstains when no capacity signal is available. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "when": {"type": "string",
                             "description": ("Spike timestamp — ISO UTC (2026-07-06T15:48:00Z) or "
                                             "'YYYY-MM-DD HH:MM UTC'. Scopes event analysis to ±30 min.")},
                    "days": {"type": "integer",
                             "description": "Event lookback in days used to find the window (default 7)."},
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
                    "item": {"type": "string",
                             "description": "Optional item/artifact name to scope to (e.g. one semantic model)."},
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
                    "item": {"type": "string",
                             "description": "Optional item/artifact name to scope to (e.g. one semantic model)."},
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
                },
                "required": [],
            },
            "handler": capacity_patterns_handler,
        },
    ]

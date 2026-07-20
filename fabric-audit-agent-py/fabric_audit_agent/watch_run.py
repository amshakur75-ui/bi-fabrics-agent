"""Autonomous watcher — the 5-minute Job entry point.

Pull-only until now; this is the PUSH loop. Each run: confirm the LIVE base capacity (SKU-first),
pull the recent capacity stream + per-operation events, evaluate the approved triggers, dedup
against prior runs, and POST a two-way Adaptive Card per NEW incident to the Teams Workflows
webhook. Stays SILENT when nothing new fires.

Read-only + safe-outbound: the ONLY side effect is posting alert cards to the configured webhook
(no writes/refreshes/scale). ``plan_watch`` is pure and offline-testable; ``main`` wires the live
sources and delivery.

Config (job parameters / env):
  FABRIC_WATCH_WEBHOOK_URL   Power Automate Workflows webhook (required to deliver).
  FABRIC_WATCH_LOOKBACK      KQL lookback for the pull (default "15m" -- overlaps the 5-min cadence
                             so nothing is missed between runs; dedup handles the overlap).
  FABRIC_WATCH_STATE_PATH    JSON file for cross-run dedup state (default /tmp/fabric-watch-state.json;
                             use a Volume path on the Job so it survives restarts).
  FABRIC_WATCH_CU_PCT        capacity trigger (default 100).
  FABRIC_WATCH_OP_PCT        operation trigger, CONVERTED % (default 30 == 300% lifetime).
  FABRIC_WATCH_SUSTAINED     windows to call an overage "sustained" (default 3 == ~90s).
"""
import json
import os
import time

from .investigation.watch import evaluate_incidents, new_incidents
from .investigation.overloads import overload_windows
from .investigation.timepoint_peaks import timepoint_peaks
from .timefmt import to_display, parse_iso_utc


def _events_to_ops(events):
    ops = []
    for e in events or []:
        end = parse_iso_utc(e.get("ts"))
        if end is None:
            continue
        end_ep = end.timestamp()
        dur_s = (e.get("durationMs") or 0) / 1000.0
        ops.append({"startEpoch": end_ep - dur_s, "endEpoch": end_ep,
                    "cuSeconds": e.get("cuSeconds"), "user": e.get("user"),
                    "item": e.get("item"), "operation": e.get("operation")})
    return ops


def _series_epoch(series_raw):
    out = []
    for pt in series_raw or []:
        dt = parse_iso_utc(pt.get("ts"))
        if dt is not None and pt.get("cuPct") is not None:
            out.append({"epoch": dt.timestamp(), "cuPct": pt["cuPct"]})
    return out


def plan_watch(series_raw, events, *, base_cu, seen_ids=(), cu_pct=100.0,
               op_pct_converted=30.0, sustained_windows=3):
    """PURE: given the raw capacity series + normalized events + live base, return the NEW
    incidents to alert on (already deduped against ``seen_ids``). Offline-testable."""
    series = _series_epoch(series_raw)
    ops = _events_to_ops(events)
    windows = overload_windows(series, ops, base_cu=base_cu, min_cu_pct=0.0, top_windows=1000)
    # Pre-bound the peaks to ops at/over the converted trigger (converted N% == lifetime N*10%),
    # then evaluate_incidents re-checks the converted value exactly.
    peaks = timepoint_peaks(events, base_cu=base_cu, top_n=200,
                            min_pct=(op_pct_converted * 10 if base_cu else None), lens="lifetime",
                            include_refresh=True)
    incidents = evaluate_incidents(windows, peaks, base_cu=base_cu, cu_pct=cu_pct,
                                   op_pct_converted=op_pct_converted,
                                   sustained_windows=sustained_windows)
    return new_incidents(incidents, seen_ids)


def _load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {"seen": {}}


def _save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _sample_incident():
    """A representative incident for the --test-card path (prove the webhook without waiting for a
    real spike). Clearly labelled as a test."""
    return {
        "kind": "capacity", "id": "test-card", "severity": "warn", "emoji": "⚠️",
        "title": "⚠️ TEST — Capacity watcher connectivity check",
        "summary": "TEST card — the Fabric watcher can reach this channel",
        "why": ("This is a one-off test of the autonomous watcher's Teams pipe. If you can see the "
                "Acknowledge / Snooze / Explain choices and submit one, the two-way round-trip works. "
                "Real alerts will look like this, with the actual capacity/operation details."),
        "facts": [{"title": "Status", "value": "connectivity test"},
                  {"title": "Read-only", "value": "yes — alerts only, no changes"}],
        "whenDisplay": None,
    }


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ
    webhook = env.get("FABRIC_WATCH_WEBHOOK_URL")

    def _deliver(incident):
        if not webhook:
            print("[watch] no FABRIC_WATCH_WEBHOOK_URL set -- would deliver:",
                  json.dumps(incident.get("summary"), ensure_ascii=False))
            return
        from .adapters.clients import PlainJsonHttp
        from .adapters.delivery_teams import create_watch_delivery
        delivery = create_watch_delivery(PlainJsonHttp(), webhook)
        delivery["deliverIncident"](incident)

    # --test-card: deliver a single sample card and exit (webhook proof).
    if "--test-card" in argv:
        inc = _sample_incident()
        _deliver(inc)
        print("[watch] test card delivered" if webhook else "[watch] test card built (no webhook)")
        return 0

    # ---- live run ----
    from .tools import _live_base_cu   # module-level; reads baseCapacityUnits fresh
    base_cu = _live_base_cu(env)
    lookback = env.get("FABRIC_WATCH_LOOKBACK", "15m")
    cu_pct = float(env.get("FABRIC_WATCH_CU_PCT", "100"))
    op_pct = float(env.get("FABRIC_WATCH_OP_PCT", "30"))
    sustained = int(env.get("FABRIC_WATCH_SUSTAINED", "3"))
    state_path = env.get("FABRIC_WATCH_STATE_PATH", "/tmp/fabric-watch-state.json")

    series_raw, events = _pull_live(env, lookback)
    seen = _load_state(state_path).get("seen", {})
    fresh = plan_watch(series_raw, events, base_cu=base_cu, seen_ids=seen.keys(),
                       cu_pct=cu_pct, op_pct_converted=op_pct, sustained_windows=sustained)

    now = int(time.time())
    for inc in fresh:
        when = inc.get("when") or (
            None if inc.get("whenEpoch") is None
            else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(inc["whenEpoch"])))
        if when:
            disp = to_display(when)
            if disp:
                inc["whenDisplay"] = disp
        _deliver(inc)
        seen[inc["id"]] = now

    # prune dedup state older than 6h so ids can re-fire if a problem recurs later.
    seen = {k: v for k, v in seen.items() if now - v < 6 * 3600}
    _save_state(state_path, {"seen": seen})
    print(f"[watch] base_cu={base_cu} pulled series={len(series_raw)} events={len(events)} "
          f"newIncidents={len(fresh)}")
    return 0


def _pull_live(env, lookback):
    """Return (capacity_series_raw, normalized_events) from the live sources. Fault-tolerant: a
    failing source yields [] for that stream so the watcher still evaluates the other."""
    series_raw, events = [], []
    # capacity CU% series (total per 30s window)
    try:
        if env.get("FABRIC_CAPACITY_EVENTS_CLUSTER") and env.get("FABRIC_CAPACITY_EVENTS_DB"):
            from .tools import _capacity_kusto_query
            from .adapters.collector_capacity_events import capacity_series
            cfg = {"window": lookback}
            if env.get("FABRIC_CAPACITY_EVENTS_TABLE"):
                cfg["table"] = env["FABRIC_CAPACITY_EVENTS_TABLE"]
            if env.get("FABRIC_CAPACITY_EVENTS_KQL"):
                cfg["kql"] = env["FABRIC_CAPACITY_EVENTS_KQL"]
            series_raw = capacity_series(_capacity_kusto_query(env), cfg)
    except Exception as exc:
        print(f"[watch] capacity series pull failed: {type(exc).__name__}: {exc}")
    # per-operation events (all op types, minus SE double-counters)
    try:
        if env.get("FABRIC_LA_WORKSPACE_ID") and env.get("FABRIC_CLIENT_ID"):
            from .job import _require
            from .adapters.clients import build_log_analytics_query
            from .adapters.collector_events_la import create_event_collector
            la = build_log_analytics_query(
                env["FABRIC_LA_WORKSPACE_ID"], _require(env, "FABRIC_TENANT_ID"),
                env["FABRIC_CLIENT_ID"], _require(env, "FABRIC_CLIENT_SECRET"))
            collector = create_event_collector(la, {
                "window": f"| where TimeGenerated > ago({lookback})",
                "cap": 5000, "order": "cost", "excludePrefixes": ["VertiPaqSE"]})
            events = collector["collect"]()
    except Exception as exc:
        print(f"[watch] event pull failed: {type(exc).__name__}: {exc}")
    return series_raw, events


if __name__ == "__main__":
    raise SystemExit(main())

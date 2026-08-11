"""Round 9 P0s: three ways the product asserted something it had not established.

The adversarial sweep proved each of these by execution before I touched them.
"""
from agent_server.chart_tool import render_chart_spec
from fabric_audit_agent.investigation.throttle import decompose_throttle


def _series():
    return [{"name": "s", "data": [{"x": "09:00", "y": 1.0}, {"x": "10:00", "y": 2.0}]}]


# ---- the caveat is not the caller's to switch off --------------------------

def test_an_explicit_is_proxy_false_cannot_override_a_proxy_scope():
    """The DEFAULT was right, but an explicit `isProxy: false` was accepted with no clamp — and the
    tool schema invites it ("Capacity-scoped CU% is true CU -> false"), so a model charting "CU by
    item" plausibly sets false and renders a per-item CpuTimeMs donut labelled capacity CU with no
    caveat. One JSON field from the claim gates.true_cu_per_user_gate marks permanently blocked. The
    old code's asymmetry said it outright: a wrong `true` was guarded, a wrong `false` was not."""
    for scope in ("item", "user"):
        out = render_chart_spec({"chartType": "bar", "title": "t", "sourceScope": scope,
                                 "isProxy": False, "series": _series()})
        assert out["chart"]["isProxy"] is True, f"{scope} cost is always a proxy"
        assert out["chart"]["proxyCaveat"], "the caveat must survive a caller saying otherwise"


def test_capacity_scope_still_honours_an_explicit_flag():
    """Capacity-scoped CU% genuinely can be true CU, so the caller keeps its vote there."""
    out = render_chart_spec({"chartType": "line", "title": "t", "sourceScope": "capacity",
                             "isProxy": False, "series": _series()})
    assert out["chart"]["isProxy"] is False and "proxyCaveat" not in out["chart"]


# ---- no data is not a clean bill of health ---------------------------------

def test_an_empty_capacity_series_is_unknown_not_not_throttling():
    """`over` is empty both when the capacity was calm AND when nothing was collected, so a zero-row
    pull returned "CU% never exceeded the threshold — slowness has another cause". Reached live from
    capacity_diagnostics, where a zero-row pull raises nothing and records no error, so the agent
    reads it as a POSITIVE finding — steering an admin away from the cause during a real event."""
    for series in ([], [{"epoch": i, "cuPct": None} for i in range(12)]):
        out = decompose_throttle(series, [])
        assert out["conclusion"] == "unknown-no-data"
        assert out["stage2"]["available"] is False
        assert "NOT evidence that the capacity was healthy" in out["note"]


def test_a_genuinely_calm_capacity_still_says_not_throttling():
    """The fix must not turn every quiet window into an alarm."""
    out = decompose_throttle([{"epoch": i, "cuPct": 40.0} for i in range(12)], [])
    assert out["conclusion"] == "not-throttling"


def test_a_real_overload_is_unaffected():
    out = decompose_throttle([{"epoch": i, "cuPct": 247.0} for i in range(12)], [])
    assert out["conclusion"] == "over-utilized-unconfirmed"


# ---- a resolved ticket must stop counting ----------------------------------

def _ack_store(resolved_handles):
    return {"get": lambda h: ({"status": "resolved"} if h in resolved_handles else None)}


def test_a_ticket_a_human_resolved_is_not_counted_as_a_finding_today():
    """A Resolve in the notification center writes ONLY to alert_ack; nothing propagates it back to
    audit_alerts, and no sweep-family row is ever marked currentlyActive=False (only the capacity
    family maintains that flag). So every sweep ticket ever written stayed active forever: the app's
    badge dropped it the moment it was resolved (that query joins alert_ack) while the 6pm card
    still read "Findings today: 47" and listed it. A lifetime cumulative count including work
    already done — the same shape as the "Open tickets: 161" flood."""
    from fabric_audit_agent.automation.daily_summary import run_daily_summary

    rows = {
        "activity.slow-operation::aaron@newellco.com": {
            "incidentKey": "activity.slow-operation::aaron@newellco.com", "checkType": "activity",
            "status": "active", "currentlyActive": True, "severity": "warn",
            "resource": "aaron@newellco.com", "chatId": None},
        "model.bidirectional::Ent/Sales": {
            "incidentKey": "model.bidirectional::Ent/Sales", "checkType": "model",
            "status": "active", "currentlyActive": True, "severity": "warn",
            "resource": "Ent / Sales", "chatId": "chat-2"},
    }
    store = {"query_active": lambda: dict(rows), "query_pending": lambda: {},
             "query_informational": lambda: {}, "upsert": lambda r: None,
             "resolve": lambda k, t: None}

    both = run_daily_summary(alerts_store=store, ack_store=_ack_store(set()))
    assert both["openTickets"] == 2, "baseline: nothing resolved yet"

    # The chat-less one is resolved by INCIDENT KEY, which is the only handle it has.
    one = run_daily_summary(
        alerts_store=store,
        ack_store=_ack_store({"activity.slow-operation::aaron@newellco.com"}))
    assert one["openTickets"] == 1, "a resolved ticket must stop counting"

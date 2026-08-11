"""Round 10: the five P1s from the round-9 gate sweep.

Each was proven by execution before it was touched. The theme, again: output that is confident,
plausible and wrong — a ceiling projected from noise, a 100/100 score on a collection that saw
nothing, a chart with no x values, a blindness alarm nobody receives.
"""
import inspect

from fabric_audit_agent import job as job_mod
from fabric_audit_agent.automation.health import HealthReport
from fabric_audit_agent.detectors.concentration import detect_concentration
from fabric_audit_agent.forecast import forecast_capacity
from fabric_audit_agent.health_score import build_health_score
from fabric_audit_agent.kb import get_remediation
from fabric_audit_agent.severity import score_severity


def _hist(vals):
    return [{"metrics": {"peakCuPct": v}} for v in vals]


# ---- P1-2: do not project a ceiling from a flat line ----------------------

def test_a_flat_series_projects_no_ceiling_breach():
    """`runs_to_ceiling` was gated on slope > 0 while `trend` needs slope > 0.5, so a flat series
    rendered "At current trend (+0%/run), peak CU reaches 100% in ~1393 run(s)" — a sentence that
    contradicts its own +0%/run. pipeline.py surfaces the forecast only when runsToCeiling is set,
    making this the dominant shipped case."""
    out = forecast_capacity(_hist([60, 60, 60, 60, 60, 60.2]))
    assert out["trend"] == "flat"
    assert out["runsToCeiling"] is None
    assert "reaches" not in out["message"]


def test_noise_around_a_constant_projects_nothing():
    out = forecast_capacity(_hist([60, 61, 60, 62, 60, 61]))
    assert out["runsToCeiling"] is None


def test_a_genuine_climb_still_projects_and_carries_its_caveat():
    rising = forecast_capacity(_hist([40, 50, 60, 70, 80, 90]))
    assert rising["trend"] == "rising" and rising["runsToCeiling"] is not None
    # A noisy climb must still be able to say so — the caveat used to require trend != "flat"
    # AND was computed on a branch that could not reach it.
    noisy = forecast_capacity(_hist([40, 90, 45, 95, 50, 99]))
    if noisy["runsToCeiling"] is not None and noisy["weakFit"]:
        assert "weak fit" in noisy["message"]


# ---- P1-3: 100/100 is not a clean bill of health on a blind collection ----

def test_a_blind_collection_qualifies_its_perfect_score():
    """Zero findings is indistinguishable from a clean estate, so a degraded collection scored
    100/100 and the narrative said "Estate health is 100/100" — handed verbatim to the chat agent —
    while dataQuality simultaneously listed missing capacityId, sku, memoryGB and peakCuPct."""
    out = build_health_score([], data_quality=["missing capacityId", "missing peakCuPct"])
    assert out["overall"] == 100
    assert out["scoreQualified"] is True
    assert "not that the estate is healthy" in out["qualification"]


def test_a_clean_collection_is_not_qualified():
    assert "scoreQualified" not in build_health_score([], data_quality=[])
    assert "scoreQualified" not in build_health_score([])


def test_a_score_with_real_findings_is_not_qualified():
    """With findings the score already reflects real problems; the caveat would just add noise."""
    findings = [{"key": "model.bidirectional", "score": {"level": "Warning"}}]
    assert "scoreQualified" not in build_health_score(findings, data_quality=["missing sku"])


# ---- P1-5: an unmeasurable share is a coverage gap, not an all-clear ------

def test_every_item_unmeasurable_raises_a_coverage_flag():
    """A bare `continue` on sharePct=None treated "we could not measure" as "not concentrated", so a
    cost-column rename would take the flagship 30% feature out in total silence."""
    # Full rollup shape: production always sets userCount/truncated alongside the share.
    #
    # Three items, not two: the flag now has an activity floor (capacity.unmeasurableMinItems,
    # default 3), because without one every idle window with a single unpriced item minted a
    # `meta`-family ticket -- and `meta` IS in the notification center's ACTIONABLE set, so that was
    # a real to-do item every hour. The floor does not touch what this test is about: a cost column
    # that stops resolving on a window with actual work in it must still be caught, loudly.
    items = [{"name": n, "workspace": "Ent", "sharePct": None, "shareBasis": "unavailable",
              "cuSeconds": 0, "topUsers": [], "userCount": 0, "truncated": False,
              "attributionMode": "cost-duration"}
             for n in ("Ent-Reporting-DTC", "Ent-Reporting-Sales", "Ent-Reporting-Ops")]
    types = [f["type"] for f in detect_concentration({"items": items})]
    assert types == ["meta.attribution-unmeasurable"]


def test_a_near_idle_window_does_not_mint_a_coverage_ticket():
    """The other side of the floor. "We could price neither of the two things we saw" is not yet
    evidence that the cost column is broken, and it repeats every hour if it tickets."""
    items = [{"name": n, "workspace": "Ent", "sharePct": None, "shareBasis": "unavailable",
              "cuSeconds": 0, "topUsers": [], "userCount": 0, "truncated": False,
              "attributionMode": "cost-duration"}
             for n in ("Nightly-Refresh", "Ops-Ping")]
    assert detect_concentration({"items": items}) == []


def test_a_measurable_window_raises_no_coverage_flag():
    items = [{"name": "Ent-Reporting-DTC", "workspace": "Ent", "sharePct": 62.0,
              "cuSeconds": 4000.0, "attributionMode": "cost-cpu", "shareBasis": "cost",
              "topUsers": [{"user": "aaron@newellco.com", "cuSeconds": 3000.0}],
              "userCount": 1, "truncated": False}]
    types = [f["type"] for f in detect_concentration({"items": items})]
    assert "meta.attribution-unmeasurable" not in types


def test_the_new_type_is_scored_and_has_real_remediation():
    """MULTI-SITE: a new finding type needs a severity branch AND a KB entry, or it ships as
    Info (dropped by SWEEP_MIN_LEVEL="Warning") carrying developer placeholder text."""
    sev = score_severity({"type": "meta.attribution-unmeasurable", "evidence": {"itemsSeen": 2}})
    assert sev["level"] == "Warning", "Info would be dropped by the sweep's minimum level"
    assert "not yet in the knowledge base" not in str(get_remediation(
        "meta.attribution-unmeasurable"))


# ---- P1-1: a stale heartbeat must reach a human -------------------------

def test_the_sweep_fails_the_run_when_degraded():
    """_check_tier2_health records a STALE TIER2 HEARTBEAT into the sweep's HealthReport, but
    job_main only printed it — so on_failure could not fire and a paused tier2 job meant capacity
    alerting was dead with nobody told. tier2_main already raises; this is its twin."""
    src = inspect.getsource(job_mod.job_main)
    assert "health.degraded" in src and "raise RuntimeError" in src
    assert "FABRIC_FAIL_ON_DEGRADED" in src, "must keep the same escape hatch as tier2_main"


def test_the_stale_message_survives_a_missing_threshold():
    """_check_tier2_heartbeat does not return thresholdMinutes, so the message rendered
    'threshold None min' — a test stub fabricated the key and hid it."""
    h = HealthReport()
    job_mod._check_tier2_health.__wrapped__ if False else None
    import unittest.mock as mock
    with mock.patch.object(job_mod, "_check_tier2_heartbeat",
                           return_value={"stale": True, "ageMinutes": 330}):
        job_mod._check_tier2_health({}, health=h)
    assert "None" not in h.summary
    assert "330 min ago" in h.summary


# ---- P0-2 remainder: a finding that stopped firing must stop counting ------

def _store(rows):
    data = dict(rows)
    return {"query_active": lambda: dict(data),
            "upsert": lambda r: data.__setitem__(r["incidentKey"], r),
            "_data": data}


def _row(key, check_type, active=True):
    return {"incidentKey": key, "checkType": check_type, "status": "active",
            "currentlyActive": active, "severity": "warn", "resource": "x"}


def test_a_sweep_finding_that_stopped_firing_is_marked_not_firing():
    """NOTHING ever wrote currentlyActive=False on a sweep row — this function only wrote True, and
    tier2's stale-marking loop sits behind an ownership filter covering only the capacity family. So
    every sweep ticket ever written stayed active forever and "Findings today: N" was a lifetime
    cumulative total."""
    from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings

    store = _store({"model.bidirectional::Ent/Sales": _row("model.bidirectional::Ent/Sales", "model")})
    out = deliver_new_findings([], alerts_store=store, delivery_sinks={}, collection_complete=True)
    assert out["marked_stale"] == 1
    assert store["_data"]["model.bidirectional::Ent/Sales"]["currentlyActive"] is False
    assert store["_data"]["model.bidirectional::Ent/Sales"]["status"] == "active", \
        "only a human resolves; this just records that it stopped firing"


def test_an_incomplete_collection_marks_nothing():
    """If a collector was down its findings are ABSENT, not fixed — marking them stale would report
    real, unfixed problems as gone. That is the worse failure, so it is the default."""
    from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings

    store = _store({"model.bidirectional::Ent/Sales": _row("model.bidirectional::Ent/Sales", "model")})
    out = deliver_new_findings([], alerts_store=store, delivery_sinks={}, collection_complete=False)
    assert "marked_stale" not in out
    assert store["_data"]["model.bidirectional::Ent/Sales"]["currentlyActive"] is True


def test_the_sweep_never_marks_a_tier2_owned_row_stale():
    """tier2 runs every 5 minutes with its own grace window; an hourly sweep has no idea whether a
    capacity incident is mid-incident."""
    from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings

    store = _store({"capacity::cap-1": _row("capacity::cap-1", "capacity_incident"),
                    "concentration::Ent/DTC": _row("concentration::Ent/DTC", "concentration")})
    out = deliver_new_findings([], alerts_store=store, delivery_sinks={}, collection_complete=True)
    assert out["marked_stale"] == 0
    assert all(r["currentlyActive"] is True for r in store["_data"].values())


def test_the_health_score_qualification_is_actually_WIRED_not_just_available():
    """The parameter existed and NO caller passed it, so the qualification could never appear: a
    degraded collect still scored a bare 100/100 and the narrative still called the estate healthy.
    A fix nothing calls is decorative — this asserts the end-to-end behaviour, not the signature."""
    from fabric_audit_agent.diagnosis import diagnose

    out = diagnose({"capacity": {}, "items": []})       # nothing detectable
    assert out["health"]["overall"] == 100
    assert out["health"]["scoreQualified"] is True, \
        "a blind collection must not report an unqualified 100/100"
    assert out["dataQuality"], "the gaps that justify the qualification must be surfaced too"


def test_a_complete_collection_scores_without_qualification():
    """The guard must not fire on a healthy estate, or the caveat becomes noise and gets ignored."""
    from fabric_audit_agent.diagnosis import diagnose

    facts = {"capacity": {"capacityId": "cap-1", "sku": "F64", "peakCuPct": 42.0,
                          "throttleMinutes": 0.0, "memoryGB": 128, "region": "eastus",
                          "state": "Active", "tenant": "t1"},
             "items": [], "refreshes": []}
    out = diagnose(facts)
    assert out["health"].get("scoreQualified") is None


# ---- round 12: the latch, and the detail it was blanking --------------------

def _faithful():
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from test_alerts_store_delta_fidelity import create_delta_faithful_store
    return create_delta_faithful_store()


_FINDING = [{"key": "refresh.credential::Ent/Sales-Model", "type": "refresh.credential",
             "resource": "Ent / Sales-Model", "score": {"level": "Warning"},
             "what": "Refresh failing on an expired credential.", "why": "y",
             "recommendation": "Re-enter the credential.", "when": ""}]


def test_a_finding_that_stops_and_returns_is_re_activated():
    """currentlyActive=True was written only on FIRST delivery, and the stale-marking writes False,
    so fire -> absent -> fire again LATCHED OFF forever: dropped from the digest count, dropped from
    the app's firing tab, then surfaced as "still open but no longer firing — clear them from the
    notification center". The product telling an admin to dismiss a live, unfixed problem. Findings
    churn hourly here because the LA pull is a top-N-by-cost cut whose cut line moves."""
    from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings

    store = _faithful()
    kw = dict(alerts_store=store, delivery_sinks={}, collection_complete=True)
    key = "refresh.credential::Ent/Sales-Model"

    deliver_new_findings(_FINDING, **kw)
    assert store["_data"][key]["currentlyActive"] is True
    out = deliver_new_findings([], **kw)
    assert out["marked_stale"] == 1 and store["_data"][key]["currentlyActive"] is False
    out = deliver_new_findings(_FINDING, **kw)          # it comes back
    assert out.get("reactivated") == 1
    assert store["_data"][key]["currentlyActive"] is True, "a returning finding must not stay latched off"


def test_stale_and_reactivate_never_blank_the_ticket_detail():
    """`detail` is NOT in context_alerts._FIELDS, so a row read back from the store always has it as
    None — and create_ticket_writer's upsert is a full-row overwrite. Passing row["detail"] blanked
    the ticket description on every stale-marked row (~105 per sweep), emptying the app's hover card
    and degrading "Chat about" to the bare title. tier2's twin reads investigationSummary for
    exactly this reason."""
    from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings

    store, written = _faithful(), []
    kw = dict(alerts_store=store, delivery_sinks={}, collection_complete=True,
              ticket_writer=lambda cid, t: written.append(t))
    deliver_new_findings(_FINDING, **kw)
    deliver_new_findings([], **kw)                      # stale
    deliver_new_findings(_FINDING, **kw)                # re-activate
    assert written, "the ticket table must be kept in sync"
    for t in written:
        assert t["detail"], f"detail must never be blanked (got {t['detail']!r})"

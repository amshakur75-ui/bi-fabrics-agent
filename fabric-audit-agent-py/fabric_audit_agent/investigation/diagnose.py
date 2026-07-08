"""Executable form of the docs/runbooks decision trees: the agent runs the investigation itself --
confirming AND eliminating hypotheses -- instead of hoping the LLM follows a prose runbook. Every
evidence figure comes from injected inputs (grounded by construction)."""
from ..dax import analyze_dax
from ..detectors.refresh import detect_refreshes
from .expensive import top_expensive
from .forecast_throttle import forecast_time_to_threshold
from .throttle import decompose_throttle
from .workload import refresh_collisions


def _step(step, hypothesis, verdict, evidence):
    return {"step": step, "hypothesis": hypothesis, "verdict": verdict, "evidence": evidence}


def _confidence(confirmed_count):
    if confirmed_count >= 2:
        return "high"
    if confirmed_count == 1:
        return "medium"
    return "low"


def diagnose_throttle(series, events, *, refreshes=None, has_real_cost=True):
    chain = []
    decomp = decompose_throttle(series, events, has_real_cost=has_real_cost)
    stage1 = decomp["stage1"]

    step1_verdict = "eliminated" if stage1["timepointsOver"] == 0 else "confirmed"
    chain.append(_step("capacity over-utilized?", "CU% exceeded the throttling threshold",
                        step1_verdict, {"timepointsOver": stage1["timepointsOver"],
                                        "maxCuPct": stage1["maxCuPct"]}))
    if step1_verdict == "eliminated":
        return {"symptom": "throttle", "chain": chain, "rootCause": None,
                "eliminated": ["capacity throttling"], "confidence": "high"}

    confirmed = 1  # step1 confirmed counts as corroborating evidence
    stage2 = decomp["stage2"]
    if stage2.get("available"):
        signal_fired = decomp["conclusion"] == "throttling-confirmed"
        chain.append(_step("throttling signal fired?",
                            "A throttling signal (interactive delay/rejection, background rejection) fired",
                            "confirmed" if signal_fired else "eliminated",
                            {k: v for k, v in stage2.items() if k != "available"}))
        if signal_fired:
            confirmed += 1
    else:
        chain.append(_step("throttling signal fired?",
                            "A throttling signal (interactive delay/rejection, background rejection) fired",
                            "unconfirmed", {"note": stage2.get("note")}))

    stage3 = decomp["stage3"] or {}
    tops = stage3.get("topOperations") or []
    top_offender = tops[0] if tops else None
    if not tops:
        chain.append(_step("who drove the over-window?", "A specific operation/user drove the over-window",
                            "unconfirmed",
                            {"note": "no events in over-window — event source may not cover the workspace"}))
    elif not has_real_cost:
        chain.append(_step("who drove the over-window?", "A specific operation/user drove the over-window",
                            "unconfirmed",
                            {"note": "operation-level data — drivers listed unranked", "topOperations": tops}))
    else:
        chain.append(_step("who drove the over-window?", "A specific operation/user drove the over-window",
                            "confirmed", {"topOperations": tops}))
        confirmed += 1

    over_windows = stage1["overWindows"]
    collisions = []
    if over_windows:
        w0, w1 = over_windows[0]
        collisions = refresh_collisions(events, peak_start=w0, peak_end=w1)
    if collisions:
        chain.append(_step("was it a refresh collision?", "A scheduled refresh landed inside the over-window",
                            "confirmed", {"collisions": collisions}))
        confirmed += 1
    else:
        chain.append(_step("was it a refresh collision?", "A scheduled refresh landed inside the over-window",
                            "eliminated", {"collisions": []}))

    query_text = top_offender.get("queryText") if top_offender else None
    if query_text:
        patterns = analyze_dax(query_text)
        if patterns:
            chain.append(_step("dax anti-pattern", "The top offender's query shows a DAX anti-pattern",
                                "confirmed", {"patterns": patterns}))
            confirmed += 1
        else:
            chain.append(_step("dax anti-pattern", "The top offender's query shows a DAX anti-pattern",
                                "unconfirmed", {"patterns": []}))
    else:
        chain.append(_step("dax anti-pattern", "The top offender's query shows a DAX anti-pattern",
                            "unconfirmed", {"note": "operation-level data — per-query text unavailable"}))

    forecast = forecast_time_to_threshold(series)
    forecast_verdict = "confirmed" if forecast.get("minutesToThreshold") is not None else "unconfirmed"
    chain.append(_step("headroom trajectory", "CU% is trending toward the threshold",
                        forecast_verdict, forecast))

    root_cause = None
    eliminated = []
    if collisions:
        root_cause = f"scheduled refresh collided with the peak CU window ({over_windows[0][0]} - {over_windows[0][1]})"
    elif top_offender and has_real_cost:
        root_cause = f"single offender \"{top_offender.get('item')}\" ({top_offender.get('user')}) drove the over-window"
    elif tops and not has_real_cost:
        root_cause = "surge with no confirmed single offender (cost-blind data — driver unranked)"
    else:
        eliminated.append("identifiable driver")

    return {"symptom": "throttle", "chain": chain, "rootCause": root_cause,
            "eliminated": eliminated, "confidence": _confidence(confirmed)}


def _error_class(code):
    code_lower = (code or "").lower()
    if "credential" in code_lower or "auth" in code_lower:
        return "credentials"
    if "timeout" in code_lower:
        return "timeout"
    return "other"


def diagnose_refresh(refreshes, events, series):
    chain = []
    flags = detect_refreshes({"refreshes": refreshes})
    failing = [f for f in flags if f["type"] == "refresh.failing"]

    if not failing:
        chain.append(_step("failures present?", "At least one refresh failed", "eliminated",
                            {"failingCount": 0}))
        return {"symptom": "refresh", "chain": chain, "rootCause": None,
                "eliminated": ["refresh failure"], "confidence": "high"}

    chain.append(_step("failures present?", "At least one refresh failed", "confirmed",
                        {"failingCount": len(failing), "failures": failing}))
    confirmed = 1

    error_classes = [_error_class(f["evidence"].get("errorCode")) for f in failing]
    top_class = max(set(error_classes), key=error_classes.count)
    chain.append(_step("error-code class", "Failures share a common error-code class", "confirmed",
                        {"class": top_class, "errorCodes": [f["evidence"].get("errorCode") for f in failing]}))
    confirmed += 1

    retry_storms = [f for f in flags if f["type"] == "refresh.retry-storm"]
    if retry_storms:
        chain.append(_step("retry storms?", "Refreshes retried excessively", "confirmed",
                            {"retryStorms": retry_storms}))
        confirmed += 1
    else:
        chain.append(_step("retry storms?", "Refreshes retried excessively", "eliminated",
                            {"retryStorms": []}))

    slow_phases = [f for f in flags if f["type"] == "refresh.slow-phase"]
    if slow_phases:
        chain.append(_step("slow Data phase?", "The Data phase of a refresh ran unusually long",
                            "confirmed", {"slowPhases": slow_phases}))
        confirmed += 1
    else:
        chain.append(_step("slow Data phase?", "The Data phase of a refresh ran unusually long",
                            "eliminated", {"slowPhases": []}))

    peaks = decompose_throttle(series, events)["stage1"]["overWindows"]
    collisions = []
    for w0, w1 in peaks:
        collisions.extend(refresh_collisions(events, peak_start=w0, peak_end=w1))
    if collisions:
        chain.append(_step("collision with interactive peak?", "A failing refresh landed in an interactive peak",
                            "confirmed", {"collisions": collisions}))
        confirmed += 1
    else:
        chain.append(_step("collision with interactive peak?", "A failing refresh landed in an interactive peak",
                            "eliminated", {"collisions": []}))

    if retry_storms:
        root_cause = f"retry storm on \"{retry_storms[0]['resource']}\" ({retry_storms[0]['evidence']['attempts']} attempts, {top_class} errors)"
    else:
        root_cause = f"refresh failures on \"{failing[0]['resource']}\" classified as {top_class}"

    return {"symptom": "refresh", "chain": chain, "rootCause": root_cause,
            "eliminated": [], "confidence": _confidence(confirmed)}


def diagnose_slowness(series, events, *, has_real_cost=True):
    chain = []
    decomp = decompose_throttle(series, events, has_real_cost=has_real_cost)
    stage1 = decomp["stage1"]
    confirmed = 0

    step1_verdict = "eliminated" if stage1["timepointsOver"] == 0 else "confirmed"
    chain.append(_step("throttling?", "CU% exceeded the throttling threshold", step1_verdict,
                        {"timepointsOver": stage1["timepointsOver"], "maxCuPct": stage1["maxCuPct"]}))

    if step1_verdict == "confirmed":
        confirmed += 1
        stage2 = decomp["stage2"]
        if stage2.get("available"):
            signal_fired = decomp["conclusion"] == "throttling-confirmed"
            chain.append(_step("throttling signal fired?", "A throttling signal fired",
                                "confirmed" if signal_fired else "eliminated",
                                {k: v for k, v in stage2.items() if k != "available"}))
            if signal_fired:
                confirmed += 1
                return {"symptom": "slowness", "chain": chain,
                        "rootCause": "capacity throttling (signal fired)",
                        "eliminated": [], "confidence": _confidence(confirmed)}
        else:
            chain.append(_step("throttling signal fired?", "A throttling signal fired", "unconfirmed",
                                {"note": stage2.get("note")}))

    eliminated = ["capacity throttling"] if step1_verdict == "eliminated" else []

    totals = {}
    for e in events:
        item = e.get("item")
        totals[item] = totals.get(item, 0.0) + (e.get("cuSeconds") or 0.0)
    grand_total = sum(totals.values())
    hot_item, hot_share = None, 0.0
    if grand_total > 0:
        hot_item, hot_cu = max(totals.items(), key=lambda kv: kv[1])
        hot_share = hot_cu / grand_total * 100.0

    if hot_item is not None and hot_share > 30.0:
        chain.append(_step("single hot item >30% share?", "One item dominates workload share",
                            "confirmed", {"item": hot_item, "sharePct": hot_share}))
        confirmed += 1
        root_cause = f"single hot item \"{hot_item}\" ({hot_share:.1f}% of workload share)"
        return {"symptom": "slowness", "chain": chain, "rootCause": root_cause,
                "eliminated": eliminated, "confidence": _confidence(confirmed)}
    chain.append(_step("single hot item >30% share?", "One item dominates workload share",
                        "eliminated", {"item": hot_item, "sharePct": hot_share}))
    eliminated.append("single hot item")

    user_totals = {}
    for e in events:
        user = e.get("user")
        user_totals[user] = user_totals.get(user, 0.0) + (e.get("cuSeconds") or 0.0)
    hot_user, hot_user_share = None, 0.0
    if grand_total > 0:
        hot_user, hot_user_cu = max(user_totals.items(), key=lambda kv: kv[1])
        hot_user_share = hot_user_cu / grand_total * 100.0

    if hot_user is not None and hot_user_share > 30.0:
        chain.append(_step("hot user surge?", "One user dominates workload share", "confirmed",
                            {"user": hot_user, "sharePct": hot_user_share}))
        confirmed += 1
        root_cause = f"hot user surge from \"{hot_user}\" ({hot_user_share:.1f}% of workload share)"
        return {"symptom": "slowness", "chain": chain, "rootCause": root_cause,
                "eliminated": eliminated, "confidence": _confidence(confirmed)}
    chain.append(_step("hot user surge?", "One user dominates workload share", "eliminated",
                        {"user": hot_user, "sharePct": hot_user_share}))
    eliminated.append("hot user surge")

    heaviest = top_expensive(events, n=1)
    query_text = heaviest[0].get("queryText") if heaviest else None
    if query_text:
        patterns = analyze_dax(query_text)
        if patterns:
            chain.append(_step("dax anti-pattern", "The heaviest query shows a DAX anti-pattern",
                                "confirmed", {"patterns": patterns}))
            confirmed += 1
            root_cause = f"DAX anti-pattern in the heaviest query ({heaviest[0].get('item')})"
            return {"symptom": "slowness", "chain": chain, "rootCause": root_cause,
                    "eliminated": eliminated, "confidence": _confidence(confirmed)}
        chain.append(_step("dax anti-pattern", "The heaviest query shows a DAX anti-pattern",
                            "unconfirmed", {"patterns": []}))
    else:
        chain.append(_step("dax anti-pattern", "The heaviest query shows a DAX anti-pattern",
                            "unconfirmed", {"note": "operation-level data — per-query text unavailable"}))
    eliminated.append("DAX anti-pattern")

    return {"symptom": "slowness", "chain": chain, "rootCause": None,
            "eliminated": eliminated, "confidence": _confidence(confirmed)}


def run_diagnosis(symptom, *, series, events, refreshes=None, has_real_cost=True):
    if symptom == "throttle":
        return diagnose_throttle(series, events, refreshes=refreshes, has_real_cost=has_real_cost)
    if symptom == "refresh":
        return diagnose_refresh(refreshes, events, series)
    if symptom == "slowness":
        return diagnose_slowness(series, events, has_real_cost=has_real_cost)
    raise ValueError(f"unknown symptom: {symptom!r}")

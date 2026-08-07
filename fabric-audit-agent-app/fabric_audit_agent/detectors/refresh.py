"""Refresh-failure classification detectors, from the raw Get-Refresh-History payload."""
import json

from ..config import DEFAULT_CONFIG
from ..query.windows import _parse_iso_utc


def _where(r):
    return f"{r.get('workspace')} / {r.get('datasetName')}"


def _error_code(r):
    raw = r.get("serviceExceptionJson")
    try:
        return json.loads(raw).get("errorCode") or "unparseable"
    except (TypeError, ValueError, AttributeError):
        return "unparseable"


def _error_text(r):
    """The human-readable failure text (not just the code) for the investigation to surface."""
    raw = r.get("serviceExceptionJson")
    try:
        j = json.loads(raw)
        return (j.get("errorDescription") or j.get("error", {}).get("message")
                or j.get("errorCode") or "").strip() or None
    except (TypeError, ValueError, AttributeError):
        return str(raw)[:300] if raw else None


def _minutes_between(start, end):
    try:
        start_dt = _parse_iso_utc(start, "startTime")
        end_dt = _parse_iso_utc(end, "endTime")
    except ValueError:
        return None
    return (end_dt - start_dt).total_seconds() / 60


_SUBCAUSE_WHAT = {
    "credential": "an expired or invalid credential",
    "gateway": "a gateway that is offline, unreachable, or overloaded",
    "timeout": "the source query timing out",
    "concurrency": "a refresh concurrency/capacity limit being exceeded",
    "constraint": "a data/constraint violation (e.g. duplicate key)",
}


def classify_refresh_failure(record):
    """tightening.md Part 12 Cat 1: map a failed refresh record's error code/text to a sub-cause
    category, so the finding names the actual root cause instead of a generic failure. Matches
    tolerantly (errorCode and/or case-insensitive message substrings — real payloads vary). Returns
    one of "credential"/"gateway"/"timeout"/"concurrency"/"constraint", or None when nothing matches
    (including malformed records — fail-open). Pure."""
    try:
        if not isinstance(record, dict) or record.get("status") != "Failed":
            return None
        code = _error_code(record)
        text = _error_text(record) or ""
        raw = record.get("serviceExceptionJson") or ""
        blob = f"{code} {text} {raw}".lower()
    except Exception:
        return None

    if any(k in blob for k in (
        "credential", "aadsts", "oauth token", "access token has expired", "token has expired",
        "token is expired", "account is disabled", "account has been disabled",
        "expired account", "invalid credentials",
    )):
        return "credential"
    if "gateway" in blob or "dmgatewayerror" in blob:
        return "gateway"
    if "execution timeout expired" in blob or "-2147467259" in blob or "timeout expired" in blob:
        return "timeout"
    if ("exceeded the maximum" in blob or "concurren" in blob
            or ("capacity" in blob and "limit" in blob)):
        return "concurrency"
    if "duplicate key" in blob or "constraint" in blob or "cannot insert" in blob:
        return "constraint"
    return None


def refresh_failure_pattern(refreshes):
    """B3: group failures by (dataset, errorCode) to distinguish a CHRONIC pattern (the same error
    repeating) from a one-off transient blip. Returns a list of
    ``{resource, errorCode, count, sampleText, firstSeen, lastSeen}`` sorted by count desc. Pure."""
    groups = {}
    for r in refreshes or []:
        if r.get("status") != "Failed":
            continue
        key = (_where(r), _error_code(r))
        g = groups.setdefault(key, {"resource": _where(r), "errorCode": _error_code(r),
                                    "count": 0, "sampleText": None, "firstSeen": None,
                                    "lastSeen": None})
        g["count"] += 1
        g["sampleText"] = g["sampleText"] or _error_text(r)
        when = r.get("startTime")
        if when:
            g["firstSeen"] = min(g["firstSeen"], when) if g["firstSeen"] else when
            g["lastSeen"] = max(g["lastSeen"], when) if g["lastSeen"] else when
    return sorted(groups.values(), key=lambda g: g["count"], reverse=True)


def detect_refreshes(facts, config=None):
    config = config or DEFAULT_CONFIG
    refreshes = (facts or {}).get("refreshes") or []
    thr = config["refresh"]
    flags = []
    for r in refreshes:
        where = _where(r)
        when = r.get("startTime") or ""
        attempts = r.get("refreshAttempts") or []

        if r.get("status") == "Failed":
            error_code = _error_code(r)
            error_text = _error_text(r)
            _detail = f": {error_text}" if error_text and error_text != error_code else ""
            flags.append({
                "type": "refresh.failing", "resource": where, "when": when,
                "evidence": {"errorCode": error_code, "errorText": error_text,
                             "refreshType": r.get("refreshType"), "attempts": len(attempts)},
                "what": f"Refresh of \"{r.get('datasetName')}\" failed with {error_code}{_detail}.",
            })

            # tightening.md Part 12 Cat 1: per-failure sub-cause, distinct from the aggregate
            # refresh.chronic pattern above — both can coexist (a chronic timeout is still a timeout).
            cause = classify_refresh_failure(r)
            if cause:
                sample = (error_text or "")[:200]
                flags.append({
                    "type": f"refresh.{cause}", "resource": where, "when": when,
                    "evidence": {"item": r.get("datasetName"), "workspace": r.get("workspace"),
                                 "errorCode": error_code, "cause": cause, "sampleMessage": sample},
                    "what": (f"Refresh of \"{r.get('datasetName')}\" failed due to "
                             f"{_SUBCAUSE_WHAT.get(cause, cause)}"
                             + (f": {sample}" if sample else "") + "."),
                })

        if len(attempts) >= thr["retryStormAttempts"]:
            flags.append({
                "type": "refresh.retry-storm", "resource": where, "when": when,
                "evidence": {"attempts": len(attempts)},
                "what": f"Refresh of \"{r.get('datasetName')}\" retried {len(attempts)} times.",
            })

        for attempt in attempts:
            if attempt.get("type") != "Data":
                continue
            minutes = _minutes_between(attempt.get("startTime"), attempt.get("endTime"))
            if minutes is None or minutes <= thr["slowDataPhaseMin"]:
                continue
            flags.append({
                "type": "refresh.slow-phase", "resource": where, "when": when,
                "evidence": {"phase": "Data", "minutes": minutes},
                "what": f"Data phase of \"{r.get('datasetName')}\" refresh took {minutes:.1f} minutes.",
            })

    # B3: chronic vs transient — the same error repeating across >= chronicFailureCount refreshes is
    # a standing problem worth flagging differently from a single blip.
    for g in refresh_failure_pattern(refreshes):
        if g["count"] >= thr.get("chronicFailureCount", 3):
            flags.append({
                "type": "refresh.chronic", "resource": g["resource"], "when": g["lastSeen"] or "",
                "evidence": {"errorCode": g["errorCode"], "count": g["count"],
                             "errorText": g["sampleText"], "firstSeen": g["firstSeen"],
                             "lastSeen": g["lastSeen"]},
                "what": (f"\"{g['resource']}\" has failed refresh {g['count']} times with the same "
                         f"error ({g['errorCode']}) — a chronic pattern, not a one-off"
                         + (f": {g['sampleText']}" if g.get("sampleText") else "") + "."),
            })

    return flags

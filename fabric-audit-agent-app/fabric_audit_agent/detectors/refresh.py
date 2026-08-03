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


def _minutes_between(start, end):
    try:
        start_dt = _parse_iso_utc(start, "startTime")
        end_dt = _parse_iso_utc(end, "endTime")
    except ValueError:
        return None
    return (end_dt - start_dt).total_seconds() / 60


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
            flags.append({
                "type": "refresh.failing", "resource": where, "when": when,
                "evidence": {"errorCode": error_code, "refreshType": r.get("refreshType"), "attempts": len(attempts)},
                "what": f"Refresh of \"{r.get('datasetName')}\" failed with {error_code}.",
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

    return flags

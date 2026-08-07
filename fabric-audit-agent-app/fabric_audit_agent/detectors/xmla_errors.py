"""XMLA / connection-level error detector. tightening.md Part 12 Category 3 (Sub-plan 2 of the
alerting redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``,
Task 2c).

Reads ``facts["events"]`` (``normalize_event``-shaped dicts, same source as
``detectors/query_antipatterns.py`` / ``detectors/absolute_cost.py``). ``EventText`` (Log
Analytics ``PowerBIDatasetsWorkspace``) is carried forward onto every event as ``queryText`` by
``investigation/events.py``'s ``normalize_event`` -- tightening.md Cat 3 says the XMLA/connection
error shapes ("XML for Analysis request timed out", "Bad Request" on a TMSL/XMLA command,
XMLA-endpoint auth failures) are "greppable in EventText", so that's the field this detector
scans. Classification (including the mandatory "session moved to another node" suppression) is
the pure ``investigation.xmla_classify.classify_xmla_error`` -- this module is just the
facts-in/flags-out wiring + evidence shaping.

One finding per matching event (no cross-event dedupe, unlike the Category-2 shape-recurrence
detector): each XMLA/connection failure is a standalone actionable event, not a design pattern
that benefits from being collapsed. Fail-open: an event with no/blank ``queryText``, or non-string
text, is skipped, never crashes the run (``classify_xmla_error`` also never raises).
"""
from ..config import DEFAULT_CONFIG
from ..investigation.xmla_classify import classify_xmla_error

_WHAT = {
    "auth": "an authentication/token failure against the XMLA endpoint",
    "bad-request": "a Bad Request on a TMSL/XMLA command",
    "timeout": "an XML for Analysis request timing out",
    "connection-drop": "the XMLA connection being dropped/reset",
}

_MAX_ERROR_TEXT = 300


def detect_xmla_errors(facts, config=None):
    config = config or DEFAULT_CONFIG
    facts = facts or {}
    events = facts.get("events") or []

    flags = []
    for ev in events:
        text = ev.get("queryText")
        cause = classify_xmla_error(text)
        if not cause:
            continue

        item = ev.get("item") or "unknown item"
        user = ev.get("user") or "unknown user"
        error_text = text.strip()[:_MAX_ERROR_TEXT]

        flags.append({
            "type": f"xmla.{cause}", "resource": item, "when": ev.get("ts") or "",
            "evidence": {"item": item, "user": user, "errorText": error_text, "cause": cause},
            "what": (f"\"{item}\" hit {_WHAT.get(cause, cause)}: {error_text}"),
        })

    return flags

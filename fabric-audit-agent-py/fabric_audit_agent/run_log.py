"""The agent's own audit trail for a run. Port of ``core/run-log.js``. Pure (time injected)."""

_DOMAINS = ["capacity", "models", "reports", "pipelines", "lineage", "access", "usage"]


def build_run_log(facts=None, envelope=None, at=""):
    facts = facts or {}
    envelope = envelope or {}
    d = envelope.get("data") or {}
    return {
        "at": at,
        "collectedDomains": [dom for dom in _DOMAINS if facts.get(dom) is not None],
        "findingCount": len(d.get("findings") or []),
        "suppressedCount": len(d.get("suppressed") or []),
        "readOnly": True,
        "note": "Agent is read-only; only outward action is delivering findings.",
    }

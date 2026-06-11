"""Deterministic reasoner (no LLM). Faithful port of the Node ``adapters/reasoner.stub.js``.

Maps each detector flag to a 7-field finding by combining the KB remediation, a
per-type impact line, and the severity score. The real Claude reasoner (later increment)
implements the same ``reason(facts, flags)`` interface and enriches the prose.
"""
from .finding import create_finding
from .severity import score_severity
from .kb import get_remediation
from .config import DEFAULT_CONFIG


def _impact_for(flag):
    t = flag.get("type")
    e = flag.get("evidence") or {}
    if t == "capacity.throttle":
        return "Reports and datasets on this capacity slow down or queue during the peak window."
    if t == "capacity.contention":
        return f"Downstream reports for {', '.join(e.get('datasets') or [])} show stale data until refreshes drain."
    if t == "capacity.oversized-model":
        return "Long refreshes consume capacity memory and CU, compounding contention."
    if t == "capacity.concentration":
        return "One item monopolizing CU can slow or throttle every other workload on the same capacity."
    if t in ("model.bidirectional", "model.auto-datetime"):
        return "Slower queries and a larger model that consumes more capacity memory."
    if t == "model.refresh-failing":
        return "Reports on this model show stale data when refreshes fail."
    if t in ("report.too-many-visuals", "report.slow-visual"):
        return "Slow page loads for every user who opens this report."
    if t == "report.directquery":
        return "Every interaction round-trips to the source, adding load and latency."
    if t in ("pipeline.failing", "pipeline.gateway"):
        return "Downstream datasets and reports go stale until the pipeline recovers."
    if t == "lineage.blast-radius":
        return "Every downstream dataset and report shows stale or missing data until the root item is fixed."
    if t in ("security.admin-grant", "security.external-share", "security.unusual-access"):
        return "Potential data exposure or compliance risk until reviewed."
    if t == "cost.unused-report":
        return "Wasted storage/refresh load; safe to clean up."
    if t == "cost.idle-capacity":
        return "Ongoing spend with little utilization."
    if t == "meta.detector-error":
        return "This check could not run; its findings are missing from this audit."
    return "Impact not assessed."


def create_stub_reasoner(config=None):
    """Return a reasoner with a ``reason(facts, flags) -> findings`` callable."""
    config = config or DEFAULT_CONFIG

    def reason(facts, flags):
        out = []
        for flag in flags:
            kb = get_remediation(flag.get("type"))
            finding = create_finding({
                "what": flag.get("what"),
                "where": flag.get("resource"),
                "when": flag.get("when"),
                "why": kb["rootCause"],
                "impact": _impact_for(flag),
                "fix": kb["fixes"],
                "score": score_severity(flag, config),
            })
            finding["key"] = f"{flag.get('type')}::{flag.get('resource')}"
            out.append(finding)
        return out

    return {"reason": reason}

"""Semantic-model detectors. Faithful port of the Node ``core/detectors/model.js``."""
from ..config import DEFAULT_CONFIG


def detect_models(facts, config=None):
    config = config or DEFAULT_CONFIG
    models = (facts or {}).get("models") or []
    mdl = config["model"]
    flags = []
    for m in models:
        where = f"{m.get('workspace')} / {m.get('name')}"
        when = m.get("observedAt") or ""
        if (m.get("bidirectionalRels") or 0) >= mdl["bidirectionalMin"]:
            flags.append({
                "type": "model.bidirectional", "resource": where, "when": when,
                "evidence": {"count": m.get("bidirectionalRels")},
                "what": f"Model \"{m.get('name')}\" has {m.get('bidirectionalRels')} bidirectional relationships.",
            })
        if m.get("autoDateTime") is True:
            flags.append({
                "type": "model.auto-datetime", "resource": where, "when": when, "evidence": {},
                "what": f"Model \"{m.get('name')}\" has Auto Date/Time enabled.",
            })
        if (m.get("refreshFailRatePct") or 0) >= mdl["refreshFailPct"]:
            flags.append({
                "type": "model.refresh-failing", "resource": where, "when": when,
                "evidence": {"failRatePct": m.get("refreshFailRatePct")},
                "what": f"Model \"{m.get('name')}\" refresh fail rate is {m.get('refreshFailRatePct')}%.",
            })
    return flags

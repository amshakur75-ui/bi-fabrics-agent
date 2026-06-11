"""Report-performance detectors. Faithful port of the Node ``core/detectors/report.js``."""
from ..config import DEFAULT_CONFIG


def detect_reports(facts, config=None):
    config = config or DEFAULT_CONFIG
    reports = (facts or {}).get("reports") or []
    rep = config["report"]
    flags = []
    for r in reports:
        where = f"{r.get('workspace')} / {r.get('name')}"
        if (r.get("visuals") or 0) >= rep["visualsMin"]:
            flags.append({
                "type": "report.too-many-visuals", "resource": where, "when": "",
                "evidence": {"visuals": r.get("visuals")},
                "what": f"Report \"{r.get('name')}\" has {r.get('visuals')} visuals on its busiest page.",
            })
        if r.get("mode") == "DirectQuery":
            src = r.get("source")   # nullish (?? ), not falsy: an empty-string source is kept
            flags.append({
                "type": "report.directquery", "resource": where, "when": "",
                "evidence": {"source": src if src is not None else "unknown"},
                "what": f"Report \"{r.get('name')}\" uses DirectQuery against {src if src is not None else 'an unknown source'}.",
            })
        if (r.get("slowestVisualMs") or 0) >= rep["slowVisualMs"]:
            flags.append({
                "type": "report.slow-visual", "resource": where, "when": "",
                "evidence": {"ms": r.get("slowestVisualMs")},
                "what": f"Report \"{r.get('name')}\" has a visual rendering in {r.get('slowestVisualMs')} ms.",
            })
    return flags

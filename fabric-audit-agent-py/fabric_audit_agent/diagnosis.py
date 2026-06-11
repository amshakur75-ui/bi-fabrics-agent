"""Full offline diagnosis. Faithful port of the Node ``core/diagnosis.js``.

detectors -> stub reasoner -> health score, capacity verdict, prioritized roadmap.
No network, no API key.
"""
import json
from .detectors import detect_all
from .reasoner_stub import create_stub_reasoner
from .health_score import build_health_score
from .roadmap import build_roadmap
from .verdict import build_capacity_verdict


def diagnose(facts):
    flags = detect_all(facts)
    findings = create_stub_reasoner()["reason"](facts, flags)
    return {
        "flags": flags,
        "findings": findings,
        "health": build_health_score(findings),
        "verdict": build_capacity_verdict(facts, flags),
        "roadmap": build_roadmap(findings),
    }


def format_diagnosis(result):
    findings, health = result["findings"], result["health"]
    verdict, roadmap = result["verdict"], result["roadmap"]
    lines = ["", "================  YOUR ESTATE — DIAGNOSIS  ================", ""]
    if not findings:
        lines.append("No issues detected from the data provided.")
        lines.append("(If you expected findings, re-check the values / column mapping above.)")
    else:
        for f in findings:
            fix0 = (f.get("fix") or [None])[0]
            lines.append(f"[{f['score']['level']}] {f['what']}")
            lines.append(f"    Why:    {f['why']}")
            lines.append(f"    Impact: {f['impact']}")
            lines.append(f"    Fix:    {fix0 if fix0 is not None else ''}")
            lines.append("")
        lines.append("-----------------------------------------------------------")
        lines.append(f"Findings: {len(findings)}   Health: {health['overall']}/100")
        lines.append(f"By domain: {json.dumps(health['byDomain'])}")
        lines.append(f"Capacity verdict: {verdict['decision'].upper()} — {verdict['reason']}")
        lines.append("")
        lines.append("Do these first:")
        for r in roadmap[:5]:
            lines.append(f"   #{r['rank']} [{r['level']}] {r['what']}")
    lines += ["", "===========================================================", ""]
    return "\n".join(lines)

"""Audience-tailored views from an audit envelope. Port of ``core/audience.js``. Pure."""


def view_for(envelope=None, audience="team"):
    envelope = envelope or {}
    d = envelope.get("data") or {}
    findings = d.get("findings") or []

    if audience == "exec":
        return {
            "audience": "exec",
            "health": (d.get("healthScore") or {}).get("overall"),
            "verdict": (d.get("verdict") or {}).get("decision"),
            "critical": len([f for f in findings if (f.get("score") or {}).get("level") == "Critical"]),
            "warning": len([f for f in findings if (f.get("score") or {}).get("level") == "Warning"]),
            "topFindings": [{"what": r.get("what"), "level": r.get("level")} for r in (d.get("roadmap") or [])[:3]],
            "accountability": (d.get("accountability") or {}).get("ignoredCount") or 0,
        }
    if audience == "author":
        return {
            "audience": "author",
            "items": [{"what": f.get("what"), "tip": f.get("userTip")} for f in findings if f.get("userTip")],
        }
    # team (default) — full working view
    return {
        "audience": "team",
        "findings": findings,
        "roadmap": d.get("roadmap") or [],
        "routing": d.get("routing") or {},
        "sla": d.get("sla"),
        "correlations": d.get("correlations") or [],
    }

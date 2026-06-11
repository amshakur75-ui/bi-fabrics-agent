"""Security / access detectors. Faithful port of the Node ``core/detectors/security.js``."""
import math
import re
from ..config import DEFAULT_CONFIG


def detect_security(facts, config=None):
    config = config or DEFAULT_CONFIG
    a = (facts or {}).get("access") or {}
    sec = config["security"]
    flags = []

    for g in (a.get("adminGrants") or []):
        if re.search("admin", str(g.get("role") or ""), re.I) and g.get("sensitive"):
            flags.append({
                "type": "security.admin-grant",
                "resource": f"{g.get('workspace')}",
                "when": g.get("grantedAt") or "",
                "evidence": {"principal": g.get("principal"), "role": g.get("role"), "sensitive": True},
                "what": f"Admin role granted to {g.get('principal')} on sensitive workspace \"{g.get('workspace')}\".",
            })

    for s in (a.get("externalShares") or []):
        flags.append({
            "type": "security.external-share",
            "resource": f"{s.get('workspace')} / {s.get('item')}",
            "when": s.get("at") or "",
            "evidence": {"sharedWith": s.get("sharedWith")},
            "what": f"\"{s.get('item')}\" shared externally with {s.get('sharedWith')}.",
        })

    for e in (a.get("accessEvents") or []):
        base = e.get("baselineCount") or 0
        count = e.get("count") or 0
        if base > 0:
            ratio = count / base
        else:
            ratio = math.inf if count > 0 else 0
        if ratio >= sec["unusualRatio"]:
            flags.append({
                "type": "security.unusual-access",
                "resource": f"{e.get('workspace')}",
                "when": "",
                "evidence": {
                    "user": e.get("user"), "count": e.get("count"), "baselineCount": base,
                    "ratio": int(math.floor(ratio + 0.5)) if math.isfinite(ratio) else 999,
                },
                "what": f"{e.get('user')} accessed \"{e.get('workspace')}\" {e.get('count')} times vs a baseline of {base}.",
            })
    return flags

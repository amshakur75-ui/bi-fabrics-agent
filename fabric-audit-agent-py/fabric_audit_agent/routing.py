"""Route finding keys to owners by domain. Port of ``core/routing.js``. Pure."""
from .key_utils import domain_of

DEFAULT_ROUTES = {
    "security": "security-team", "cost": "finops", "capacity": "powerbi-team",
    "model": "powerbi-team", "report": "powerbi-team", "pipeline": "powerbi-team",
    "lineage": "powerbi-team", "meta": "powerbi-team",
}


def route_findings(findings=None, routes=None):
    findings = findings or []
    routes = routes if routes is not None else DEFAULT_ROUTES
    routed = {}
    for f in findings:
        dest = routes.get(domain_of(f.get("key")), "unrouted")
        routed.setdefault(dest, []).append(f.get("key"))
    return routed

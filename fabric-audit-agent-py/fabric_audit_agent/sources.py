"""Source registry + capability coverage resolver (spec: source-capability layer).

Single source of truth for WHAT telemetry sources exist, HOW to detect them from env, and
WHAT capabilities each provides. ``resolve_sources(env)`` computes the coverage report the
tools thread into their envelopes; collector COMPOSITION stays in ``job.build_collector_from_env``
(already built, authority-first) — this module never opens a connection. Pure; env injected.
"""

CAPABILITIES = ("capacityCU", "userAttribution", "perItemCU", "eventDepth", "owner")

# authority > liveness when picking best source per capability.
_AUTHORITY_RANK = {"authoritative": 2, "proxy": 1}
_LIVENESS_RANK = {"live": 3, "near-live": 2, "daily": 1, "offline": 0}


def _gate(*names):
    """Configured when EVERY named env var is a non-empty string ('0' counts — nullish, not falsy-int)."""
    def check(env):
        return all(bool(str(env.get(n) or "")) for n in names)
    return check


SOURCES = {
    "csv": {
        "descriptor": {"provides": ("capacityCU", "perItemCU"), "liveness": "offline",
                        "authority": "authoritative", "scope": "tenant"},
        "configured": _gate("FABRIC_CSV_PATHS"),
    },
    "capacity_events": {
        "descriptor": {"provides": ("capacityCU",), "liveness": "live",
                        "authority": "authoritative", "scope": "tenant"},
        "configured": _gate("FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_CAPACITY_EVENTS_DB"),
    },
    "activity": {
        "descriptor": {"provides": ("userAttribution", "owner"), "liveness": "near-live",
                        "authority": "authoritative", "scope": "tenant"},
        "configured": _gate("FABRIC_CLIENT_ID", "FABRIC_TENANT_ID", "FABRIC_CLIENT_SECRET"),
    },
    "fuam": {  # future (Phase 3 B3): descriptor present so coverage names the gap; never configured yet.
        "descriptor": {"provides": ("perItemCU", "owner"), "liveness": "daily",
                        "authority": "authoritative", "scope": "tenant"},
        "configured": _gate("FABRIC_FUAM_SQL_HTTP_PATH"),
    },
    "events_la": {
        "descriptor": {"provides": ("eventDepth", "userAttribution", "perItemCU"), "liveness": "live",
                        "authority": "proxy", "scope": "per-workspace"},
        "configured": _gate("FABRIC_LA_WORKSPACE_ID", "FABRIC_CLIENT_ID"),
    },
    "workspace_monitoring": {
        "descriptor": {"provides": ("eventDepth", "userAttribution", "perItemCU"), "liveness": "live",
                        "authority": "proxy", "scope": "per-workspace"},
        "configured": _gate("FABRIC_KUSTO_CLUSTER", "FABRIC_KUSTO_DB", "FABRIC_CLIENT_ID"),
    },
}

_DEGRADED_NOTES = {
    "eventDepth": "per-query cost unavailable — enable Log Analytics or Workspace Monitoring for per-query depth",
    "perItemCU": "per-item CU is a proxy or estimate (no FUAM)",
}


def resolve_sources(env):
    """Return {"coverage": {...}} — best configured source per capability (authority, then liveness)."""
    configured = {sid: s["descriptor"] for sid, s in SOURCES.items() if s["configured"](env)}
    by_capability = {}
    for cap in CAPABILITIES:
        best_id, best_d = None, None
        for sid, d in configured.items():
            if cap not in d["provides"]:
                continue
            if best_d is None or (
                _AUTHORITY_RANK[d["authority"]], _LIVENESS_RANK[d["liveness"]]
            ) > (_AUTHORITY_RANK[best_d["authority"]], _LIVENESS_RANK[best_d["liveness"]]):
                best_id, best_d = sid, d
        by_capability[cap] = (
            {"source": best_id, "liveness": best_d["liveness"], "authority": best_d["authority"]}
            if best_id is not None else None
        )
    blind = [cap for cap in CAPABILITIES if by_capability[cap] is None]
    # Two explicit, named degradation checks (not a generic loop — each names its own condition):
    degraded = []
    # 1. eventDepth absent OR proxy-only → per-query cost is unavailable/proxy.
    depth = by_capability["eventDepth"]
    if depth is None or depth["authority"] == "proxy":
        degraded.append(_DEGRADED_NOTES["eventDepth"])
    # 2. perItemCU served by csv (offline export) or missing while other sources exist → estimate.
    per_item = by_capability["perItemCU"]
    if per_item is not None and per_item["source"] == "csv":
        degraded.append(_DEGRADED_NOTES["perItemCU"])
    return {"coverage": {"byCapability": by_capability, "blind": blind, "degraded": degraded}}

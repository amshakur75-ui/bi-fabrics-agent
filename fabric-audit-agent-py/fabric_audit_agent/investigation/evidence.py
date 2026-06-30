"""Evidence envelope + coverage + confidence helpers (pure).

Coverage honesty + abstention are must-fixes: every investigation states which workspaces/sources
it actually saw, and confidence is derived from evidence density + source corroboration (not the
model's say-so)."""


def build_coverage(facts):
    facts = facts or {}
    items = facts.get("items") or []
    workspaces = sorted({(it.get("workspace") or "") for it in items if it.get("workspace")})
    sources = []
    if facts.get("capacity"):
        sources.append("capacity")
    if facts.get("users"):
        # Per-user attribution is only meaningful when user rows are present.
        sources.append("attribution")
    elif items:
        # Items exist but no user breakdown -> inventory signal, not user attribution.
        sources.append("inventory")
    failed = list(facts.get("sourcesFailed") or [])
    # "mock" only when nothing real was collected; live sources always populate users/items/capacity.
    mode = "live" if (workspaces or facts.get("users") or facts.get("capacity")) else "mock"
    blind = ["live estate (no source configured)"] if mode == "mock" else []
    return {"workspacesSeen": workspaces, "sources": sources, "sourcesFailed": failed,
            "mode": mode, "blind": blind}


def assess_confidence(*, found, corroborating_sources):
    if not found:
        return {"level": "insufficient", "basis": "requested entity not present in collected data"}
    if corroborating_sources >= 2:
        return {"level": "high", "basis": f"{corroborating_sources} sources corroborate"}
    if corroborating_sources == 1:
        return {"level": "medium", "basis": "single source (CPU-time proxy, not authoritative CU)"}
    return {"level": "low", "basis": "weak/indirect evidence"}


def evidence_item(kind, summary, data):
    return {"kind": kind, "summary": summary, "data": data}

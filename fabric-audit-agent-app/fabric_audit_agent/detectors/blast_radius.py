"""Blast-radius (lineage) detector. Faithful port of the Node ``core/detectors/blast-radius.js``.

For each root-cause failure (a Failed node with no Failed upstream), emit one finding
listing every downstream asset reachable from it. Cycle-safe; excludes the root.
"""
from collections import deque


def _build_adjacency(edges):
    downstream, upstream = {}, {}
    for e in (edges or []):
        downstream.setdefault(e.get("from"), []).append(e.get("to"))
        upstream.setdefault(e.get("to"), []).append(e.get("from"))
    return downstream, upstream


def _reach_downstream(start_id, downstream):
    seen, seen_set = [], set()
    queue = deque(downstream.get(start_id, []))
    while queue:
        node_id = queue.popleft()
        if node_id in seen_set or node_id == start_id:   # exclude root + cycle-safe
            continue
        seen_set.add(node_id)
        seen.append(node_id)
        for nxt in downstream.get(node_id, []):
            if nxt not in seen_set:
                queue.append(nxt)
    return seen


def detect_blast_radius(facts, _config=None):
    lineage = (facts or {}).get("lineage")
    if not lineage or not (lineage.get("nodes") or []):
        return []
    nodes = lineage["nodes"]
    node_by_id = {n.get("id"): n for n in nodes}
    downstream, upstream = _build_adjacency(lineage.get("edges") or [])

    def is_failed(nid):
        n = node_by_id.get(nid)
        return bool(n) and n.get("status") == "Failed"

    def name_or_id(i):
        n = node_by_id.get(i)
        nm = n.get("name") if n else None
        return nm if nm is not None else i

    root_causes = [
        n for n in nodes
        if n.get("status") == "Failed" and not any(is_failed(u) for u in upstream.get(n.get("id"), []))
    ]

    flags = []
    for rc in root_causes:
        affected = [name_or_id(i) for i in _reach_downstream(rc.get("id"), downstream)]
        suffix = ": " + ", ".join(affected) if affected else ""
        flags.append({
            "type": "lineage.blast-radius",
            "resource": f"{rc.get('workspace')} / {rc.get('name')}",
            "when": rc.get("failedAt") or "",
            "evidence": {"root": rc.get("name"), "rootType": rc.get("type"), "affected": affected, "affectedCount": len(affected)},
            "what": f"{rc.get('type')} \"{rc.get('name')}\" failed, impacting {len(affected)} downstream asset(s){suffix}.",
        })
    return flags

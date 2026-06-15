"""Compose multiple collectors into one estate.

Lets the live sources combine: **CSV** (authoritative CU% + per-item CU share) + **List Usages**
(capacity sku/state/quota) + **Workspace Monitoring** (per-user attribution). Item-level user
attribution is joined onto the CU-share items by ``(workspace, name)``.

Precedence is **first-non-empty-wins**, so order collectors authoritative-first: e.g.
``[csv, list_usages, workspace_monitoring]`` keeps the CSV's real CU ``sharePct`` while picking
up sku/quota from List Usages and ``topUsers`` from Workspace Monitoring.
"""

_EMPTY = (None, "", [])


def _merge_into(dst, src):
    for k, v in (src or {}).items():
        if v in _EMPTY:
            continue
        dst.setdefault(k, v)   # first collector with a real value for this field wins


def _item_key(it):
    return ((it.get("workspace") or "").lower(), (it.get("name") or "").lower())


def merge_facts_list(facts_list):
    capacity, items = {}, {}
    extra = {"models": [], "reports": [], "pipelines": []}
    for f in facts_list or []:
        _merge_into(capacity, f.get("capacity") or {})
        for it in f.get("items") or []:
            _merge_into(items.setdefault(_item_key(it), {}), it)
        for key in extra:
            extra[key].extend(f.get(key) or [])

    merged = {}
    if capacity:
        merged["capacity"] = capacity
    if items:
        merged["items"] = list(items.values())
    for key, rows in extra.items():
        if rows:
            merged[key] = rows
    return merged


def create_merged_collector(collectors):
    cols = list(collectors)

    def collect():
        return merge_facts_list([c["collect"]() for c in cols])

    return {"collect": collect}

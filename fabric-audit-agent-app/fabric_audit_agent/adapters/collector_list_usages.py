"""Capacity-context CollectorPort — Fabric Core "List Capacities" + Azure "List Usages".

Supported REST (no Capacity Metrics model needed). Provides capacity **metadata** (sku, state,
region) and **CU quota** consumption (provisioned vs limit) — NOT the utilization/throttle
timeline, which still comes from Capacity Metrics (CSV / semantic model). The HTTP client is
injected, so this is unit-testable offline and swaps to a real authed client at deploy:

  http        (Power BI scope) -> capacitiesUrl  (Fabric Core /v1/capacities, api.fabric.microsoft.com)
  usages_http (ARM scope)      -> usagesUrl       (Azure ARM List Usages, management.azure.com); falls back to http

The two endpoints need DIFFERENT token audiences — passing one ARM-scoped client for both 401s the
Fabric capacities call. ``build_collector_from_env`` wires a Power BI client for capacities and an
ARM client for usages.
"""


def _match_capacity(rows, want):
    want = (want or "").lower()
    for c in rows:
        if not want:
            return c
        if want in (str(c.get("id") or "").lower(),
                    str(c.get("displayName") or "").lower(),
                    str(c.get("name") or "").lower()):
            return c
    return rows[0] if rows else None


def create_list_usages_collector(http, config, usages_http=None):
    cfg = config or {}
    usages_client = usages_http or http   # ARM-scoped client for the ARM usages endpoint; defaults to http

    def collect():
        cap = {}
        if cfg.get("capacitiesUrl"):
            page = http.get_json(cfg["capacitiesUrl"])
            rows = (page.get("value") if isinstance(page, dict) else page) or []
            chosen = _match_capacity(rows, cfg.get("capacity"))
            if chosen:
                cap["capacityId"] = chosen.get("displayName") or chosen.get("name") or chosen.get("id")
                for src, dst in (("sku", "sku"), ("state", "state"), ("region", "region")):
                    if chosen.get(src) is not None:
                        cap[dst] = chosen[src]
        if cfg.get("usagesUrl"):
            page = usages_client.get_json(cfg["usagesUrl"])
            for u in ((page.get("value") if isinstance(page, dict) else page) or []):
                nm = u.get("name")
                unit = nm.get("value") if isinstance(nm, dict) else nm
                if str(unit).upper() in ("CU", "CAPACITYQUOTA", "CAPACITY"):
                    cap["cuQuotaUsed"] = u.get("currentValue")
                    cap["cuQuotaLimit"] = u.get("limit")
                    break
        return {"capacity": cap} if cap else {}

    return {"collect": collect}

"""REST CollectorPort. Port of ``adapters/collector.rest.js``.

The HTTP client is injected (``http.get_json(url) -> dict``) so this is testable offline and
swappable at deploy (a real client adds Entra auth + base URL + paging — see
``adapters.clients.EntraHttp``). Synchronous: Databricks jobs use blocking I/O.

Endpoints are representative; verify exact paths against the live Fabric/Power BI Admin API
at deploy. If a domain URL isn't configured, the domain is passed as ``[]``/``{}`` so
``to_facts`` tolerates it.
"""
from ..mappers import to_facts


def fetch_all_pages(http, url):
    """Follow ``nextLink`` pages, accumulating ``.value``."""
    all_rows = []
    next_url = url
    guard = 0
    while next_url and guard < 1000:
        guard += 1
        page = http.get_json(next_url)
        value = page.get("value") if isinstance(page, dict) else None
        if isinstance(value, list):
            all_rows.extend(value)
        elif page is not None:
            all_rows.append(page)
        next_url = page.get("nextLink") if isinstance(page, dict) else None
    if guard >= 1000:
        print(f"[fetch_all_pages] page guard reached — results may be truncated {url}")
    return all_rows


def create_rest_collector(http, config):
    def collect():
        config_ = config or {}

        # Capacity domain (single object + paged refreshes); each domain optional.
        capacity_raw = http.get_json(config_["capacityUrl"]) if config_.get("capacityUrl") else None
        refreshes_raw = fetch_all_pages(http, config_["refreshesUrl"]) if config_.get("refreshesUrl") else []

        datasets_raw = fetch_all_pages(http, config_["datasetsUrl"]) if config_.get("datasetsUrl") else []
        reports_raw = fetch_all_pages(http, config_["reportsUrl"]) if config_.get("reportsUrl") else []
        pipelines_raw = fetch_all_pages(http, config_["pipelinesUrl"]) if config_.get("pipelinesUrl") else []
        lineage_raw = http.get_json(config_["lineageUrl"]) if config_.get("lineageUrl") else {}
        access_raw = http.get_json(config_["accessUrl"]) if config_.get("accessUrl") else {}
        usage_raw = http.get_json(config_["usageUrl"]) if config_.get("usageUrl") else {}

        # capacity: capacityRaw?.value?.[0] ?? capacityRaw ?? {}
        cap = None
        if isinstance(capacity_raw, dict):
            v = capacity_raw.get("value")
            if isinstance(v, list) and v:
                cap = v[0]
        if cap is None:
            cap = capacity_raw if capacity_raw is not None else {}

        if isinstance(refreshes_raw, list):
            refreshes = refreshes_raw
        elif isinstance(refreshes_raw, dict):
            refreshes = refreshes_raw.get("value") or []
        else:
            refreshes = []

        raw = {
            "capacity": cap,
            "refreshes": refreshes,
            "datasets": datasets_raw,
            "reports": reports_raw,
            "pipelines": pipelines_raw,
            "lineage": lineage_raw,
            "access": access_raw,
            "usage": usage_raw,
        }
        return to_facts(raw)

    return {"collect": collect}

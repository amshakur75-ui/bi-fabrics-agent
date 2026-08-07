"""Compose multiple collectors into one estate.

Lets the live sources combine: **CSV** (authoritative CU% + per-item CU share) + **List Usages**
(capacity sku/state/quota) + **Workspace Monitoring** (per-user attribution). Item-level user
attribution is joined onto the CU-share items by ``(workspace, name)``.

Precedence is **first-non-empty-wins**, so order collectors authoritative-first: e.g.
``[csv, list_usages, workspace_monitoring]`` keeps the CSV's real CU ``sharePct`` while picking
up sku/quota from List Usages and ``topUsers`` from Workspace Monitoring.
"""
from datetime import datetime, timezone

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
    # "refreshes" (B3) was previously missing — a standalone refresh collector's facts would be
    # silently dropped on merge. Included so refresh flags survive a multi-source collect.
    # "events" (TASK 1-WIRE, 2026-08-07): raw per-operation events from an events collector
    # (adapters/collector_events_la.py, wired via job._build_events_collector) — concatenated the
    # same way as the other list domains so detectors/absolute_cost.py + detectors/query_shape.py
    # see facts["events"] in the merged sweep. Absent when no source contributes any (dropped below
    # by the same `if rows:` guard as every other extra key), matching the fail-open contract.
    extra = {"models": [], "reports": [], "pipelines": [], "users": [], "refreshes": [], "events": []}
    # Track the earliest collectedAt across sources — the merged result is only
    # as fresh as the OLDEST contributing source.
    collected_at = None
    # facts["access"] (security, FIX C) is a DICT of sub-lists, not a top-level list — merge its
    # sub-lists across sources so a standalone security collector survives the multi-source merge.
    access = {"adminGrants": [], "externalShares": [], "accessEvents": []}
    for f in facts_list or []:
        _merge_into(capacity, f.get("capacity") or {})
        for it in f.get("items") or []:
            _merge_into(items.setdefault(_item_key(it), {}), it)
        for key in extra:
            extra[key].extend(f.get(key) or [])
        a = f.get("access") or {}
        for key in access:
            access[key].extend(a.get(key) or [])
        ca = f.get("collectedAt")
        if ca is not None:
            if collected_at is None or ca < collected_at:
                collected_at = ca

    merged = {}
    if capacity:
        merged["capacity"] = capacity
    if items:
        merged["items"] = list(items.values())
    for key, rows in extra.items():
        if rows:
            merged[key] = rows
    if any(access.values()):
        merged["access"] = access
    if collected_at is not None:
        merged["collectedAt"] = collected_at
    return merged


def create_merged_collector(collectors):
    cols = list(collectors)

    def collect():
        import logging
        from concurrent.futures import ThreadPoolExecutor

        def _one(c):
            try:
                return ("ok", c["collect"]())
            except Exception as exc:
                logging.getLogger(__name__).warning("collector skipped due to error: %s", exc)
                return ("failed", str(exc))

        # Collect sources CONCURRENTLY: each live source is network-bound (its own token
        # acquisition + query), and serial collection is what pushes run_audit toward the
        # Databricks Apps 120s request ceiling as sources/windows grow. ``executor.map``
        # preserves input order, so first-non-empty-wins precedence is unchanged; per-source
        # fault tolerance is unchanged (a failing source is skipped and surfaced, not fatal).
        if len(cols) > 1:
            with ThreadPoolExecutor(max_workers=min(len(cols), 8)) as pool:
                outcomes = list(pool.map(_one, cols))
        else:
            outcomes = [_one(c) for c in cols]

        results = [payload for status, payload in outcomes if status == "ok"]
        failed = [payload for status, payload in outcomes if status == "failed"]
        if not results:
            raise RuntimeError("All collectors failed — no data to audit.")
        merged = merge_facts_list(results)
        if failed:
            # Surface coverage gaps so the agent can say "a source was unreachable" rather than
            # silently reporting a partial picture (which would skew CU shares / denominators).
            merged["sourcesFailed"] = failed
        # Ensure a collectedAt timestamp exists even when no individual source provided one
        # (e.g. CSV-only collectors pre-dating the timestamp feature).
        if "collectedAt" not in merged:
            merged["collectedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return merged

    return {"collect": collect}

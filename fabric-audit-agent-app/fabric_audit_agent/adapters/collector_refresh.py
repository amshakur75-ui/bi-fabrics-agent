"""Refresh-history CollectorPort — Power BI Get-Refresh-History -> ``facts["refreshes"]`` (B3).

Feeds ``detectors/refresh.py`` (failing / retry-storm / slow-phase / chronic). The HTTP client is
injected (``EntraHttp.get_json``), so this is unit-testable offline and swaps to a real authed
client at deploy. Read-only. Fail-open: a per-dataset error (e.g. a 404 for a workspace the SP is
not a member of) is skipped, never aborting the collect.

ACCESS REALITY (confirmed by live probe, 2026-08-05): the full per-refresh detail the detector needs
— ``status`` / ``serviceExceptionJson`` (error text) / ``refreshAttempts`` — comes from the
per-dataset endpoint ``/v1.0/myorg/groups/{group}/datasets/{dataset}/refreshes``, which requires the
service principal to be a MEMBER of the workspace. ``admin/capacities/refreshables`` is reachable
tenant-wide but returns only ``refreshSchedule`` + ``capacity`` + ``group`` (no failure detail), and
``admin/activityevents`` carries refresh *activity* without the exception JSON. So estate-wide refresh
failure detection is gated on granting the SP workspace membership (documented gap); until then this
collector produces data only for the workspaces the SP can already read.

Config (``create_refresh_collector(http, config)``):
  ``datasets``  list of ``{"group","dataset","workspace","datasetName"}`` to scan.
  ``top``       per-dataset history depth (default 20).
  ``urlTemplate``  refresh-history URL, default the group/dataset endpoint above.
"""
from datetime import datetime, timezone

_DEFAULT_URL = ("https://api.powerbi.com/v1.0/myorg/groups/{group}/datasets/{dataset}"
                "/refreshes?$top={top}")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_refresh_collector(http, config):
    cfg = config or {}
    top = int(cfg.get("top", 20))
    template = cfg.get("urlTemplate", _DEFAULT_URL)

    def collect():
        refreshes = []
        for ds in (cfg.get("datasets") or []):
            group, dataset = ds.get("group"), ds.get("dataset")
            if not (group and dataset):
                continue
            url = ds.get("refreshesUrl") or template.format(group=group, dataset=dataset, top=top)
            try:
                page = http.get_json(url)
            except Exception:
                continue  # fail-open: SP lacks membership (404) or a transient error — skip this ds
            rows = (page.get("value") if isinstance(page, dict) else page) or []
            for r in rows[:top]:
                refreshes.append({
                    "workspace": ds.get("workspace") or group,
                    "datasetName": ds.get("datasetName") or dataset,
                    "status": r.get("status"),
                    "startTime": r.get("startTime"),
                    "endTime": r.get("endTime"),
                    "refreshType": r.get("refreshType"),
                    "serviceExceptionJson": r.get("serviceExceptionJson"),
                    "refreshAttempts": r.get("refreshAttempts") or [],
                })
        return {"refreshes": refreshes, "collectedAt": _now_iso()}

    return {"collect": collect}

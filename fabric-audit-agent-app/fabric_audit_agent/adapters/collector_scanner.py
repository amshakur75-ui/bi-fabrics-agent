"""Model-metadata CollectorPort — Power BI Scanner API -> ``facts["models"]`` (FIX C).

Feeds ``detectors/model.py`` (bidirectional-relationship overuse), which in turn powers
``coaching.py``'s model tips. The Scanner API is the ONLY source that exposes a dataset's
relationships, so it's the required path for the bidirectional signal.

Async flow (all admin, confirmed reachable given current SP grants):
  POST admin/workspaces/getInfo?datasetSchema=True  -> {"id": scanId}
  GET  admin/workspaces/scanStatus/{scanId}         -> poll until status == "Succeeded"
  GET  admin/workspaces/scanResult/{scanId}         -> {"workspaces":[{"datasets":[{"relationships":[...]}]}]}

Maps each dataset to the shape model.py expects. ``bidirectionalRels`` = relationships whose
``crossFilteringBehavior`` is ``BothDirections``.

TENANT-SETTING GAP (confirmed by live scan 2026-08-06): the scan returns datasets, but their
``tables`` / ``relationships`` come back EMPTY unless the Fabric admin has enabled *"Enhance admin
API responses with detailed metadata"* (and *"...with DAX and mashup expressions"* for
relationships) in the Admin Portal tenant settings. Without those, this collector still enumerates
models but ``bidirectionalRels`` is 0 for all (no false positives) — the bidirectional signal is
gated on that one tenant setting, not on code. Documented, not silently wrong. ``autoDateTime`` and ``refreshFailRatePct`` are
NOT exposed by the scan (documented gap) -> left None, so model.py simply doesn't flag on them
(no false positives). HTTP client (get_json/post_json) + sleep are injected -> unit-testable
offline. Read-only. Fail-open: any error yields no models, never aborts the wider collect.
"""
import time
from datetime import datetime, timezone

_BASE = "https://api.powerbi.com/v1.0/myorg/admin"


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def create_scanner_models_collector(http, config):
    cfg = config or {}
    workspace_ids = list(cfg.get("workspaceIds") or [])
    base = cfg.get("baseUrl", _BASE)
    sleep = cfg.get("sleep", time.sleep)
    poll_seconds = float(cfg.get("pollSeconds", 2))
    max_polls = int(cfg.get("maxPolls", 30))

    def _scan_batch(batch):
        scan = http.post_json(f"{base}/workspaces/getInfo?datasetSchema=True", {"workspaces": batch})
        scan_id = (scan or {}).get("id")
        if not scan_id:
            return []
        for _ in range(max_polls):
            status = (http.get_json(f"{base}/workspaces/scanStatus/{scan_id}") or {}).get("status")
            if status == "Succeeded":
                break
            if status in ("Failed", "Disabled"):
                return []
            sleep(poll_seconds)
        else:
            return []  # never completed within the poll budget
        result = http.get_json(f"{base}/workspaces/scanResult/{scan_id}") or {}
        models, reports = [], []
        for ws in (result.get("workspaces") or []):
            ds_mode = {}   # datasetId -> targetStorageMode, for the report DirectQuery signal
            for ds in (ws.get("datasets") or []):
                rels = ds.get("relationships") or []
                bidi = sum(1 for r in rels
                           if (r or {}).get("crossFilteringBehavior") == "BothDirections")
                models.append({
                    "workspace": ws.get("name"), "name": ds.get("name"),
                    "observedAt": _now_iso(), "bidirectionalRels": bidi,
                    "autoDateTime": None, "refreshFailRatePct": None,
                })
                ds_mode[ds.get("id")] = ds.get("targetStorageMode")
            for rep in (ws.get("reports") or []):
                # visuals per page needs the detailed-metadata tenant setting (see docstring); 0 without.
                pages = rep.get("pages") or []
                visuals = max((len((p.get("visualObjects") or p.get("visuals")) or [])
                               for p in pages), default=0)
                mode = ds_mode.get(rep.get("datasetId"))
                reports.append({
                    "workspace": ws.get("name"), "name": rep.get("name"),
                    "visuals": visuals,
                    "mode": "DirectQuery" if mode == "DirectQuery" else rep.get("dataSourceType"),
                    "source": None, "slowestVisualMs": None,   # slow-visual has no reachable source
                })
        return models, reports

    def collect():
        models, reports = [], []
        try:
            for batch in _chunks(workspace_ids, 100):
                m, r = _scan_batch(batch)
                models.extend(m)
                reports.extend(r)
        except Exception as exc:
            print(f"[scanner] scan collect failed ({type(exc).__name__}: {exc})")
        return {"models": models, "reports": reports, "collectedAt": _now_iso()}

    return {"collect": collect}

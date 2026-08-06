"""Model-metadata CollectorPort — Power BI Scanner API -> ``facts["models"]`` (FIX C).

Feeds ``detectors/model.py`` (bidirectional-relationship overuse), which in turn powers
``coaching.py``'s model tips. The Scanner API is the ONLY source that exposes a dataset's
relationships, so it's the required path for the bidirectional signal.

Async flow (all admin, confirmed reachable given current SP grants):
  POST admin/workspaces/getInfo?datasetSchema=True  -> {"id": scanId}
  GET  admin/workspaces/scanStatus/{scanId}         -> poll until status == "Succeeded"
  GET  admin/workspaces/scanResult/{scanId}         -> {"workspaces":[{"datasets":[{"relationships":[...]}]}]}

Maps each dataset to the shape model.py expects. ``bidirectionalRels`` = relationships whose
``crossFilteringBehavior`` is ``BothDirections``. ``autoDateTime`` and ``refreshFailRatePct`` are
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
        out = []
        for ws in (result.get("workspaces") or []):
            for ds in (ws.get("datasets") or []):
                rels = ds.get("relationships") or []
                bidi = sum(1 for r in rels
                           if (r or {}).get("crossFilteringBehavior") == "BothDirections")
                out.append({
                    "workspace": ws.get("name"), "name": ds.get("name"),
                    "observedAt": _now_iso(), "bidirectionalRels": bidi,
                    "autoDateTime": None, "refreshFailRatePct": None,
                })
        return out

    def collect():
        models = []
        try:
            for batch in _chunks(workspace_ids, 100):
                models.extend(_scan_batch(batch))
        except Exception as exc:
            print(f"[scanner] models collect failed ({type(exc).__name__}: {exc})")
        return {"models": models, "collectedAt": _now_iso()}

    return {"collect": collect}

"""Run every registered detector and flatten the flags. Faithful port of ``core/detectors/index.js``.

A failing detector emits a ``meta.detector-error`` flag instead of crashing the run.
"""
from ..config import DEFAULT_CONFIG
from .capacity import detect_capacity
from .concentration import detect_concentration
from .model import detect_models
from .report import detect_reports
from .pipeline import detect_pipelines
from .blast_radius import detect_blast_radius
from .security import detect_security
from .cost import detect_cost
from .refresh import detect_refreshes
from .absolute_cost import detect_absolute_cost
from .query_shape import detect_query_shape
from .query_antipatterns import detect_query_antipatterns
from .xmla_errors import detect_xmla_errors
from .long_running import detect_long_running_cluster
from .user_baseline import detect_user_baseline_deviation_precomputed

_DETECTORS = [
    detect_capacity, detect_concentration, detect_models,
    detect_reports, detect_pipelines, detect_blast_radius, detect_security, detect_cost,
    detect_refreshes, detect_absolute_cost, detect_query_shape, detect_query_antipatterns,
    detect_xmla_errors, detect_long_running_cluster,
]


def detect_all(facts, config=None, detectors=None, *, baseline_store=None):
    """Run every registered detector and flatten the flags.

    ``baseline_store`` (Design A' B2, 2026-08-09): when provided, also runs the per-user
    baseline-deviation detector with a 3-layer fallback (personalized / estate / silent).
    Threaded from ``job.run_job`` at deploy time; ``None`` here is the safe default — the
    detector is simply skipped and no alerts fire until the nightly bootstrap job has
    populated the store.
    """
    config = config or DEFAULT_CONFIG
    detectors = detectors if detectors is not None else _DETECTORS
    flags = []
    for fn in detectors:
        try:
            flags.extend(fn(facts, config))
        except Exception as err:  # a failing detector is skipped, not fatal
            name = getattr(fn, "__name__", "unknown-detector")
            flags.append({
                "type": "meta.detector-error", "resource": name, "when": "",
                "evidence": {"detector": name, "message": str(err)},
                "what": f"Detector \"{name}\" failed and was skipped: {err}",
            })
    # B2: the per-user baseline detector takes an extra ``baseline_store`` arg beyond
    # ``(facts, config)``, so it runs outside the uniform detectors loop above. Wrapped in
    # the same failure-isolation shell — a broken store never crashes the sweep.
    if baseline_store is not None:
        try:
            flags.extend(detect_user_baseline_deviation_precomputed(
                facts, config, baseline_store=baseline_store))
        except Exception as err:
            flags.append({
                "type": "meta.detector-error",
                "resource": "detect_user_baseline_deviation_precomputed", "when": "",
                "evidence": {"detector": "detect_user_baseline_deviation_precomputed",
                             "message": str(err)},
                "what": ("Detector \"detect_user_baseline_deviation_precomputed\" failed and "
                         f"was skipped: {err}"),
            })
    # B4: after per-item detection, surface systemic anti-patterns spanning multiple workspaces.
    try:
        from .cross_workspace import cross_workspace_patterns
        min_ws = int((config.get("crossWorkspace") or {}).get("minWorkspaces", 3))
        flags.extend(cross_workspace_patterns(flags, min_workspaces=min_ws))
    except Exception:
        pass
    return flags

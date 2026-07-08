"""Run every registered detector and flatten the flags. Faithful port of ``core/detectors/index.js``.

A failing detector emits a ``meta.detector-error`` flag instead of crashing the run.
"""
from ..config import DEFAULT_CONFIG
from .capacity import detect_capacity
from .concentration import detect_concentration
from .user_concentration import detect_user_concentration
from .model import detect_models
from .report import detect_reports
from .pipeline import detect_pipelines
from .blast_radius import detect_blast_radius
from .security import detect_security
from .cost import detect_cost
from .refresh import detect_refreshes

_DETECTORS = [
    detect_capacity, detect_concentration, detect_user_concentration, detect_models,
    detect_reports, detect_pipelines, detect_blast_radius, detect_security, detect_cost,
    detect_refreshes,
]


def detect_all(facts, config=None, detectors=None):
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
    return flags

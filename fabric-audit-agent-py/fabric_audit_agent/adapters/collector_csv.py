"""CSV CollectorPort — facts from Capacity Metrics CSV / ``.vpax`` exports.

Wraps the importer so the FULL ``run_audit`` pipeline can run on a manual export, identical to
what the ``import`` CLI reads. The authoritative **CU% / throttle** signal lives here (the live
REST sources give capacity quota + per-user attribution, not the smoothed utilization timeline),
so this stays in the mix even once live sources are wired. No permissions needed.
"""
import copy
from pathlib import Path

from ..importers.csv import parse_csv
from ..importers.map import map_table, merge_facts
from ..importers.vpax import vpax_to_models
from ..importers.capacity_metrics import (
    looks_like_items, map_items, looks_like_timepoints, capacity_signal_from_timepoints,
)


def _read_text(f):
    with open(f, encoding="utf-8") as fh:
        return fh.read()


def _read_bytes(f):
    with open(f, "rb") as fh:
        return fh.read()


def build_facts_from_files(paths):
    """Parse CSV/.vpax files into the facts shape the pipeline consumes (capacity/models/reports/items)."""
    parts, items, tp_signal = [], [], None
    for f in paths or []:
        ext = Path(f).suffix.lower()
        if ext == ".csv":
            parsed = parse_csv(_read_text(f))
            headers, rows = parsed["headers"], parsed["rows"]
            if not headers:
                continue
            if looks_like_items(headers):
                items.extend(map_items(headers, rows)["items"])
            else:
                parts.append(map_table(headers, rows))
                if looks_like_timepoints(headers):
                    tp_signal = capacity_signal_from_timepoints(headers, rows)
        elif ext == ".vpax":
            res = vpax_to_models(_read_bytes(f))
            parts.append({"capacity": None, "models": res["models"], "reports": [], "coverage": res["coverage"]})
    facts = merge_facts(parts)
    if items:
        facts["items"] = items

    cap = facts.get("capacity")
    if cap and tp_signal:
        # The raw "%" column holds pre-smoothing spikes; prefer the computed p95 + Overloaded-state
        # signal when the raw peak is unusable (>1000% or absent), and fill throttle if none was read.
        raw_peak = cap.get("peakCuPct") or 0
        if tp_signal.get("peakCuPct") and (raw_peak > 1000 or raw_peak == 0):
            cap["peakCuPct"] = tp_signal["peakCuPct"]
        if tp_signal.get("throttleMinutes") and not (cap.get("throttleMinutes") or 0):
            cap["throttleMinutes"] = tp_signal["throttleMinutes"]

    # Sanitize any remaining unreadable raw-% (no usable timepoints signal) so it can't drive a bogus verdict.
    if cap and (cap.get("peakCuPct") or 0) > 1000:
        facts = copy.deepcopy(facts)
        facts["capacity"]["peakCuPct"] = 0
    return facts


def create_csv_collector(paths):
    files = [paths] if isinstance(paths, str) else list(paths)

    def collect():
        return build_facts_from_files(files)

    return {"collect": collect}

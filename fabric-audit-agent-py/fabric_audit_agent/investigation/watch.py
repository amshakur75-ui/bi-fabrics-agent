"""Autonomous watcher core — pure trigger evaluation + harmless/real classification.

The 5-minute watcher (see ``watch_run.py``) resolves the LIVE base capacity, pulls the recent
capacity stream + per-operation events, and hands them here. This module decides WHAT is worth
alerting on and HOW to frame it -- pure/stdlib, so it is fully offline-testable.

Triggers (approved 2026-07-19):
  * Capacity: any 30-second window with total CU% >= 100 OR interactive CU% >= 100.
  * Operation: any user operation at/over 30% CONVERTED of base (== 300% lifetime).

Classification (the user's ask -- always fire, but say whether it matters):
  * A capacity overage that is a brief blip (few windows) AND background-dominated is HARMLESS
    ("went over for a moment, here's why, no concern") -> severity "info".
  * A capacity overage that is SUSTAINED (>= sustained_windows consecutive 30-s windows) or
    INTERACTIVE-driven is a real problem -> severity "warn".
  * An operation over the converted threshold is always a "warn" (a 300%+ lifetime op is heavy),
    with the who/what/why attached.

Dedup: each incident carries a stable ``id`` so the orchestrator can suppress re-alerting the same
ongoing episode every 5 minutes (see ``watch_run.py``). Capacity episodes key on their FIRST
over-threshold window (stable while the episode persists); operations key on user+item+timestamp.
"""

_WINDOW_SEC = 30


def _fmt_pct(converted, lifetime):
    """The house display: '<converted>% (<lifetime>%)'. Either may be None (base unknown)."""
    if converted is None:
        return "n/a"
    return f"{converted}% ({lifetime}%)" if lifetime is not None else f"{converted}%"


def evaluate_incidents(windows, peaks, *, base_cu, cu_pct=100.0, op_pct_converted=30.0,
                       sustained_windows=3):
    """Return a list of incident dicts from the run's overload ``windows`` (see
    ``investigation.overloads.overload_windows`` -- pass ALL windows, i.e. min_cu_pct=0) and
    per-operation ``peaks`` (see ``investigation.timepoint_peaks.timepoint_peaks``).

    Each incident: ``{kind, id, severity ('info'|'warn'), emoji, title, summary, why, facts:
    [{title,value}], whenEpoch}``. Empty list == nothing worth reporting (the watcher stays silent).
    """
    incidents = []

    # ---- Capacity overage (grouped into ONE episode per run) ----
    over = [w for w in (windows or [])
            if (w.get("totalCuPct") or 0) >= cu_pct or (w.get("interactiveCuPct") or 0) >= cu_pct]
    if over:
        peak_w = max(over, key=lambda w: w.get("totalCuPct") or 0)
        first_epoch = min((w.get("windowEpoch") for w in over if w.get("windowEpoch") is not None),
                          default=peak_w.get("windowEpoch"))
        n = len(over)
        sustained = n >= sustained_windows
        inter = peak_w.get("interactiveCuPct")
        back = peak_w.get("backgroundCuPct")
        interactive_driven = inter is not None and back is not None and inter >= back
        real = sustained or interactive_driven
        contributors = peak_w.get("contributors") or []
        top = contributors[0] if contributors else None

        if real:
            driver = ("interactive user queries" if interactive_driven
                      else "sustained load")
            who = f" -- top contributor {top['user']} on {top['item']}" if top and top.get("user") else ""
            why = (f"Total CU peaked at {peak_w.get('totalCuPct')}% across {n} window(s) "
                   f"(~{n * _WINDOW_SEC}s); this looks {('interactive/user-' if interactive_driven else 'sustained-')}"
                   f"driven ({driver}){who}. Worth attention.")
            summary = f"Capacity sustained over {int(cu_pct)}% — peak {peak_w.get('totalCuPct')}%"
            severity, emoji = "warn", "⚠️"
        else:
            src = "background/system work (e.g. a refresh finishing)" if (back is not None and (inter or 0) < (back or 0)) \
                  else "a brief load spike"
            why = (f"Total CU touched {peak_w.get('totalCuPct')}% for {n} window(s) "
                   f"(~{n * _WINDOW_SEC}s) then eased -- driven by {src}. No sustained pressure; "
                   "nothing to act on.")
            summary = f"Capacity briefly over {int(cu_pct)}% — peak {peak_w.get('totalCuPct')}% (no concern)"
            severity, emoji = "info", "✅"

        facts = [
            {"title": "Peak total CU%", "value": f"{peak_w.get('totalCuPct')}%"},
            {"title": "Interactive / Background", "value": f"{inter}% / {back}%"},
            {"title": "Windows over threshold", "value": f"{n} (~{n * _WINDOW_SEC}s)"},
        ]
        if top and top.get("user"):
            facts.append({"title": "Top contributor",
                          "value": f"{top['user']} — {top.get('item')} ({top.get('cuInWindow')} CU-s)"})
        incidents.append({
            "kind": "capacity", "id": f"capacity:{first_epoch}", "severity": severity,
            "emoji": emoji, "title": f"{emoji} {summary}", "summary": summary, "why": why,
            "facts": facts, "whenEpoch": peak_w.get("windowEpoch"),
        })

    # ---- Heavy operations (each over the converted threshold) ----
    for p in (peaks or []):
        conv = p.get("pctBaseConverted")
        if conv is None or conv < op_pct_converted:
            continue
        life = p.get("pctBaseLifetime")
        user = p.get("user") or "(unattributed)"
        item = p.get("item")
        op = p.get("operation")
        detail = p.get("operationDetail")
        op_label = f"{op} / {detail}" if detail else op
        dur_s = round((p.get("durationMs") or 0) / 1000.0, 0)
        why = (f"{user} ran a {op_label} on {item} that reached {_fmt_pct(conv, life)} of base "
               f"over ~{int(dur_s)}s. That is a heavy single operation worth a look at the model/query.")
        incidents.append({
            "kind": "operation",
            "id": f"op:{(user or '').lower()}:{(item or '').lower()}:{p.get('ts')}",
            "severity": "warn", "emoji": "⚠️",
            "title": f"⚠️ Heavy operation — {user} at {_fmt_pct(conv, life)} of base",
            "summary": f"{user} — {_fmt_pct(conv, life)} of base on {item}",
            "why": why,
            "facts": [
                {"title": "User", "value": user},
                {"title": "Item", "value": str(item)},
                {"title": "Operation", "value": str(op_label)},
                {"title": "% of base", "value": _fmt_pct(conv, life)},
                {"title": "Duration", "value": f"{int(dur_s)}s"},
                {"title": "CU-seconds", "value": str(p.get("cuSeconds"))},
            ],
            "whenEpoch": None, "when": p.get("ts"),
        })

    return incidents


def new_incidents(incidents, seen_ids):
    """Filter to incidents whose id is not already in ``seen_ids`` (dedup across watcher runs)."""
    seen = set(seen_ids or [])
    return [i for i in (incidents or []) if i.get("id") not in seen]

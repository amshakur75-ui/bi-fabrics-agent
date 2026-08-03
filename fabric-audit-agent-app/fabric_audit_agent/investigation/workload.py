"""Interactive-vs-refresh workload split + refresh-collision detection.
Pure / stdlib — input is already-normalized event dicts (from events.normalize_event).
Closes the 'not yet checked: interactive query traffic vs a scheduled refresh
landing in the peak window' gap flagged by the live agent."""


def split_workload(events):
    """Split events into interactive vs refresh CU totals and compute the interactive %.

    Args:
        events: Iterable of normalized event dicts
                ({ts,user,item,workspace,operation,kind,cuSeconds,...}).

    Returns dict:
        {
            interactiveCuSeconds: float,   # sum of cuSeconds where kind=="interactive"
            refreshCuSeconds:     float,   # sum of cuSeconds where kind=="refresh"
            interactivePct:       float,   # interactiveCuSeconds / total * 100 (0 if total==0)
        }
    """
    interactive_cu = 0.0
    refresh_cu = 0.0

    for event in events:
        cu = event.get("cuSeconds") or 0.0
        if event.get("kind") == "interactive":
            interactive_cu += cu
        elif event.get("kind") == "refresh":
            refresh_cu += cu

    total = interactive_cu + refresh_cu
    interactive_pct = (interactive_cu / total * 100.0) if total > 0 else 0.0

    return {
        "interactiveCuSeconds": interactive_cu,
        "refreshCuSeconds": refresh_cu,
        "interactivePct": interactive_pct,
    }


def refresh_collisions(events, *, peak_start, peak_end):
    """Return refresh events whose timestamp falls within the peak window [peak_start, peak_end].

    The window is inclusive on both ends; comparison is lexicographic over ISO-8601 strings,
    which sorts correctly for zero-padded UTC timestamps.

    Args:
        events:     Iterable of normalized event dicts.
        peak_start: ISO-8601 string — start of the peak window (inclusive).
        peak_end:   ISO-8601 string — end of the peak window (inclusive).

    Returns:
        List of dicts [{item, ts, cuSeconds}] for each refresh event in the window.
    """
    result = []
    for event in events:
        if event.get("kind") != "refresh":
            continue
        ts = event.get("ts") or ""
        if peak_start <= ts <= peak_end:
            result.append({
                "item": event.get("item"),
                "ts": ts,
                "cuSeconds": event.get("cuSeconds") or 0.0,
            })
    return result

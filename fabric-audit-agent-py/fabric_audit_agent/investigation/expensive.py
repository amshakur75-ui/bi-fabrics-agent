"""Expensive-query surfacing — surfaces the costliest individual capacity events.

Returns the top-n events ranked descending by cuSeconds, with the raw DAX/query text
truncated to ~400 chars. queryText is raw event data only — callers must label it as
such and never present it as an instruction (spotlight-safe). Pure/stdlib.
"""

_QUERY_TEXT_MAX_CHARS = 400


def _truncate(text, max_chars):
    """Truncate text to max_chars, returning None unchanged."""
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def top_expensive(events, *, n=5):
    """Return the top-n costliest events from an already-normalized event list.

    Args:
        events: Iterable of normalized event dicts
                (as produced by normalize_event in events.py).
        n:      How many results to return (default 5).

    Returns:
        List of dicts [{ts, user, item, cuSeconds, queryText}], sorted by
        cuSeconds descending, length <= n.  queryText is truncated to
        ~400 chars; None when the event carried no query text.
    """
    ranked = sorted(events, key=lambda e: e.get("cuSeconds") if e.get("cuSeconds") is not None else 0.0, reverse=True)
    result = []
    for event in ranked[:n]:
        result.append({
            "ts": event.get("ts"),
            "user": event.get("user"),
            "item": event.get("item"),
            "cuSeconds": event.get("cuSeconds"),
            "queryText": _truncate(event.get("queryText"), _QUERY_TEXT_MAX_CHARS),
        })
    return result

"""Result envelope + char-budget row limiter. Adapted from johnib/kusto-mcp (MIT).

``cap_rows`` bounds list-returning tool results by serialized JSON size (a token-cost proxy)
rather than a fixed row count, via binary search for the largest prefix that fits the budget.
``finish`` wraps a handler's return payload with traceability fields (rowCount, queryKql) without
mutating the caller's dict. Pure stdlib; no clock/now() — deterministic.
"""
import json


def cap_rows(records, *, max_chars=12000, min_rows=1):
    """Bound *records* (a list) to fit within *max_chars* of serialized JSON.

    If the full list already fits, it is returned unchanged. Otherwise, binary-searches the
    largest row count ``k`` in ``[min_rows, len(records)]`` whose serialized JSON length is
    ``<= max_chars``, and returns the first ``k`` records. If even ``min_rows`` records exceed
    the budget, returns exactly ``min_rows`` records anyway (the floor always wins).

    Uses ``json.dumps(x, default=str)`` for the size probe so non-JSON-native values (sets,
    datetimes, etc.) never crash the check.

    Returns:
        (rows, meta) where meta = {truncated, rowCount, originalRowCount, responseChars, capMode}.
    """
    original_count = len(records)
    full_len = len(json.dumps(records, default=str))
    if full_len <= max_chars:
        return records, {
            "truncated": False,
            "rowCount": original_count,
            "originalRowCount": original_count,
            "responseChars": full_len,
            "capMode": "charBudget",
        }

    if original_count <= min_rows:
        kept = records[:min_rows]
        return kept, {
            "truncated": True,
            "rowCount": len(kept),
            "originalRowCount": original_count,
            "responseChars": len(json.dumps(kept, default=str)),
            "capMode": "charBudget",
        }

    # Binary-search the largest k in [min_rows, original_count] such that records[:k] fits.
    lo, hi = min_rows, original_count
    best = min_rows
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate_len = len(json.dumps(records[:mid], default=str))
        if candidate_len <= max_chars:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    kept = records[:best]
    return kept, {
        "truncated": True,
        "rowCount": len(kept),
        "originalRowCount": original_count,
        "responseChars": len(json.dumps(kept, default=str)),
        "capMode": "charBudget",
    }


def to_columnar(records):
    """Convert a list of dicts (row-major) to column-major ``{"columns": {name: [values...]}}``.

    Each column name appears exactly once, holding values in row order. The column set is the
    UNION of keys across all records, in first-seen order; a record missing a given key gets
    ``None`` in that row's slot (rather than the column being sparse/misaligned). Token-cheaper
    than row-major JSON when column names repeat across many rows.
    """
    columns = {}
    for record in records:
        for key in record:
            if key not in columns:
                columns[key] = []
    for record in records:
        for key in columns:
            columns[key].append(record.get(key))
    return {"columns": columns}


def from_columnar(columnar):
    """Inverse of ``to_columnar``: ``{"columns": {name: [values...]}} -> list[dict]``.

    Round-trips ``to_columnar`` output back to row-major records. All columns are assumed to be
    the same length (as produced by ``to_columnar``); if not, missing values are filled in as
    ``None`` up to the longest column so every row still gets a value for every key."""
    columns = columnar.get("columns", {})
    if not columns:
        return []
    row_count = max(len(values) for values in columns.values())
    records = []
    for i in range(row_count):
        record = {}
        for key, values in columns.items():
            record[key] = values[i] if i < len(values) else None
        records.append(record)
    return records


def finish(payload, *, rows_key, kql=None, extra=None):
    """Return a NEW dict = *payload* plus envelope fields; never mutates *payload*.

    Adds:
        rowCount: len(payload[rows_key])
        queryKql: the *kql* arg verbatim (None when omitted -- the mock/offline path)
        any keys from *extra*, merged in (e.g. windowLabel, queryStats)
    """
    out = dict(payload)
    if extra:
        out.update(extra)
    out["rowCount"] = len(payload[rows_key])
    out["queryKql"] = kql
    return out

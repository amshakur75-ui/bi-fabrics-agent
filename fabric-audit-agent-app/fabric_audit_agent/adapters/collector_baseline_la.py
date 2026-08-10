"""Baseline aggregate collector — per-user + estate CPU-second percentiles from Log Analytics.

Computes the percentiles SERVER-SIDE (KQL ``summarize percentile(...)``) instead of pulling raw
rows and reducing in Python.

WHY THIS EXISTS (2026-08-10). The nightly bootstrap originally reused
``job._build_events_collector``, which is a SWEEP collector: it caps at ``_EVENTS_CAP`` (5,000)
rows ordered ``by cost desc``. Over a 14-day window that keeps roughly the top 1% of events, so
the "p95" it produced was closer to the true 99.9th percentile. Measured live on this tenant the
estate p95 came out at **1,052 CPU-s** — i.e. "an anomaly is 17+ minutes of CPU time" — and only
tail-heavy users cleared ``min_history`` at all, because a user needed 20 events *inside the
global top-5,000*. The cohort was selected by the very variable being measured.

Aggregating in KQL removes the cap entirely: the percentile is computed over every matching row
in the window and only a few hundred summary rows come back over the wire. That is both correct
and dramatically cheaper than the raw pull it replaces.

``query(kql) -> list[dict]`` is injected (``adapters.clients.build_log_analytics_query`` at
deploy). Read-only.
"""
from ..query.kql_guard import escape_string, first_statement

# Storage-engine sub-query children (``VertiPaqSEQueryEnd``, ...) are CHILDREN of a QueryEnd;
# counting them double-counts cost and pollutes the distribution with raw scans. Same exclusion
# the sweep's event collector applies.
_DEFAULT_EXCLUDE_PREFIXES = ("VertiPaqSE",)

# Shared prefix: resolve the acting user (ExecutingUser, else EffectiveUsername — XMLA read
# sessions leave ExecutingUser empty) and derive CPU-seconds on the SAME definition
# ``investigation.events.normalize_event`` uses: coalesce(CpuTimeMs, DurationMs) / 1000.
# Keeping these identical is what makes a precomputed baseline comparable to a live event.
_PREFIX = (
    "PowerBIDatasetsWorkspace",
    "{window}",
    "| extend _euser = iff(isnotempty(ExecutingUser), tostring(ExecutingUser), "
    "tostring(column_ifexists('EffectiveUsername', '')))",
    "| where isnotempty(_euser)",
)

_COST = "| extend _cu = todouble(coalesce(CpuTimeMs, DurationMs)) / 1000.0"


def _base_lines(window_clause, exclude_prefixes):
    lines = [p.format(window=window_clause) for p in _PREFIX]
    for pref in (exclude_prefixes or ()):
        lines.append('| where not(OperationName startswith "{}")'.format(escape_string(pref)))
    lines.append(_COST)
    lines.append("| where isnotnull(_cu)")
    return lines


def build_per_user_kql(window_clause, exclude_prefixes=_DEFAULT_EXCLUDE_PREFIXES):
    """Per-user percentiles over the whole window — no row cap, no cost ordering."""
    lines = _base_lines(window_clause, exclude_prefixes)
    lines.append(
        "| summarize p50 = percentile(_cu, 50), p95 = percentile(_cu, 95), "
        "sampleCount = count(), minCu = min(_cu), maxCu = max(_cu) by _euser"
    )
    # tolower IS LOAD-BEARING. `investigation.events.normalize_event` lowercases the user
    # (`(_identity_email(row) or "").lower()`), so the LIVE event carries
    # "abdishakur.mohamed@newellco.com". Without tolower here the baseline row keys on
    # "Abdishakur.Mohamed@newellco.com", `per_user.get(user)` misses, and EVERY mixed-case user
    # is silently demoted to the estate baseline forever — while the card still reads
    # "33x the estate-wide p95 (their personalized baseline isn't ready yet)" for someone who
    # has 14 days of history sitting in the table. Defeats the whole "their own history" premise
    # and looks like it is working.
    lines.append("| project user = tolower(_euser), p50, p95, sampleCount, minCu, maxCu")
    return first_statement("\n".join(lines))


def build_estate_kql(window_clause, exclude_prefixes=_DEFAULT_EXCLUDE_PREFIXES):
    """One estate-wide row across all users — the layer-2 fallback for cold-start users.

    Computed as its own aggregate, NOT derived from the per-user percentiles (an average of
    percentiles is not a percentile).
    """
    lines = _base_lines(window_clause, exclude_prefixes)
    lines.append(
        "| summarize p50 = percentile(_cu, 50), p95 = percentile(_cu, 95), "
        "sampleCount = count(), minCu = min(_cu), maxCu = max(_cu)"
    )
    lines.append("| project p50, p95, sampleCount, minCu, maxCu")
    return first_statement("\n".join(lines))


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _row_to_baseline(row, *, scope, user, as_of):
    """Map one LA summary row to the canonical baseline-row shape (context_user_baseline)."""
    count = _num(row.get("sampleCount"))
    return {
        "scope": scope,
        # Lowercase defensively too — the KQL already does tolower(), but a `kql` override or a
        # different source must not be able to reintroduce the case mismatch described above.
        "user": user.lower() if isinstance(user, str) else user,
        "p50": _num(row.get("p50")),
        "p95": _num(row.get("p95")),
        "count": int(count) if count is not None else None,
        "min": _num(row.get("minCu")),
        "max": _num(row.get("maxCu")),
        "asOf": as_of,
    }


def create_baseline_collector(query, config=None):
    """Return ``{"collect": () -> list[baseline row dict]}`` — rows ready for
    ``context_user_baseline``'s ``upsert_many``.

    ``config`` keys:
      ``window``          full KQL WHERE clause (default ``"| where TimeGenerated > ago(14d)"``)
      ``minHistory``      drop per-user rows with fewer samples (default 20). The estate row is
                          ALWAYS emitted when there is any data, so cold-start coverage never
                          gaps even if no individual user qualifies.
      ``asOf``            ISO-8601 stamp written onto every row (caller supplies; this module
                          stays deterministic for tests).
      ``excludePrefixes`` OperationName denylist (default ``("VertiPaqSE",)``).
    """
    cfg = config or {}
    window = cfg.get("window", "| where TimeGenerated > ago(14d)")
    exclude = cfg.get("excludePrefixes", _DEFAULT_EXCLUDE_PREFIXES)
    min_history = int(cfg.get("minHistory", 20))
    as_of = cfg.get("asOf")
    per_user_kql = build_per_user_kql(window, exclude)
    estate_kql = build_estate_kql(window, exclude)

    def collect():
        rows = []
        for r in (query(per_user_kql) or []):
            user = r.get("user") or r.get("_euser")
            if not user:
                continue
            b = _row_to_baseline(r, scope="user", user=user, as_of=as_of)
            if b["count"] is None or b["count"] < min_history or b["p95"] is None:
                continue
            rows.append(b)
        for r in (query(estate_kql) or []):
            b = _row_to_baseline(r, scope="estate", user=None, as_of=as_of)
            if b["count"]:
                rows.append(b)
            break   # a no-`by` summarize returns exactly one row
        return rows

    return {"collect": collect, "kql": per_user_kql, "estateKql": estate_kql}

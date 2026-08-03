"""Alert-on-change decision for the scheduled sweep (Phase 6). Pure, no I/O.

Given the current run *envelope* and the PREVIOUS run *history* (captured BEFORE the sweep
appended the current run to the store), decide whether a MATERIAL change vs the last run
warrants an alert. Low-noise by construction: alert on CHANGE, never on standing state.
Read-only — this only decides what to surface; it never acts, writes, or remediates.

Signal source of truth is the per-key LEVEL delta (not any ``score.reason`` string), so it
covers both annotated escalation (``automation/escalate.py``) and any severity increase.
The current envelope stores levels at ``data.findings[].score.level``; the history record
stores a FLAT ``level`` per finding (see ``pipeline.py`` run-append). Both are read here.
"""

# Finding severity ordering.
_LEVEL_RANK = {"Info": 1, "Warning": 2, "Critical": 3}
# Capacity verdict ordering. ``unknown`` is intentionally absent: transitions to/from it are
# recorded for visibility but never by themselves count as "worse" (avoids telemetry-gap flapping).
_VERDICT_RANK = {"healthy": 1, "optimize": 2, "size-up": 3}


def _rank(level):
    return _LEVEL_RANK.get(level, 0)


def _floor(min_level):
    # Unknown min_level falls back to Warning (the default operating floor).
    return _LEVEL_RANK.get(min_level, _LEVEL_RANK["Warning"])


def decide_alert(envelope, prev_history, *, min_level="Warning"):
    """Return ``{"alert": bool, "reason": str, "changes": {...}}``.

    ``changes`` carries ONLY the deltas that qualified at/above *min_level* (where level-gated):
    ``new`` / ``worsened`` / ``resolved`` (lists of keys), ``verdictChange`` / ``slaIncrease``
    (``{"from","to"}`` or absent). ``prev_history`` must be the history as-of BEFORE the current
    run was appended (its last entry is the previous run). An empty ``prev_history`` is the first
    run: ``reason`` is ``"baseline"`` and only genuinely new findings / a first SLA breach qualify.
    """
    data = (envelope or {}).get("data") or {}
    floor = _floor(min_level)

    # ---- current run: active (score.level) + suppressed keys ----
    cur_active = {}
    for f in data.get("findings") or []:
        k = f.get("key")
        if k is not None:
            cur_active[k] = (f.get("score") or {}).get("level")
    # A snoozed finding lives in data.suppressed, NOT data.findings. Union both so a snooze is
    # never mis-reported as a resolution.
    cur_all_keys = set(cur_active) | {
        s.get("key") for s in (data.get("suppressed") or []) if s.get("key") is not None
    }

    # ---- previous run (flat level per finding) ----
    prev_run = prev_history[-1] if prev_history else None
    is_baseline = prev_run is None
    prev_levels = {}
    prev_nonsuppressed = set()
    for r in (prev_run or {}).get("findings") or []:
        k = r.get("key")
        if k is None:
            continue
        prev_levels[k] = r.get("level")
        if not r.get("suppressed"):
            prev_nonsuppressed.add(k)

    # ---- key-level deltas ----
    new = [k for k, lvl in cur_active.items() if k not in prev_levels and _rank(lvl) >= floor]
    worsened = [
        k for k, lvl in cur_active.items()
        if k in prev_levels and _rank(lvl) > _rank(prev_levels[k]) and _rank(lvl) >= floor
    ]
    resolved = [] if is_baseline else [
        k for k in prev_nonsuppressed
        if k not in cur_all_keys and _rank(prev_levels.get(k)) >= floor
    ]

    # ---- verdict change (material only when it worsens on the ordinal) ----
    verdict_change = None
    verdict_worse = False
    if not is_baseline:
        cur_dec = (data.get("verdict") or {}).get("decision")
        prev_dec = prev_run.get("verdictDecision")
        if prev_dec is not None and cur_dec is not None and cur_dec != prev_dec:
            verdict_change = {"from": prev_dec, "to": cur_dec}
            pr, cr = _VERDICT_RANK.get(prev_dec), _VERDICT_RANK.get(cur_dec)
            if pr is not None and cr is not None and cr > pr:
                verdict_worse = True

    # ---- SLA breach: alert on INCREASE only (standing breach does not re-alert) ----
    sla_increase = None
    cur_breached = (data.get("sla") or {}).get("breachedCount", 0) or 0
    prev_breached = 0 if is_baseline else (prev_run.get("slaBreachedCount", 0) or 0)
    if cur_breached > prev_breached:
        sla_increase = {"from": prev_breached, "to": cur_breached}

    changes = {}
    if new:
        changes["new"] = sorted(new)
    if worsened:
        changes["worsened"] = sorted(worsened)
    if resolved:
        changes["resolved"] = sorted(resolved)
    if verdict_change:
        changes["verdictChange"] = verdict_change
    if sla_increase:
        changes["slaIncrease"] = sla_increase

    alert = bool(new or worsened or resolved or verdict_worse or sla_increase)

    if is_baseline:
        reason = "baseline"
    elif not alert:
        reason = "no material change"
    else:
        parts = []
        if new:
            parts.append(f"{len(new)} new")
        if worsened:
            parts.append(f"{len(worsened)} worsened")
        if resolved:
            parts.append(f"{len(resolved)} resolved")
        if verdict_worse:
            parts.append(f"verdict {verdict_change['from']}→{verdict_change['to']}")
        if sla_increase:
            parts.append(f"SLA breaches {sla_increase['from']}→{sla_increase['to']}")
        reason = ", ".join(parts)

    return {"alert": alert, "reason": reason, "changes": changes}

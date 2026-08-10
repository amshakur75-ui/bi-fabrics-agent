"""Daily capacity digest (Step 10) — a once-a-day summary card with an Acknowledge action.

A pure card builder (``build_daily_summary``) plus a delivery orchestrator (``run_daily_summary``)
that reuses the SAME infrastructure as the Tier-2 alert path — no new tables, no new UI:

- **Open tickets** come from the shared ``audit_alerts`` store (``query_active``), minus the digest's
  own rows.
- **Acknowledgement** rides on the existing ``alert_ack`` store. The digest is pre-created as a
  public chat (owned by ``fabric-audit-agent``) so it lands in the app's Alerts sidebar and can be
  acknowledged / resolved there with the controls that already exist.
- **Re-surfacing** is stateful across days: each digest is recorded as a ``daily_summary`` row. The
  next run resolves the ones the team acknowledged and counts the rest, so anything still open comes
  back as an "N earlier summaries awaiting acknowledgement" banner on the new card.

Read-only posture is absolute — the digest is a notification, it never acts on capacity.

REDESIGN (tightening.md Part 13/14, docs/superpowers/specs/2026-08-07-alerting-redesign-and-
plugin-parity-design.md Sub-plan 3): the digest used to LEAD with Peak true CU% / throttle minutes
and dump every open ticket in one flat list. It now leads with the Part 12 BAD-activity taxonomy —
refresh failures / query performance (recurring-shape vs one-off) / slow operations / XMLA errors —
each its OWN section, plus a plain Top-N users ranking. CU is demoted to a single one-line
"Capacity context:" cross-reference near the bottom, and is DROPPED ENTIRELY (never a fallback) when
there's nothing to report — "No significant issues found in today's activity" is itself the answer,
not a gap to fill with a percentage.
"""
from datetime import datetime, timezone

DIGEST_CHECK = "daily_summary"
_SYSTEM_USER_ID = "fabric-audit-agent"
_MAX_TICKET_LINES = 8  # cap each per-category list so the card stays mobile-friendly
_TOP_USERS_LIMIT = 10  # Part 13: "Top 10 users of the day"

# Taxonomy classification (tightening.md Part 12). A ticket's exact finding TYPE (e.g.
# "activity.slow-operation") is more precise than its ``checkType`` family (e.g. "activity" —
# the first dot-segment, see automation/sweep_delivery.py:_family) because the family collapses
# recurring-shape / long-running-cluster / slow-operation together. The reasoner stamps
# ``incidentKey = f"{flag['type']}::{flag['resource']}"`` (adapters/reasoner_claude.py,
# reasoner_stub.py), so the exact type is recoverable from the incidentKey prefix; checkType is
# the fallback for any row that doesn't follow that shape (defensive, e.g. hand-built test rows).
_REFRESH_PREFIX = "refresh."
_XMLA_PREFIX = "xmla."
# Category 2/4 "recurring shape / design issue" — the SAME shape recurring across events/days,
# pointing at a report/model design flaw rather than a one-off event.
_RECURRING_SHAPE_TYPES = {"query.mdx-crossjoin", "query.dax-antipattern", "activity.recurring-shape"}
# The rest of query performance: clusters of expensive queries against one item.
_QUERY_PERF_OTHER_TYPES = {"activity.long-running-cluster"}
_SLOW_OP_TYPES = {"activity.slow-operation"}


def digest_key(date_str):
    """Stable incident key for a day's digest row (one per calendar day)."""
    return f"{DIGEST_CHECK}::{date_str}"


def _sev_counts(tickets):
    warn = sum(1 for t in tickets if (t.get("severity") or "").lower() == "warn")
    info = len(tickets) - warn
    return warn, info


def _ticket_line(t):
    emoji = "⚠️" if (t.get("severity") or "").lower() == "warn" else "ℹ️"
    check = t.get("checkType") or "incident"
    res = t.get("resource") or "capacity"
    reason = t.get("materialityReason") or t.get("investigationSummary") or ""
    active = "" if t.get("currentlyActive") is not False else " (currently inactive)"
    tail = f" — {reason}" if reason else ""
    return f"{emoji} {check} ({res}){active}{tail}"


def _finding_type(t):
    """The exact taxonomy finding type for a ticket row (see module docstring); ``checkType``
    (the coarser family) when the incidentKey doesn't carry the ``type::resource`` shape."""
    key = t.get("incidentKey") or ""
    if "::" in key:
        return key.split("::", 1)[0]
    return t.get("checkType") or ""


def _categorize(open_tickets):
    """Split open tickets into the Part 12 taxonomy sections. Returns a dict of lists:
    ``refresh``, ``query_recurring``, ``query_other``, ``slow_ops``, ``xmla``, ``other`` (anything
    not yet in the taxonomy, e.g. legacy model/cost/security findings — kept, never silently
    dropped, just not the digest's main focus)."""
    buckets = {"refresh": [], "query_recurring": [], "query_other": [], "slow_ops": [],
               "xmla": [], "other": []}
    for t in open_tickets:
        ft = _finding_type(t)
        if ft.startswith(_REFRESH_PREFIX):
            buckets["refresh"].append(t)
        elif ft in _RECURRING_SHAPE_TYPES:
            buckets["query_recurring"].append(t)
        elif ft in _QUERY_PERF_OTHER_TYPES:
            buckets["query_other"].append(t)
        elif ft in _SLOW_OP_TYPES:
            buckets["slow_ops"].append(t)
        elif ft.startswith(_XMLA_PREFIX):
            buckets["xmla"].append(t)
        else:
            buckets["other"].append(t)
    return buckets


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _top_users(open_tickets, events, limit=_TOP_USERS_LIMIT):
    """Rank the day's heaviest users — a plain ranking, no capacity %, ever.

    Preferred source: ``events`` (``facts["events"]``, the same normalize_event-shaped list the
    Part 12 detectors read — ``run_daily_summary_job`` wires it through from the 1d collect it
    already fetches for capacity). Ranked by summed ``cuSeconds`` (falling back to operation
    count when no event carries a usable cuSeconds), source noted as ``"events"``.

    Fallback when no event-level data was passed in: rank by how many ``activity.slow-operation``
    findings (the ONLY taxonomy type whose ``resource`` is actually a user login — see
    detectors/absolute_cost.py; recurring-shape / long-running-cluster key by ITEM, not user, and
    must not be miscounted as a per-user ranking) each user has open today. This is real data
    already on hand, not invented, but it's a finding-count proxy, not true CU-seconds — the
    caller is told so via the returned ``source`` so the text can note the limitation.

    Returns ``(ranked, source)`` where ``ranked`` is a list of
    ``{"user", "cuSeconds"|None, "operations"}`` and ``source`` is ``"events"``,
    ``"costUnknown"`` (events were passed in but NONE carried a usable ``cuSeconds`` — ranked by
    operation count instead of fabricating a 0.0 CU-s cost), or ``"findingCount"`` (or ``None``
    when there is nothing to rank).
    """
    events = list(events or [])
    if events:
        agg = {}
        any_usable_cu = False
        for ev in events:
            user = ev.get("user")
            if not user:
                continue
            cu_raw = _num(ev.get("cuSeconds"))
            if cu_raw is not None:
                any_usable_cu = True
            row = agg.setdefault(user, {"user": user, "cuSeconds": 0.0, "operations": 0})
            row["cuSeconds"] += cu_raw or 0.0
            row["operations"] += 1
        if agg:
            # BUG 5 fix: when events exist but none carry a usable cuSeconds, ranking by (all-zero)
            # cuSeconds and reporting source="events" fabricates "0.0 CU-s" — a real cost that
            # just isn't known, not a zero cost. Fall back to an operation-count ranking labeled
            # honestly as cost-unknown instead.
            if any_usable_cu:
                ranked = sorted(agg.values(), key=lambda r: (-r["cuSeconds"], -r["operations"]))
                return ranked[:limit], "events"
            ranked = sorted(
                ({"user": r["user"], "cuSeconds": None, "operations": r["operations"]}
                 for r in agg.values()),
                key=lambda r: -r["operations"])
            return ranked[:limit], "costUnknown"

    counts = {}
    for t in open_tickets:
        if _finding_type(t) not in _SLOW_OP_TYPES:  # the only type whose resource is a user
            continue
        user = t.get("resource")
        if not user:
            continue
        counts[user] = counts.get(user, 0) + 1
    if counts:
        ranked = sorted(
            ({"user": u, "cuSeconds": None, "operations": c} for u, c in counts.items()),
            key=lambda r: -r["operations"])
        return ranked[:limit], "findingCount"

    return [], None


_SECTION_TITLES = {
    "refresh": "Refresh failures",
    "query_recurring": "Recurring shape (design issue)",
    "query_other": "Other query performance issues",
    "slow_ops": "Slow operations",
    "xmla": "XMLA / connection errors",
    "other": "Other findings",
}


def _section_lines(tickets):
    lines = [f"- {_ticket_line(t)}" for t in tickets[:_MAX_TICKET_LINES]]
    if len(tickets) > _MAX_TICKET_LINES:
        lines.append(f"- …and {len(tickets) - _MAX_TICKET_LINES} more")
    return lines


def _top_users_lines(ranked, source):
    lines = []
    for i, r in enumerate(ranked, 1):
        if source == "events":
            cu = r["cuSeconds"]
            lines.append(f"{i}. {r['user']} — {cu:.1f} CPU-s ({r['operations']} operation(s))")
        elif source == "costUnknown":
            lines.append(f"{i}. {r['user']} — {r['operations']} operation(s) (cost unknown)")
        else:
            lines.append(f"{i}. {r['user']} — {r['operations']} finding(s)")
    return lines


def build_daily_summary(*, open_tickets, capacity, coverage_gaps, date_str,
                        app_url="", ack_url=None, unacked_prior=0, informational=None,
                        events=None, health=None, stale_open=None):
    """Build the digest as ``(markdown, card, summary)``. Pure — no I/O.

    ``open_tickets``: active ``audit_alerts`` rows (digest AND capacity rows already excluded —
    capacity has its own real-time alert + auto-resolve lifecycle, so it does not belong here).
    Classified into the Part 12 taxonomy (refresh / query performance / slow operations / xmla /
    other) and rendered as its own section — CU is NOT the headline (see ``capacity`` below).
    ``capacity``: ``{"peakCuPct": float|None, "throttleMinutes": float|None}`` day high-water, or
    {}. Rendered as a single "Capacity context:" cross-reference line, never the lead, and DROPPED
    entirely when the taxonomy found nothing today (no CU fallback for an empty digest).
    ``coverage_gaps``: short strings (e.g. silent-failure / coverage-gap notes) to list.
    ``unacked_prior``: number of earlier digests not yet acknowledged (drives the banner).
    ``informational``: stable, known attribution patterns (Fix A) — noted for awareness, no action.
    ``events``: optional ``facts["events"]``-shaped list for the Top-N users ranking (see
    ``_top_users``); falls back to a finding-count proxy from ``open_tickets`` when omitted.
    ``ack_url``: where the Acknowledge action points (defaults to the app root ``{app_url}/`` — the
    app has no ``/alerts`` route, so callers should pass the digest chat deep-link when they have it).
    ``health``: optional ``automation.health.HealthReport`` — when degraded, a banner is rendered
    at the TOP of the digest (both markdown and card) so a silent outage (a failed collector, a
    dropped chat/ticket write, a drifted startup invariant) becomes visible instead of only living
    in job logs. Omitted or healthy -> no banner, matching today's shape exactly.
    """
    from .health import render_health_line
    health_line = render_health_line(health)
    open_tickets = list(open_tickets or [])
    stale_open = list(stale_open or [])   # still-open backlog whose finding stopped firing
    coverage_gaps = list(coverage_gaps or [])
    informational = list(informational or [])
    capacity = capacity or {}
    peak = capacity.get("peakCuPct")
    throttle = capacity.get("throttleMinutes")

    buckets = _categorize(open_tickets)
    n_taxonomy = sum(len(buckets[k]) for k in ("refresh", "query_recurring", "query_other",
                                                "slow_ops", "xmla", "other"))
    has_issues = n_taxonomy > 0
    ranked_users, users_source = _top_users(open_tickets, events)

    # ---- markdown (the pre-created chat body; also a plain-text fallback) ----
    md = [f"# 📋 Daily Fabric capacity summary — {date_str}", ""]
    if health_line:
        md.append(f"> {health_line}\n")
    if unacked_prior:
        s = "summary" if unacked_prior == 1 else "summaries"
        md.append(f"> ⚠️ {unacked_prior} earlier daily {s} still awaiting acknowledgement.\n")
    if stale_open:
        # Backlog banner (informational, not counted in "Findings today"): tickets still marked
        # open in the store but whose underlying finding is no longer firing. Prompt the reader
        # to clear them from the notification center instead of letting them clutter the count.
        n = len(stale_open)
        md.append(
            f"> ℹ️ {n} earlier ticket{'s' if n != 1 else ''} still marked open but no longer "
            "firing — clear them from the notification center to keep the count clean.\n")

    if has_issues:
        warn, info = _sev_counts(open_tickets)
        md.append(f"**Findings today:** {n_taxonomy} ({warn} warning, {info} info)")
    else:
        md.append("**No significant issues found in today's activity.**")

    if buckets["refresh"]:
        md += ["", f"## {_SECTION_TITLES['refresh']}"]
        md += _section_lines(buckets["refresh"])

    if buckets["query_recurring"] or buckets["query_other"]:
        md += ["", "## Query performance"]
        if buckets["query_recurring"]:
            md += ["", f"### {_SECTION_TITLES['query_recurring']}"]
            md += _section_lines(buckets["query_recurring"])
        if buckets["query_other"]:
            md += ["", f"### {_SECTION_TITLES['query_other']}"]
            md += _section_lines(buckets["query_other"])

    if buckets["slow_ops"]:
        md += ["", f"## {_SECTION_TITLES['slow_ops']}"]
        md += _section_lines(buckets["slow_ops"])

    if buckets["xmla"]:
        md += ["", f"## {_SECTION_TITLES['xmla']}"]
        md += _section_lines(buckets["xmla"])

    if buckets["other"]:
        md += ["", f"## {_SECTION_TITLES['other']}"]
        md += _section_lines(buckets["other"])

    if ranked_users:
        md += ["", "## Top users today"]
        if users_source == "findingCount":
            md.append("_No per-event CU-seconds data for this digest — ranked by open finding "
                       "count instead._")
        elif users_source == "costUnknown":
            md.append("_Events present but none carried usable CU-seconds — ranked by operation "
                       "count instead._")
        md += _top_users_lines(ranked_users, users_source)

    if coverage_gaps:
        md += ["", "## Coverage gaps"]
        md += [f"- {g}" for g in coverage_gaps]
    if informational:
        md += ["", "## Stable patterns (informational — no action needed)"]
        for t in informational[:_MAX_TICKET_LINES]:
            md.append(f"- {_ticket_line(t)}")
        if len(informational) > _MAX_TICKET_LINES:
            md.append(f"- …and {len(informational) - _MAX_TICKET_LINES} more")

    if has_issues and (peak is not None or throttle is not None):
        bits = []
        if peak is not None:
            bits.append(f"{peak:.0f}% peak true CU%".replace("%%", "%"))
        if throttle is not None:
            bits.append(f"{throttle:.0f} throttle min")
        md += ["", f"---\n_Capacity context: {', '.join(bits)} — see capacity alerts for detail._"]

    markdown = "\n".join(md)

    # ---- adaptive card (v1.2 for mobile Teams) ----
    header = f"📋 Daily Fabric capacity summary — {date_str}"
    body = [{"type": "TextBlock", "text": header, "weight": "Bolder",
             "size": "Medium", "wrap": True}]
    if health_line:
        body.append({"type": "TextBlock", "wrap": True, "size": "Small", "weight": "Bolder",
                     "color": "Attention", "text": health_line})
    if unacked_prior:
        s = "summary" if unacked_prior == 1 else "summaries"
        body.append({"type": "TextBlock", "wrap": True, "size": "Small", "weight": "Bolder",
                     "color": "Warning",
                     "text": f"⚠️ {unacked_prior} earlier daily {s} still awaiting acknowledgement."})

    if has_issues:
        warn, info = _sev_counts(open_tickets)
        facts = [{"title": "Findings today", "value": f"{n_taxonomy} ({warn} warning, {info} info)"}]
        for key in ("refresh", "query_recurring", "query_other", "slow_ops", "xmla", "other"):
            if buckets[key]:
                facts.append({"title": _SECTION_TITLES[key], "value": str(len(buckets[key]))})
    else:
        facts = [{"title": "Findings today", "value": "No significant issues found ✅"}]
    if coverage_gaps:
        facts.append({"title": "Coverage gaps", "value": str(len(coverage_gaps))})
    if informational:
        facts.append({"title": "Stable patterns (info)", "value": str(len(informational))})
    if has_issues and (peak is not None or throttle is not None):
        bits = []
        if peak is not None:
            bits.append(f"{peak:.0f}%".replace("%%", "%"))
        if throttle is not None:
            bits.append(f"{throttle:.0f} min throttle")
        facts.append({"title": "Capacity context", "value": " / ".join(bits)})
    body.append({"type": "FactSet", "facts": facts})

    for key in ("refresh", "query_recurring", "query_other", "slow_ops", "xmla", "other"):
        tickets = buckets[key]
        if not tickets:
            continue
        listing = "\n".join(f"- {_ticket_line(t)}" for t in tickets[:_MAX_TICKET_LINES])
        if len(tickets) > _MAX_TICKET_LINES:
            listing += f"\n- …and {len(tickets) - _MAX_TICKET_LINES} more"
        body.append({"type": "TextBlock", "text": f"**{_SECTION_TITLES[key]}**\n{listing}",
                     "wrap": True})

    if ranked_users:
        if users_source == "findingCount":
            note = "_No per-event CU-seconds data — ranked by open finding count._\n"
        elif users_source == "costUnknown":
            note = "_No usable CU-seconds on today's events — ranked by operation count._\n"
        else:
            note = ""
        listing = note + "\n".join(_top_users_lines(ranked_users, users_source))
        body.append({"type": "TextBlock", "text": f"**Top users today**\n{listing}", "wrap": True})

    content = {"type": "AdaptiveCard",
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
               "version": "1.2", "body": body}
    target = ack_url or (f"{app_url.rstrip('/')}/" if app_url else None)
    if target:
        content["actions"] = [{"type": "Action.OpenUrl",
                               "title": "Review & acknowledge", "url": target}]
    card = {"contentType": "application/vnd.microsoft.card.adaptive", "content": content}

    summary = (f"Daily summary {date_str}: "
               + (f"{n_taxonomy} finding(s)" if has_issues else "no significant issues")
               + (f", {unacked_prior} prior unacknowledged" if unacked_prior else "") + ".")
    return markdown, card, summary


def _is_acknowledged(ack_store, chat_id):
    if not (ack_store and chat_id):
        return False
    try:
        t = ack_store["get"](chat_id)
    except Exception:
        return False
    return bool(t) and (t.get("status") or "").lower() in ("acked", "resolved")


def run_daily_summary(*, alerts_store, ack_store=None, capacity=None, coverage_gaps=None,
                      delivery_sinks=None, chat_writer=None, app_url="", now_dt=None,
                      events=None, health=None):
    """Compose + deliver today's digest, reconcile prior digests, and record today's.

    Returns ``{"delivered", "openTickets", "unackedPrior", "digestKey", "chatId"}``. All I/O ports
    are injected so this is unit-testable with fakes (no Lakebase / Delta / webhook needed).
    ``events``: optional ``facts["events"]``-shaped list (see ``_top_users``); ``job.py`` wires
    this through from the same 1d collect it already runs for the capacity high-water numbers.
    ``health``: optional ``automation.health.HealthReport`` accumulated by the caller (collector /
    startup-invariant / delivery outcomes) — threaded into ``build_daily_summary`` for the banner,
    and this function also records the digest's OWN chat-write outcome into it below.
    """
    from ..outbound import dispatch_outbound

    now_dt = now_dt if now_dt is not None else datetime.now(timezone.utc)
    date_str = now_dt.date().isoformat()
    now_iso = now_dt.isoformat().replace("+00:00", "Z")

    active = alerts_store["query_active"]()
    prior_digests = {k: v for k, v in active.items() if v.get("checkType") == DIGEST_CHECK}
    # FIX B: capacity incidents (throttle/pressure/overage) have their own real-time alert +
    # auto-resolve lifecycle — they must NOT also appear in the digest (that mixes already-resolved
    # real-time events with the attribution/coverage issues the digest actually exists to roll up).
    # Design A' (2026-08-09): the capacity family gained `extreme_peak`, `throttle_imminent`,
    # and — most importantly — `capacity_incident`, which is now the checkType for MOST
    # capacity alerts (multi-signal firings coalesce into it). Omitting them here would let
    # real-time capacity events back into the digest headline count, re-creating the
    # "Open tickets: 161" flood that f9581cf fixed. Kept in sync with tier2_check's
    # _CAPACITY_CHECKS.
    _EXCLUDE = (DIGEST_CHECK, "throttle", "pressure", "overage",
                "extreme_peak", "throttle_imminent", "capacity_incident")
    # An "open" ticket is one that (a) isn't excluded by check-type AND (b) is still actively
    # firing. currentlyActive=False means the underlying finding stopped firing — the row is only
    # still in status='active' because no human clicked Resolve. Rolling those into the daily
    # headline count produced the "Open tickets: 161 (160 warning)" flood on 2026-08-07 where 160
    # of them were legacy stale findings from before the healthy-capacity gate landed. They belong
    # in a "still open, not currently firing" backlog section, not the main count.
    _is_active_now = lambda v: v.get("currentlyActive") is not False
    open_tickets = [v for k, v in active.items()
                    if v.get("checkType") not in _EXCLUDE and _is_active_now(v)]
    stale_open = [v for k, v in active.items()
                  if v.get("checkType") not in _EXCLUDE and not _is_active_now(v)]

    # Fix A informational patterns (stable, known, non-capacity-linked attribution) — noted here for
    # awareness, no live ticket. Best-effort: a store without the query just contributes nothing.
    try:
        informational = list((alerts_store.get("query_informational", lambda: {})() or {}).values())
    except Exception:
        informational = []

    # Reconcile earlier digests: resolve the acknowledged ones, count the rest (they re-surface).
    unacked_prior = 0
    for k, row in prior_digests.items():
        if k == digest_key(date_str):
            continue  # a same-day re-run of today's own digest never counts against itself
        if _is_acknowledged(ack_store, row.get("chatId")):
            alerts_store["resolve"](k, now_iso)
        else:
            unacked_prior += 1

    def _compose(ack_url):
        return build_daily_summary(
            open_tickets=open_tickets, capacity=capacity or {}, coverage_gaps=coverage_gaps or [],
            date_str=date_str, app_url=app_url, unacked_prior=unacked_prior,
            informational=informational, ack_url=ack_url, events=events, health=health,
            stale_open=stale_open)

    # Pre-create the digest chat FIRST (its body is ack-independent) so the card's "Review &
    # acknowledge" action can deep-link to THAT chat — the app has no /alerts route, so the old
    # default 404'd. Fall back to the app root when there's no chat id.
    markdown, _, summary = _compose(None)
    chat_id = None
    if chat_writer:
        try:
            chat_id = chat_writer(markdown, f"Daily summary — {date_str}")
            if health is not None:
                health.record_delivery("chat", True)
        except Exception as exc:
            print(f"[daily] digest chat write failed ({type(exc).__name__}: {exc})")
            if health is not None:
                health.record_delivery("chat", False, f"{type(exc).__name__}: {exc}")

    ack_url = None
    if app_url:
        ack_url = (f"{app_url.rstrip('/')}/chat/{chat_id}" if chat_id
                   else f"{app_url.rstrip('/')}/")
    _, card, _ = _compose(ack_url)

    delivered = False
    if delivery_sinks:
        res = dispatch_outbound("daily_summary", {"attachments": [card]}, sinks=delivery_sinks)
        delivered = bool(res.get("delivered"))

    alerts_store["upsert"]({
        "incidentKey": digest_key(date_str), "status": "active", "severity": "info",
        "checkType": DIGEST_CHECK, "resource": "capacity", "chatId": chat_id,
        "firstAlertedAt": now_iso, "lastAlertedAt": now_iso, "runAt": now_iso,
        "currentlyActive": True, "delivered": delivered, "investigationSummary": summary})

    return {"delivered": delivered, "openTickets": len(open_tickets),
            "unackedPrior": unacked_prior, "digestKey": digest_key(date_str), "chatId": chat_id}

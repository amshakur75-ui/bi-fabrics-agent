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

from ..timefmt import parse_iso_utc

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
# Types whose `resource` is a USER LOGIN rather than an item/workspace. Both are
# per-user cost findings, so the top-users ranking must count both.
_SLOW_OP_TYPES = {"activity.slow-operation", "activity.user-baseline-deviation"}


def digest_key(date_str):
    """Stable incident key for a day's digest row (one per calendar day)."""
    return f"{DIGEST_CHECK}::{date_str}"


def _went_over_budget(capacity):
    """True when the day's own capacity reading shows it crossed 100% CU at some point.

    Used to stop the headline claiming an all-clear on a day whose incident has already
    auto-resolved -- tier2 clears a capacity incident after 60 quiet minutes, so by 18:00 a real
    morning spike leaves NO open row, and the digest had no other way to know it happened.
    """
    c = capacity or {}
    peak, throttle = c.get("peakCuPct"), c.get("throttleMinutes")
    try:
        if peak is not None and float(peak) >= 100:
            return True
        if throttle is not None and float(throttle) > 0:
            return True
    except (TypeError, ValueError):
        return False
    return False


def _sev_counts(tickets):
    # `critical` counts as warn-or-worse. Testing only for == "warn" meant that the moment
    # sweep_delivery started emitting "critical", every Critical finding would have been counted as
    # INFO -- a silent downgrade of the most severe thing in the digest, caused by fixing severity
    # elsewhere. These two have to move together.
    warn = sum(1 for t in tickets
               if (t.get("severity") or "").lower() in ("warn", "critical"))
    info = len(tickets) - warn
    return warn, info


def _ticket_line(t):
    emoji = {"critical": "🚨", "warn": "⚠️"}.get((t.get("severity") or "").lower(), "ℹ️")
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

    Fallback when no event-level data was passed in: rank by how many USER-KEYED taxonomy
    findings (``_SLOW_OP_TYPES`` — ``activity.slow-operation`` and
    ``activity.user-baseline-deviation``, the two whose ``resource`` is actually a user login;
    recurring-shape / long-running-cluster key by ITEM, not user, and must not be miscounted as a
    per-user ranking) each user has open today. This is real data
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


def _synthesis_lines(*, open_tickets, buckets, capacity, capacity_open, ranked_users,
                     users_source, carried_over, window_label, coverage_gaps):
    """Plain-language read of what this window actually meant, after the itemised list.

    Deliberately derived ONLY from what the card already showed -- no new inference, no severity
    the reader cannot see above -- so the summary can never assert something the list contradicts.
    """
    where = window_label or "this window"
    lines = []

    peak = (capacity or {}).get("peakCuPct")
    throttle = (capacity or {}).get("throttleMinutes")
    if peak is None:
        lines.append("**Capacity:** no CU reading was available for this window, so capacity "
                     "health is UNKNOWN rather than confirmed healthy.")
    elif _went_over_budget(capacity):
        lines.append(f"**Capacity:** peaked at {peak:.0f}% and spent "
                     f"{float(throttle or 0):.0f} min over 100% CU — the capacity was the "
                     "constraint at least once in this window.")
    else:
        lines.append(f"**Capacity:** peaked at {peak:.0f}%, never over budget — capacity headroom "
                     "was not the problem in this window.")

    n = len(open_tickets)
    if not n:
        lines.append(f"**New findings:** none in {where}.")
    else:
        biggest = max(buckets.items(), key=lambda kv: len(kv[1]))
        label = _SECTION_TITLES.get(biggest[0], biggest[0])
        lines.append(f"**New findings:** {n} in {where}, most of them {label.lower()} "
                     f"({len(biggest[1])}). Start there.")

    if ranked_users and users_source == "events":
        top = ranked_users[0]
        lines.append(f"**Heaviest activity:** {top.get('user')} "
                     f"({top.get('cuSeconds', 0):.0f} CPU-s of monitored telemetry — a CPU-time "
                     "proxy, not billed capacity CU).")

    if carried_over:
        lines.append(f"**Backlog:** {carried_over} older finding(s) are still open and were not "
                     "relisted above.")
    if coverage_gaps:
        lines.append(f"**Caveat:** {len(coverage_gaps)} coverage gap(s) mean parts of the estate "
                     "were not visible, so this is not a complete picture.")
    if not lines:
        lines.append(f"Nothing to report for {where}.")
    return lines


def build_daily_summary(*, open_tickets, capacity, coverage_gaps, date_str,
                        app_url="", ack_url=None, unacked_prior=0, informational=None,
                        events=None, health=None, stale_open=None, capacity_open=None,
                        window_label=None, carried_over=0):
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
    # Distinct from has_issues: the taxonomy count deliberately stays capacity-free (capacity has
    # its own real-time Teams cards, and counting it here re-creates the "Open tickets: 161" flood),
    # but "is anything wrong at all?" must include capacity or the headline lies. Dropping capacity
    # from both lists meant that on a day whose only problem was an open, still-firing capacity
    # incident, the digest read "No significant issues found" -- and the mitigating "Capacity
    # context" line was itself gated on has_issues, so that was dropped too. A digest that denies a
    # live incident is worse than no digest.
    capacity_open = list(capacity_open or [])
    anything_wrong = has_issues or bool(capacity_open)
    ranked_users, users_source = _top_users(open_tickets, events)

    # ---- markdown (the pre-created chat body; also a plain-text fallback) ----
    _win = f" · {window_label}" if window_label else ""
    md = [f"# 📋 Fabric capacity summary — {date_str}{_win}", ""]
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
    elif capacity_open:
        n = len(capacity_open)
        md.append(f"**{n} capacity incident{'s' if n != 1 else ''} open and still firing** — "
                  "see the capacity alerts for detail.")
    elif _went_over_budget(capacity):
        # The incident already auto-resolved (tier2 clears after 60 quiet minutes), so there is no
        # open ticket to list -- but the day still went over budget, and "no significant issues"
        # would be false. This is the case that produced a card reading "No significant issues
        # found" for a day with a 187% spike, with every contradicting number removed.
        md.append("**No open findings right now** — but the capacity went over 100% CU earlier "
                  "today; see the capacity context below.")
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

    # CARRIED OVER, counted not relisted. Without the count the scoping would look like findings had
    # disappeared; with a full relist the card is the same nine tickets every time and whatever
    # actually happened in this window drowns.
    if carried_over:
        md += ["", f"_{carried_over} finding(s) first detected before this window are still open "
                   "and not repeated here — see the notification center for the full backlog._"]

    # ---- the closing synthesis -------------------------------------------------------------
    # The sections above are a LIST. A list is not an answer: opening the chat from "Review &
    # acknowledge" gave a relist and stopped, leaving the reader to do the aggregation themselves.
    # This says what the window MEANT.
    md += ["", "## Summary", ""]
    md += [f"- {line}" for line in _synthesis_lines(
        open_tickets=open_tickets, buckets=buckets, capacity=capacity, capacity_open=capacity_open,
        ranked_users=ranked_users, users_source=users_source, carried_over=carried_over,
        window_label=window_label, coverage_gaps=coverage_gaps)]
    if informational:
        md += ["", "## Stable patterns (informational — no action needed)"]
        for t in informational[:_MAX_TICKET_LINES]:
            md.append(f"- {_ticket_line(t)}")
        if len(informational) > _MAX_TICKET_LINES:
            md.append(f"- …and {len(informational) - _MAX_TICKET_LINES} more")

    # UNGATED. The day's high-water CU is a fact about TODAY, not a function of whether a ticket
    # happens to still be open at 18:00. Gating it meant the one line that could contradict a false
    # all-clear was removed on exactly the days it was needed.
    if peak is not None or throttle is not None:
        bits = []
        if peak is not None:
            bits.append(f"{peak:.0f}% peak true CU%".replace("%%", "%"))
        if throttle is not None:
            bits.append(f"{throttle:.0f} min over 100% CU")
        md += ["", f"---\n_Capacity context: {', '.join(bits)} — see capacity alerts for detail._"]

    markdown = "\n".join(md)

    # ---- adaptive card (v1.2 for mobile Teams) ----
    header = f"📋 Fabric capacity summary — {date_str}{_win}"
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
    elif capacity_open:
        n = len(capacity_open)
        facts = [{"title": "Findings today",
                  "value": f"⚠️ {n} capacity incident{'s' if n != 1 else ''} open and firing"}]
    else:
        facts = [{"title": "Findings today",
                   "value": ("No open findings — capacity went over 100% CU earlier today"
                             if _went_over_budget(capacity)
                             else "No significant issues found ✅")}]
    if coverage_gaps:
        facts.append({"title": "Coverage gaps", "value": str(len(coverage_gaps))})
    if informational:
        facts.append({"title": "Stable patterns (info)", "value": str(len(informational))})
    # anything_wrong, matching the markdown path ten lines up. Left on has_issues, the
    # adaptive CARD showed "1 capacity incident open and firing" and then dropped the
    # "Capacity context" fact -- the one number (e.g. 187% peak) a reader needs -- while the
    # chat body included it. The test asserted only on the markdown, which is why the same
    # defect survived ten lines from the line that fixed it.
    # Same ungating as the markdown path -- the card is the surface most people actually read.
    if peak is not None or throttle is not None:
        bits = []
        if peak is not None:
            bits.append(f"{peak:.0f}%".replace("%%", "%"))
        if throttle is not None:
            bits.append(f"{throttle:.0f} min over 100% CU")
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


def _is_acknowledged(ack_store, handle):
    """True when a human has acked/resolved this ticket. ``handle`` is a chat id OR an incident key
    -- the ack store is keyed by both, because a chat-less ticket has no chat to key on."""
    if not (ack_store and handle):
        return False
    try:
        t = ack_store["get"](handle)
    except Exception:
        return False
    return bool(t) and (t.get("status") or "").lower() in ("acked", "resolved")


def run_daily_summary(*, alerts_store, ack_store=None, capacity=None, coverage_gaps=None,
                      delivery_sinks=None, chat_writer=None, app_url="", now_dt=None,
                      events=None, health=None, window_label=None, window_start=None):
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
                "extreme_peak", "throttle_imminent", "capacity_incident",
                # Informational capacity-STATUS signals. The notification centre already
                # excludes these ("a to-do list of real problems, not 'capacity is N% today'
                # repeated all day") but the digest still counted them in "Findings today" and
                # rendered them under "Other findings" — e.g. "sustained (capacity) — CU% has
                # sat in the 70-90% band". Same chatter, different surface; keep the two lists
                # in agreement.
                "sustained", "rate_change")
    # An "open" ticket is one that (a) isn't excluded by check-type AND (b) is still actively
    # firing. currentlyActive=False means the underlying finding stopped firing — the row is only
    # still in status='active' because no human clicked Resolve. Rolling those into the daily
    # headline count produced the "Open tickets: 161 (160 warning)" flood on 2026-08-07 where 160
    # of them were legacy stale findings from before the healthy-capacity gate landed. They belong
    # in a "still open, not currently firing" backlog section, not the main count.
    _is_active_now = lambda v: v.get("currentlyActive") is not False
    # RESOLVED BY A HUMAN COUNTS AS CLOSED. A Resolve in the notification center writes only to
    # ai_chatbot.alert_ack -- nothing propagates it back to audit_alerts -- and no sweep-family row
    # is ever marked currentlyActive=False (only the capacity family maintains that flag). So every
    # sweep ticket ever written stayed active forever: the badge in the app dropped a ticket the
    # moment it was resolved (its query DOES join alert_ack) while the 6pm card still counted and
    # listed it. "Findings today: 47" was a lifetime cumulative total including work already done,
    # which is the same "Open tickets: 161" shape the currentlyActive filter was added to fix --
    # that filter just never applied to the families that don't maintain the flag.
    def _resolved_by_human(v):
        return _is_acknowledged(ack_store, v.get("chatId") or v.get("incidentKey"))

    # SCOPED TO THE WINDOW. `active` is every open row in the shared table, so without this the
    # digest relisted findings first seen days ago under a heading that says "Findings today" --
    # the same nine tickets every card, drowning whatever actually happened in this window. A
    # finding that is still open but was first detected BEFORE the window is summarised as a count
    # instead of repeated in full. Unparseable/absent timestamps are INCLUDED (the safe direction:
    # a listed finding can be checked, a hidden one cannot).
    _all_open = [v for k, v in active.items()
                 if v.get("checkType") not in _EXCLUDE and _is_active_now(v)
                 and not _resolved_by_human(v)]

    def _first_seen_in_window(v):
        if window_start is None:
            return True
        ts = parse_iso_utc(v.get("firstAlertedAt") or v.get("runAt"))
        return True if ts is None else ts >= window_start

    open_tickets = [v for v in _all_open if _first_seen_in_window(v)]
    carried_over = [v for v in _all_open if not _first_seen_in_window(v)]
    # Kept OUT of open_tickets/stale_open (and so out of the taxonomy count) but surfaced in the
    # headline by build_daily_summary — see the note beside `anything_wrong` there.
    _CAPACITY_FAMILY = ("throttle", "pressure", "overage", "extreme_peak", "throttle_imminent",
                        "capacity_incident")
    capacity_open = [v for k, v in active.items()
                     if v.get("checkType") in _CAPACITY_FAMILY and _is_active_now(v)
                     and not _resolved_by_human(v)]
    stale_open = [v for k, v in active.items()
                  if v.get("checkType") not in _EXCLUDE and not _is_active_now(v)
                  and not _resolved_by_human(v)]

    # Fix A informational patterns (stable, known, non-capacity-linked attribution) — noted here for
    # awareness, no live ticket. Best-effort: a store without the query just contributes nothing.
    try:
        informational = list((alerts_store.get("query_informational", lambda: {})() or {}).values())
    except Exception:
        informational = []

    # Reconcile earlier digests. A digest is SUPERSEDED, not resolved: yesterday's summary has no
    # outstanding action once today's exists, and there was no surface that could ever clear one --
    # no alert_ticket row is written for it, `daily_summary` is filtered out of the notification
    # center's ACTIONABLE set, and the /ack route has no caller. So the counter only ever grew, one
    # per day forever (6 by the time it was noticed), and the banner told the reader to go
    # acknowledge things in a place where they do not appear. Doubling the schedule to twice a day
    # would have doubled the rate.
    #
    # Any digest older than today is therefore auto-resolved. An EXPLICIT acknowledgement still
    # resolves one immediately (checked on either handle, because a chat-less digest has only its
    # incident key), and same-day digests are left alone so the noon card is not retired by the
    # 18:00 run before anyone has read it.
    unacked_prior = 0
    for k, row in prior_digests.items():
        if k == digest_key(date_str):
            continue  # a same-day re-run of today's own digest never counts against itself
        if _is_acknowledged(ack_store, row.get("chatId") or row.get("incidentKey") or k):
            alerts_store["resolve"](k, now_iso)
            continue
        _seen = parse_iso_utc(row.get("firstAlertedAt") or row.get("runAt"))
        if _seen is None or _seen.date() < now_dt.date():
            alerts_store["resolve"](k, now_iso)   # superseded by a later day's digest
            continue
        unacked_prior += 1

    def _compose(ack_url):
        return build_daily_summary(
            open_tickets=open_tickets, capacity=capacity or {}, coverage_gaps=coverage_gaps or [],
            date_str=date_str, app_url=app_url, unacked_prior=unacked_prior,
            informational=informational, ack_url=ack_url, events=events, health=health,
            stale_open=stale_open, capacity_open=capacity_open, window_label=window_label,
            carried_over=len(carried_over))

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

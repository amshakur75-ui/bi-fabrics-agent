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
"""
from datetime import datetime, timezone

DIGEST_CHECK = "daily_summary"
_SYSTEM_USER_ID = "fabric-audit-agent"
_MAX_TICKET_LINES = 8  # cap the per-ticket list so the card stays mobile-friendly


def digest_key(date_str):
    """Stable incident key for a day's digest row (one per calendar day)."""
    return f"{DIGEST_CHECK}::{date_str}"


def _sev_counts(open_tickets):
    warn = sum(1 for t in open_tickets if (t.get("severity") or "").lower() == "warn")
    info = len(open_tickets) - warn
    return warn, info


def _ticket_line(t):
    emoji = "⚠️" if (t.get("severity") or "").lower() == "warn" else "ℹ️"
    check = t.get("checkType") or "incident"
    res = t.get("resource") or "capacity"
    reason = t.get("materialityReason") or t.get("investigationSummary") or ""
    active = "" if t.get("currentlyActive") is not False else " (currently inactive)"
    tail = f" — {reason}" if reason else ""
    return f"{emoji} {check} ({res}){active}{tail}"


def build_daily_summary(*, open_tickets, capacity, coverage_gaps, date_str,
                        app_url="", ack_url=None, unacked_prior=0, informational=None):
    """Build the digest as ``(markdown, card, summary)``. Pure — no I/O.

    ``open_tickets``: active ``audit_alerts`` rows (digest AND capacity rows already excluded —
    capacity has its own real-time alert + auto-resolve lifecycle, so it does not belong here).
    ``capacity``: ``{"peakCuPct": float|None, "throttleMinutes": float|None}`` day high-water, or {}.
    ``coverage_gaps``: short strings (e.g. silent-failure / coverage-gap notes) to list.
    ``unacked_prior``: number of earlier digests not yet acknowledged (drives the banner).
    ``informational``: stable, known attribution patterns (Fix A) — noted for awareness, no action.
    ``ack_url``: where the Acknowledge action points (defaults to the app root ``{app_url}/`` — the
    app has no ``/alerts`` route, so callers should pass the digest chat deep-link when they have it).
    """
    open_tickets = list(open_tickets or [])
    coverage_gaps = list(coverage_gaps or [])
    informational = list(informational or [])
    capacity = capacity or {}
    warn, info = _sev_counts(open_tickets)
    peak = capacity.get("peakCuPct")
    throttle = capacity.get("throttleMinutes")

    # ---- markdown (the pre-created chat body; also a plain-text fallback) ----
    md = [f"# 📋 Daily Fabric capacity summary — {date_str}", ""]
    if unacked_prior:
        s = "summary" if unacked_prior == 1 else "summaries"
        md.append(f"> ⚠️ {unacked_prior} earlier daily {s} still awaiting acknowledgement.\n")
    if open_tickets:
        md.append(f"**Open tickets:** {len(open_tickets)} ({warn} warning, {info} info)")
    else:
        md.append("**Open tickets:** none — all clear. ✅")
    if peak is not None:
        md.append(f"**Peak true CU%% today:** {peak:.0f}%".replace("%%", "%"))
    if throttle is not None:
        md.append(f"**Throttle minutes today:** {throttle:.0f}")
    if coverage_gaps:
        md.append(f"**Coverage gaps:** {len(coverage_gaps)}")
    if informational:
        md.append(f"**Stable patterns noted (informational — no action):** {len(informational)}")
    if open_tickets:
        md += ["", "## Open tickets"]
        for t in open_tickets[:_MAX_TICKET_LINES]:
            md.append(f"- {_ticket_line(t)}")
        if len(open_tickets) > _MAX_TICKET_LINES:
            md.append(f"- …and {len(open_tickets) - _MAX_TICKET_LINES} more")
    if coverage_gaps:
        md += ["", "## Coverage gaps"]
        md += [f"- {g}" for g in coverage_gaps]
    if informational:
        md += ["", "## Stable patterns (informational — no action needed)"]
        for t in informational[:_MAX_TICKET_LINES]:
            md.append(f"- {_ticket_line(t)}")
        if len(informational) > _MAX_TICKET_LINES:
            md.append(f"- …and {len(informational) - _MAX_TICKET_LINES} more")
    markdown = "\n".join(md)

    # ---- adaptive card (v1.2 for mobile Teams) ----
    header = f"📋 Daily Fabric capacity summary — {date_str}"
    body = [{"type": "TextBlock", "text": header, "weight": "Bolder",
             "size": "Medium", "wrap": True}]
    if unacked_prior:
        s = "summary" if unacked_prior == 1 else "summaries"
        body.append({"type": "TextBlock", "wrap": True, "size": "Small", "weight": "Bolder",
                     "color": "Warning",
                     "text": f"⚠️ {unacked_prior} earlier daily {s} still awaiting acknowledgement."})
    facts = [{"title": "Open tickets",
              "value": (f"{len(open_tickets)} ({warn} warning, {info} info)"
                        if open_tickets else "none — all clear ✅")}]
    if peak is not None:
        facts.append({"title": "Peak true CU% today", "value": f"{peak:.0f}%"})
    if throttle is not None:
        facts.append({"title": "Throttle minutes", "value": f"{throttle:.0f}"})
    if coverage_gaps:
        facts.append({"title": "Coverage gaps", "value": str(len(coverage_gaps))})
    if informational:
        facts.append({"title": "Stable patterns (info)", "value": str(len(informational))})
    body.append({"type": "FactSet", "facts": facts})
    if open_tickets:
        listing = "\n".join(f"- {_ticket_line(t)}" for t in open_tickets[:_MAX_TICKET_LINES])
        if len(open_tickets) > _MAX_TICKET_LINES:
            listing += f"\n- …and {len(open_tickets) - _MAX_TICKET_LINES} more"
        body.append({"type": "TextBlock", "text": listing, "wrap": True})

    content = {"type": "AdaptiveCard",
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
               "version": "1.2", "body": body}
    target = ack_url or (f"{app_url.rstrip('/')}/" if app_url else None)
    if target:
        content["actions"] = [{"type": "Action.OpenUrl",
                               "title": "Review & acknowledge", "url": target}]
    card = {"contentType": "application/vnd.microsoft.card.adaptive", "content": content}

    n_open = len(open_tickets)
    summary = (f"Daily summary {date_str}: {n_open} open ticket(s)"
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
                      delivery_sinks=None, chat_writer=None, app_url="", now_dt=None):
    """Compose + deliver today's digest, reconcile prior digests, and record today's.

    Returns ``{"delivered", "openTickets", "unackedPrior", "digestKey", "chatId"}``. All I/O ports
    are injected so this is unit-testable with fakes (no Lakebase / Delta / webhook needed).
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
    _EXCLUDE = (DIGEST_CHECK, "throttle", "pressure", "overage")
    open_tickets = [v for k, v in active.items() if v.get("checkType") not in _EXCLUDE]

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
            informational=informational, ack_url=ack_url)

    # Pre-create the digest chat FIRST (its body is ack-independent) so the card's "Review &
    # acknowledge" action can deep-link to THAT chat — the app has no /alerts route, so the old
    # default 404'd. Fall back to the app root when there's no chat id.
    markdown, _, summary = _compose(None)
    chat_id = None
    if chat_writer:
        try:
            chat_id = chat_writer(markdown, f"Daily summary — {date_str}")
        except Exception as exc:
            print(f"[daily] digest chat write failed ({type(exc).__name__}: {exc})")

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

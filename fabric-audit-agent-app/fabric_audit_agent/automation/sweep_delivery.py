"""Deliver the hourly sweep's NEW material findings to Teams (Step 6a).

The sweep audits the FULL estate (model / refresh-contention / cost / blast-radius + the richer
LLM-reasoned findings); the Tier-2 5-minute job owns the real-time capacity gates (throttle,
pressure, concentration, overage). To avoid double-alerting, this delivers only the finding families
Tier-2 does NOT cover, and dedups against the SHARED ``audit_alerts`` store so a finding is alerted
ONCE — never repeated across sweeps, and never on top of a Tier-2 alert for the same thing.

Pure orchestration over injected ports (``alerts_store`` / ``delivery_sinks`` / ``chat_writer``).

NOTE ON "delivered": this module sends NOTHING outward. Sweep findings are TICKETED — an
``audit_alerts`` row + an ``alert_ticket`` row + a pre-created chat — so they appear in the app's
notification center and are investigable, but no Adaptive Card is posted (Teams is reserved for
Tier-2 real-time capacity emergencies; pushing every sweep finding there was the noise source
found 2026-08-09). The ``delivered`` result key is kept for backwards compatibility with existing
callers/tests and means "ticketed", not "sent". ``delivery_sinks`` is likewise accepted and
unused.
"""
import re
import urllib.parse
from datetime import datetime, timezone

# Cut points that separate the "ask" from trailing detail (mirrors the app's deriveShortTitle).
_TITLE_CUT = re.compile(r"\s[—–-]\s|[:\n]|(?<=\w)\.\s", re.I)
# The dominant concentration-alert phrasing — compress "<user>@dom is driving ~35.4% of capacity …"
# to a glanceable "<user> — 35.4% of capacity" instead of re-truncating the whole long sentence.
_CONCENTRATION = re.compile(r"^\s*(?P<who>\S+?)(?:@[\w.]+)?\s+is driving\s+~?(?P<pct>[\d.]+%)", re.I)


def short_title(text, max_words=8, max_chars=60):
    """A glanceable short chat title from a long finding sentence — strip markdown, cut at the first
    clause boundary, cap to ~max_words / max_chars. Keeps alert/sweep chat names readable in the
    sidebar instead of dumping the whole finding text."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = re.sub(r"^[#>*_`\s\"'\-–—]+", "", s).strip()
    if not s:
        return "Finding"
    conc = _CONCENTRATION.match(s)
    if conc:
        return f"{conc.group('who')} — {conc.group('pct')} of capacity"
    m = _TITLE_CUT.search(s)
    if m and m.start() > 12:
        s = s[:m.start()].strip()
    truncated = False
    words = s.split(" ")
    if len(words) > max_words:
        s = " ".join(words[:max_words])
        truncated = True
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(" ", 1)[0]
        truncated = True
    s = re.sub(r"[\s,;:.\-–—]+$", "", s).strip()
    if not s:
        return "Finding"
    return (s[0].upper() + s[1:]) + ("…" if truncated else "")

_LEVEL_RANK = {"Info": 0, "Warning": 1, "Critical": 2}
# Finding families Tier-2 already alerts on in real time — the sweep must not repeat them.
_TIER2_OWNED_PREFIXES = ("capacity.concentration", "capacity.throttle", "capacity.pressure",
                         "capacity.overage")


def _tier2_owned(key):
    return any((key or "").startswith(p) for p in _TIER2_OWNED_PREFIXES)


def _investigate_query(what, when=None):
    """The prompt auto-sent when the alert deep-link is opened. ``when`` (the fire time) anchors the
    agent to the moment the finding was detected — otherwise it investigates the live 'now' (often
    hours later, when the event has passed) and wrongly concludes nothing is wrong / the named user
    wasn't involved."""
    anchor = ""
    if when:
        anchor = (f" This was detected around {when} — investigate the capacity and activity IN THAT "
                  "TIME WINDOW as your primary anchor (use it as a direction, ±30 min), not the current "
                  "moment; the live 'now' may look clean because the event has already passed. If that "
                  "±30-min window does NOT corroborate the named user/finding (they don't appear among "
                  "the top actors there, or their activity is trivial), do NOT just widen the same "
                  "window — PIVOT: search the named user's own activity broadly (last 7-30 days) to "
                  "find when THEY were actually most active/anomalous, and investigate THAT time "
                  "instead.")
    return (f"Investigate this finding and give me the root cause and the specific fix: {what}.{anchor} "
            "Pull the recent capacity + activity and name what's driving it. Distinguish true CU% "
            "(ground truth) from the monitored-activity proxy — do not present the proxy as capacity "
            "consumption.")


# Compound keys whose first segment ("capacity") is NOT a UI-actionable checkType — map them to the
# specific type the app's notification center recognises.
_FAMILY_MAP = {"capacity.user-ranking": "concentration"}


def _family(key):
    """The finding family (checkType) from its key: ``model.bidirectional`` -> ``model``;
    ``capacity.user-ranking`` -> ``concentration`` (an actionable notification-center type)."""
    k = str(key or "")
    for prefix, check_type in _FAMILY_MAP.items():
        if k.startswith(prefix):
            return check_type
    return (k.split(".")[0] or "sweep")


def deliver_new_findings(findings, *, alerts_store, delivery_sinks, app_url="",
                         chat_writer=None, ticket_writer=None, min_level="Warning", now_iso=None,
                         health=None):
    """Deliver NEW material sweep findings; dedup via the shared ``audit_alerts`` store.

    Returns ``{"delivered":[keys], "skipped_dup", "skipped_tier2", "skipped_minor"}``.
    A finding is delivered iff: not a Tier-2-owned family, level >= ``min_level``, and its key is not
    already an active incident. On delivery it's upserted active (so the next sweep / Tier-2 dedups)
    AND — via ``ticket_writer`` — an ``alert_ticket`` row is written so the estate-wide finding SHOWS
    IN THE APP NOTIFICATION CENTER, not just Teams (checkType = the finding family, e.g. ``model``).

    ``health``: optional ``automation.health.HealthReport`` — the chat-write / ticket-write
    failures below are already logged (WARN prints); this additionally records them so a degraded
    delivery path surfaces in the digest banner instead of only in job logs.
    """
    now_iso = now_iso or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    min_rank = _LEVEL_RANK.get(min_level, 1)
    active = alerts_store["query_active"]()
    out = {"delivered": [], "skipped_dup": 0, "skipped_tier2": 0, "skipped_minor": 0}

    for f in findings or []:
        key = f.get("key")
        if not key:
            continue
        level = (f.get("score") or {}).get("level")
        if _tier2_owned(key):
            out["skipped_tier2"] += 1
            continue
        if _LEVEL_RANK.get(level, 0) < min_rank:
            out["skipped_minor"] += 1
            continue
        if key in active:
            out["skipped_dup"] += 1
            continue

        what = f.get("what") or key
        title = short_title(what)   # short, glanceable chat name (not the whole finding sentence)
        markdown = f.get("narrative") or f.get("recommendation") or what

        chat_id = None
        if chat_writer:
            try:
                chat_id = chat_writer(markdown, title)
                if health is not None:
                    health.record_delivery("chat", True)
            except Exception as exc:  # a chat-write failure must not drop the alert or the link
                print(f"[sweep] WARN: alert chat write failed ({type(exc).__name__}: {exc}) — "
                      f"continuing with chat_id=None (ticket is still written, deep-link degrades "
                      f"to a root ?query= auto-investigation link)")
                if health is not None:
                    health.record_delivery("chat", False, f"{type(exc).__name__}: {exc}")

        chat_url = None
        if app_url:
            base = (f"{app_url.rstrip('/')}/chat/{chat_id}" if chat_id
                    else f"{app_url.rstrip('/')}/")
            when = f.get("when") or now_iso   # anchor the investigation to when the finding fired
            chat_url = base + "?query=" + urllib.parse.quote(_investigate_query(what, when=when))

        family = _family(key)
        sev = "warn" if _LEVEL_RANK.get(level, 0) >= 1 else "info"
        # NOTHING IS SENT FROM HERE. The Adaptive Card build and the dispatch_outbound import
        # used to live at this point; both were dead (the card was assembled and dropped on the
        # floor), which made the module docstring's "every send routes through dispatch_outbound"
        # false and implied a Teams delivery that never happened. Removed rather than left as
        # decoration.
        # Teams is reserved for TIER-2 real-time capacity emergencies (throttle/pressure/overage).
        # Every sweep-family finding (model/report/refresh/security/pipeline/cost/blast_radius/
        # pattern/activity/query/xmla/...) goes to the app notification center ONLY — pushing every
        # sweep finding to Teams was the noise source found 2026-08-09 (Evelien 6.7s / 107 CPU-s,
        # Madhan 18.2s / 207 CPU-s, Jessica 173s / 148 CPU-s — none capacity emergencies). The
        # audit_alerts row + ticket_writer + chat_writer paths below still run, so the finding is
        # tracked, ticketed, and investigable — just not on your phone.
        alerts_store["upsert"]({
            "incidentKey": key, "status": "active", "severity": sev, "checkType": family,
            "resource": f.get("where") or key, "chatId": chat_id,
            "metric": float(_LEVEL_RANK.get(level, 0)), "firstAlertedAt": now_iso,
            "lastAlertedAt": now_iso, "lastRemindedAt": None, "resolvedAt": None,
            "escalationCount": 0, "materialityReason": f"sweep finding ({level})",
            "investigationSummary": what, "delivered": False, "runAt": now_iso,
            "currentlyActive": True,
        })
        # Write the app-readable ticket row so this estate-wide finding appears in the notification
        # center (not just Teams). ALWAYS written (even when chat_id is None, i.e. chat creation
        # failed above) — the ticket is keyed by the stable incidentKey, not chat_id, so a finding
        # is never silently dropped from the notification center just because the chat write failed
        # (Part 7). Failure-isolated: metadata must never drop the delivery.
        if ticket_writer:
            try:
                ticket_writer(chat_id, {
                    "incidentKey": key, "checkType": family, "severity": sev,
                    "resource": f.get("where") or key, "workspace": None,
                    "detail": (f.get("recommendation") or what)[:500],
                    "firstDetected": now_iso, "currentlyActive": True})
                if health is not None:
                    health.record_delivery("ticket", True)
            except Exception as exc:
                print(f"[sweep] ticket metadata write failed ({type(exc).__name__}: {exc})")
                if health is not None:
                    health.record_delivery("ticket", False, f"{type(exc).__name__}: {exc}")
        out["delivered"].append(key)

    return out

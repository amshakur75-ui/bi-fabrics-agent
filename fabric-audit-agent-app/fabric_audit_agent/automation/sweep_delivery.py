"""Deliver the hourly sweep's NEW material findings to Teams (Step 6a).

The sweep audits the FULL estate (model / refresh-contention / cost / blast-radius + the richer
LLM-reasoned findings); the Tier-2 5-minute job owns the real-time capacity gates (throttle,
pressure, concentration, overage). To avoid double-alerting, this delivers only the finding families
Tier-2 does NOT cover, and dedups against the SHARED ``audit_alerts`` store so a finding is alerted
ONCE — never repeated across sweeps, and never on top of a Tier-2 alert for the same thing.

Pure orchestration over injected ports (``alerts_store`` / ``delivery_sinks`` / ``chat_writer``);
every send routes through ``outbound.dispatch_outbound`` (the egress chokepoint).
"""
import re
import urllib.parse
from datetime import datetime, timezone

# Cut points that separate the "ask" from trailing detail (mirrors the app's deriveShortTitle).
_TITLE_CUT = re.compile(r"\s[—–-]\s|[:\n]|(?<=\w)\.\s", re.I)


def short_title(text, max_words=8, max_chars=60):
    """A glanceable short chat title from a long finding sentence — strip markdown, cut at the first
    clause boundary, cap to ~max_words / max_chars. Keeps alert/sweep chat names readable in the
    sidebar instead of dumping the whole finding text."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    s = re.sub(r"^[#>*_`\s\"'\-–—]+", "", s).strip()
    if not s:
        return "Finding"
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


def _investigate_query(what):
    return (f"Investigate this finding and give me the root cause and the specific fix: {what}. "
            "Pull the recent capacity + activity and name what's driving it. Distinguish true CU% "
            "(ground truth) from the monitored-activity proxy — do not present the proxy as capacity "
            "consumption.")


def _family(key):
    """The finding family (checkType) from its key: ``model.bidirectional`` -> ``model``."""
    return (str(key or "").split(".")[0] or "sweep")


def deliver_new_findings(findings, *, alerts_store, delivery_sinks, app_url="",
                         chat_writer=None, ticket_writer=None, min_level="Warning", now_iso=None):
    """Deliver NEW material sweep findings; dedup via the shared ``audit_alerts`` store.

    Returns ``{"delivered":[keys], "skipped_dup":n, "skipped_tier2":n, "skipped_minor":n}``.
    A finding is delivered iff: not a Tier-2-owned family, level >= ``min_level``, and its key is not
    already an active incident. On delivery it's upserted active (so the next sweep / Tier-2 dedups)
    AND — via ``ticket_writer`` — an ``alert_ticket`` row is written so the estate-wide finding SHOWS
    IN THE APP NOTIFICATION CENTER, not just Teams (checkType = the finding family, e.g. ``model``).
    """
    from ..outbound import dispatch_outbound
    from ..adapters.delivery_webhook import build_card

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
            except Exception as exc:  # a chat-write failure must not drop the alert or the link
                print(f"[sweep] alert chat write failed ({type(exc).__name__}: {exc})")

        chat_url = None
        if app_url:
            base = (f"{app_url.rstrip('/')}/chat/{chat_id}" if chat_id
                    else f"{app_url.rstrip('/')}/")
            chat_url = base + "?query=" + urllib.parse.quote(_investigate_query(what))

        family = _family(key)
        sev = "warn" if _LEVEL_RANK.get(level, 0) >= 1 else "info"
        facts = [(n, v) for n, v in (("Severity", level), ("Where", f.get("where")),
                                     ("Finding", key)) if v]
        card = build_card("new", title=title, severity=sev, facts=facts, summary=what,
                          chat_url=chat_url)
        res = dispatch_outbound("tier2_alert", {"attachments": [card]}, sinks=delivery_sinks)
        alerts_store["upsert"]({
            "incidentKey": key, "status": "active", "severity": sev, "checkType": family,
            "resource": f.get("where") or key, "chatId": chat_id,
            "metric": float(_LEVEL_RANK.get(level, 0)), "firstAlertedAt": now_iso,
            "lastAlertedAt": now_iso, "lastRemindedAt": None, "resolvedAt": None,
            "escalationCount": 0, "materialityReason": f"sweep finding ({level})",
            "investigationSummary": what, "delivered": bool(res.get("delivered")), "runAt": now_iso,
            "currentlyActive": True,
        })
        # Write the app-readable ticket row so this estate-wide finding appears in the notification
        # center (not just Teams). Failure-isolated: metadata must never drop the delivery.
        if ticket_writer and chat_id:
            try:
                ticket_writer(chat_id, {
                    "incidentKey": key, "checkType": family, "severity": sev,
                    "resource": f.get("where") or key, "workspace": None,
                    "detail": (f.get("recommendation") or what)[:500],
                    "firstDetected": now_iso, "currentlyActive": True})
            except Exception as exc:
                print(f"[sweep] ticket metadata write failed ({type(exc).__name__}: {exc})")
        out["delivered"].append(key)

    return out

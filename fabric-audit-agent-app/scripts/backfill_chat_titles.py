"""One-time backfill: give EXISTING chats short, glanceable titles.

Chat titles are only generated at CREATE time, so chats made before the short-title fix keep long
raw titles. This re-derives a short title via ``sweep_delivery.short_title`` and updates
``ai_chatbot."Chat".title`` — but only where that's a genuine improvement:

  * USER chats (owned by a real email) — derive from the first USER message (the clean prompt).
  * ALERT chats (owned by ``fabric-audit-agent``) — compress the EXISTING Python-generated title,
    NOT the first message (the first message is the markdown alert body, which is worse). Only the
    ones whose current title is genuinely long (> ``_LONG`` chars) are touched, so the already-short
    structured titles ("CU climbing fast: 33.8% → 71.4%") are left alone.

A row is skipped unless the new title is strictly shorter than the current one — the backfill never
makes a title worse.

DRY-RUN by default — prints what it WOULD change. Pass ``--apply`` to write. Needs a live Lakebase
credential (run after ``databricks auth login --profile fabric-test``):

    python scripts/backfill_chat_titles.py            # preview
    python scripts/backfill_chat_titles.py --apply     # write
"""
import json
import os
import sys

try:  # titles carry em-dashes / ellipses; force UTF-8 so the preview prints on a Windows cp1252 console
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import re  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fabric_audit_agent.automation.sweep_delivery import short_title  # noqa: E402

_ENDPOINT = "projects/fabrics-audit-agent-memory/branches/production/endpoints/primary"
_HOST = os.environ.get("FABRIC_LAKEBASE_HOST",
                       "ep-shy-bird-e1gcy0mq.database.eastus2.azuredatabricks.net")
_USER = os.environ.get("FABRIC_LAKEBASE_USER", "")
_PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "fabric-test")
_SYSTEM_USER = "fabric-audit-agent"   # owner of alert chats (SYSTEM_USER_ID in chat_store_lakebase)
_LONG = 55                             # a title longer than this is too long to glance in the sidebar

_CONCENTRATION = re.compile(r"^(?P<who>\S+?)(?:@[\w.]+)?\s+is driving\s+~?(?P<pct>[\d.]+%)", re.I)
_RAW_FLOAT = re.compile(r"\d\.\d{4,}")          # unrounded float, e.g. "at 38.7190941704" — a raw dump
_AT_FLOAT_TAIL = re.compile(r"\s+at\s+[\d.]+\s*%?\s*$", re.I)


def _first_text(parts):
    """Extract the first text fragment from a Message.parts JSON value."""
    try:
        arr = parts if isinstance(parts, list) else json.loads(parts)
    except (TypeError, ValueError):
        return ""
    for p in arr or []:
        if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
            return p["text"]
    return ""


def _looks_bad(title):
    """A title worth fixing: too long, a markdown/emoji-prefixed assistant-body dump, or a raw float."""
    return (len(title) > _LONG or title[:1] in "*#>⚠" or bool(_RAW_FLOAT.search(title)))


def _compress_alert(title):
    """Compress an ALERT chat's (already-structured) title. Unlike short_title, this keeps a
    ``Label: value`` intact (no ':' clause-cut) — it only pulls out the concentration pattern and
    drops a trailing raw-float ``at 38.71…`` tail, so "Concentration: Ent-Reporting-Sales at 94.4…"
    stays "Concentration: Ent-Reporting-Sales" rather than collapsing to just "Concentration"."""
    s = re.sub(r"^[#>*_`\s\"'\-–—⚠]+", "", title).strip()
    m = _CONCENTRATION.match(s)
    if m:
        return f"{m.group('who')} — {m.group('pct')} of capacity"
    s = _AT_FLOAT_TAIL.sub("", s).strip()
    if len(s) > 52:
        s = s[:52].rsplit(" ", 1)[0].rstrip(" ,;:.-–—") + "…"
    return s


def main(apply):
    import psycopg2
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=_PROFILE)
    cred = w.postgres.generate_database_credential(_ENDPOINT)
    tok = getattr(cred, "token", None) or cred["token"]
    conn = psycopg2.connect(host=_HOST, port=5432, dbname="databricks_postgres",
                            user=_USER, password=tok, sslmode="require")
    cur = conn.cursor()
    cur.execute(
        'SELECT c.id, c.title, c."userId", m.parts FROM ai_chatbot."Chat" c '
        'LEFT JOIN LATERAL (SELECT parts FROM ai_chatbot."Message" m2 '
        '  WHERE m2."chatId" = c.id AND m2.role = \'user\' ORDER BY m2."createdAt" ASC LIMIT 1) m ON true')
    rows = cur.fetchall()
    changed = 0
    for chat_id, title, user_id, parts in rows:
        title = title or ""
        if not _looks_bad(title):
            continue   # already short + clean — leave good titles alone
        if user_id == _SYSTEM_USER:
            # Alert chat: compress the (structured) TITLE itself — the first message is the markdown
            # alert body, which is worse. Keeps "Label: value", pulls out the concentration pattern.
            new = _compress_alert(title)
        else:
            # User chat: derive from the user's own first message (the clean prompt).
            new = short_title(_first_text(parts))
        # Never make a title worse: require a real, strictly-shorter replacement.
        if not new or new == "Finding" or new == title or len(new) >= len(title):
            continue
        changed += 1
        print(f"  {chat_id}  {repr(title)[:52]:54} -> {repr(new)}")
        if apply:
            cur.execute('UPDATE ai_chatbot."Chat" SET title = %s WHERE id = %s', (new, chat_id))
    if apply:
        conn.commit()
    conn.close()
    print(f"\n{'UPDATED' if apply else 'WOULD UPDATE'} {changed} of {len(rows)} chat titles"
          f"{'' if apply else '  (re-run with --apply to write)'}")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)

"""One-time backfill: give EXISTING chats short, glanceable titles.

Chat titles are only generated at CREATE time, so chats made before the short-title fix keep their
raw first-sentence title, and Python-created alert/sweep chats kept the full finding text. This
re-derives a short title from each chat's FIRST message (``fabric_audit_agent.automation.sweep_delivery.short_title``)
and updates ``ai_chatbot."Chat".title``.

DRY-RUN by default — prints what it WOULD change. Pass ``--apply`` to write. Needs a live Lakebase
credential (run after ``databricks auth login --profile fabric-test``):

    python scripts/backfill_chat_titles.py            # preview
    python scripts/backfill_chat_titles.py --apply     # write

Only rows whose current title differs from the derived one are touched; short titles already in good
shape are left alone.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fabric_audit_agent.automation.sweep_delivery import short_title  # noqa: E402

_ENDPOINT = "projects/fabrics-audit-agent-memory/branches/production/endpoints/primary"
_HOST = os.environ.get("FABRIC_LAKEBASE_HOST",
                       "ep-shy-bird-e1gcy0mq.database.eastus2.azuredatabricks.net")
_USER = os.environ.get("FABRIC_LAKEBASE_USER", "abdishakur.mohamed@newellco.com")
_PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "fabric-test")


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
        'SELECT c.id, c.title, m.parts FROM ai_chatbot."Chat" c '
        'JOIN LATERAL (SELECT parts FROM ai_chatbot."Message" m2 '
        '  WHERE m2."chatId" = c.id ORDER BY m2."createdAt" ASC LIMIT 1) m ON true')
    rows = cur.fetchall()
    changed = 0
    for chat_id, title, parts in rows:
        new = short_title(_first_text(parts))
        if new and new != (title or "") and new != "Finding":
            changed += 1
            print(f"  {chat_id}  {repr(title)[:50]:52} -> {repr(new)}")
            if apply:
                cur.execute('UPDATE ai_chatbot."Chat" SET title = %s WHERE id = %s', (new, chat_id))
    if apply:
        conn.commit()
    conn.close()
    print(f"\n{'UPDATED' if apply else 'WOULD UPDATE'} {changed} of {len(rows)} chat titles"
          f"{'' if apply else '  (re-run with --apply to write)'}")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)

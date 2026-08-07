"""html_utils — shared HTML/export string utilities.

Direct port of the plugin's ``html-utils.ts``:
  - ``esc``            5-character HTML escape (XSS-safe interpolation)
  - ``file_timestamp`` filesystem-safe ISO timestamp for artifact names

Centralised so both the HTML report builder and any future export surface share
one escaping definition (the DED-001 audit finding that motivated the TS file).
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["esc", "file_timestamp"]


def esc(s: object) -> str:
    """Escape a value for safe insertion into HTML text or attribute values.

    Covers the five characters that can cause XSS when embedded in HTML, in the
    exact order the TS ``esc()`` applies them (``&`` FIRST so the entity
    ampersands introduced by the later replacements are not double-escaped)::

        &  ->  &amp;
        <  ->  &lt;
        >  ->  &gt;
        "  ->  &quot;
        '  ->  &#39;   (covers single-quoted attribute values)

    The TS signature is ``esc(s: string)``; callers always ``String(...)`` first.
    We coerce defensively (``None`` -> ``""``) so non-string cell values can be
    passed through without a separate ``str()`` at every call site.
    """
    text = "" if s is None else str(s)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def file_timestamp() -> str:
    """Return a filesystem-safe ISO timestamp, e.g. ``2026-06-25T14-30-00``.

    Mirrors the TS ``new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)``:
    UTC, ``:`` and ``.`` replaced with ``-``, truncated to second precision.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

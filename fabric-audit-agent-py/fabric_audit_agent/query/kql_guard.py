"""KQL construction guards. Adapted from microsoft/fabric-rti-mcp + microsoft/mcp (MIT). Pure stdlib.
NOTE: handles standard single/double-quoted KQL string literals with backslash escaping;
KQL @"verbatim" strings ("" doubling) are NOT modeled — acceptable because we only guard
KQL we build ourselves, never arbitrary agent-authored KQL (that is the P4 firewall)."""

import re

_MAX_KQL_LENGTH = 10_000

_CONTROL_COMMANDS = (
    ".drop",
    ".alter",
    ".create",
    ".delete",
    ".set",
    ".append",
    ".set-or-append",
    ".set-or-replace",
    ".ingest",
    ".purge",
    ".execute",
)

_TAUTOLOGY_RE = re.compile(
    r"""or\s+1\s*==\s*1|or\s+true|or\s+'1'\s*==\s*'1'""",
    re.IGNORECASE,
)


def escape_string(value):
    s = str(value).replace("\x00", "")
    return s.replace("\\", "\\\\").replace('"', '\\"')


def escape_entity(name):
    s = str(name)
    if any(c in s for c in ("\n", "\r", "\t", "\x00")):
        raise ValueError(f"invalid control character in entity name: {s!r}")
    return "['" + s.replace("\\", "\\\\").replace("'", "\\'") + "']"


def first_statement(text):
    s = str(text)
    in_str = None
    escaped = False
    for i, ch in enumerate(s):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == ";":
            return s[:i].rstrip()
    return s.rstrip()


def _strip_string_literals(text):
    """Replace the contents of quoted string literals with spaces, preserving length and
    the surrounding structure (pipes, semicolons, dots) so command/tautology checks only
    see code, never literal text. Same boolean-`escaped` state machine as first_statement."""
    s = str(text)
    out = []
    in_str = None
    escaped = False
    for ch in s:
        if in_str:
            out.append(" " if ch not in ("'", '"') or ch != in_str else ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def assert_read_only_kql(kql):
    """Adapted from microsoft/fabric-rti-mcp + microsoft/mcp (MIT).

    Read-only gate for KQL we build ourselves: rejects oversized queries, control
    commands (stacked via `|`/`;` or leading), and boolean-tautology injection attempts.
    Returns the kql unchanged if clean.
    """
    s = str(kql)
    if len(s) > _MAX_KQL_LENGTH:
        raise ValueError(f"KQL exceeds maximum length of {_MAX_KQL_LENGTH} characters")

    stripped = _strip_string_literals(s)

    segments = re.split(r"[|;]", stripped)
    for segment in segments:
        candidate = segment.strip().lower()
        for command in _CONTROL_COMMANDS:
            if candidate.startswith(command):
                raise ValueError(f"control command not allowed in read-only KQL: {command}")

    if _TAUTOLOGY_RE.search(stripped):
        raise ValueError("boolean tautology not allowed in read-only KQL")

    return kql

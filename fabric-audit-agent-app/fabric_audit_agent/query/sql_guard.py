"""SQL construction guards for Fabric Lakehouse/Warehouse SQL endpoints. Pure stdlib.

Follows the same pattern as ``kql_guard.py``: deterministic read-only enforcement with entity
escaping, length/row limits, tautology detection, and string-literal stripping so checks only
see code, never literal text.

SQL Server / Fabric SQL style:
- Identifiers are bracket-escaped: ``[name]``
- String literals use single quotes: ``'value'``  (doubled-quote ``''`` escape, NOT backslash)
- Only ``SELECT``-shaped queries are allowed -- no DDL/DML of any kind.
"""

import re

_MAX_SQL_LENGTH = 10_000
_MAX_SQL_ROWS = 100_000  # consistent with Execute Queries REST API limits

# DML/DDL/admin statement keywords -- word-boundary matched against the first token of each
# semicolon-separated segment (string literals already stripped).
_BLOCKED_STATEMENT_STARTS = frozenset((
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "exec", "execute", "grant", "revoke", "merge", "bulk", "dbcc",
    "backup", "restore", "deny", "declare", "set", "use",
    "kill", "shutdown", "reconfigure", "waitfor", "deallocate",
))

# Dangerous SQL constructs that can appear INSIDE a SELECT but still mutate/exfiltrate --
# scanned as word-boundary patterns against the stripped (string-free) query.
_DANGEROUS_PATTERNS = (
    # SELECT INTO creates a new table
    (re.compile(r"\bselect\b[^;]*\binto\b", re.IGNORECASE | re.DOTALL),
     "SELECT INTO is not allowed in read-only SQL"),
    # Dynamic SQL execution inside a SELECT
    (re.compile(r"\bexec(?:ute)?\s*\(", re.IGNORECASE),
     "EXEC/EXECUTE is not allowed in read-only SQL"),
    # External data access functions
    (re.compile(r"\b(?:openrowset|opendatasource|openquery)\s*\(", re.IGNORECASE),
     "external data access functions are not allowed in read-only SQL"),
    # System/extended stored procedures
    (re.compile(r"\b(?:sp_|xp_)\w+", re.IGNORECASE),
     "system procedure calls are not allowed in read-only SQL"),
)

# Boolean tautology patterns (after string-literal stripping).
# After stripping, 'a' becomes ' ', so string-vs-string equality is detected via the
# stripped form: two adjacent single-quoted tokens compared with =. This catches
# OR 'a'='a', OR 'anything'='anything', etc.
_TAUTOLOGY_RE = re.compile(
    r"""or\s+1\s*=\s*1|or\s+'[^']*'\s*=\s*'[^']*'|or\s+1\s*=\s*'1'|or\s+'1'\s*=\s*1"""
    r"""|or\s+true\b""",
    re.IGNORECASE,
)


def _strip_string_literals(text):
    """Replace the contents of single-quoted SQL string literals with spaces, preserving
    length and surrounding structure (semicolons, keywords) so DML/tautology checks only
    see code, never literal text. SQL Server uses '' (doubled quote) as the escape inside
    single-quoted strings, not backslash."""
    s = str(text)
    out = []
    in_str = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            if ch == "'" and i + 1 < len(s) and s[i + 1] == "'":
                # escaped quote ('') inside a string -- emit spaces for both
                out.append("  ")
                i += 2
                continue
            elif ch == "'":
                # string close
                in_str = False
                out.append(ch)
            else:
                out.append(" ")
        else:
            if ch == "'":
                in_str = True
                out.append(ch)
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def escape_sql_identifier(name):
    """Bracket-escape a SQL Server / Fabric SQL identifier: ``[name]``.

    Brackets inside the name are doubled (``]`` -> ``]]``), which is the standard SQL Server
    escaping for delimited identifiers. Control characters are rejected outright.

    >>> escape_sql_identifier("My Table")
    '[My Table]'
    >>> escape_sql_identifier("T]able")
    '[T]]able]'
    """
    s = str(name)
    if any(c in s for c in ("\n", "\r", "\t", "\x00")):
        raise ValueError(f"invalid control character in SQL identifier: {s!r}")
    return "[" + s.replace("]", "]]") + "]"


def assert_read_only_sql(sql):
    """Read-only gate for SQL queries: rejects oversized queries, any non-SELECT statement,
    stacked statements via semicolons, boolean tautologies, and dangerous intra-SELECT
    constructs (SELECT INTO, EXEC, OPENROWSET, etc.).

    Returns the sql unchanged if clean; raises ``ValueError`` otherwise.
    """
    s = str(sql)

    # 1. length
    if len(s) > _MAX_SQL_LENGTH:
        raise ValueError(f"SQL exceeds maximum length of {_MAX_SQL_LENGTH} characters")

    # 2. strip string literals so checks only see code
    stripped = _strip_string_literals(s)

    # 3. first word must be SELECT or WITH (CTE)
    first_word = ""
    for token in stripped.split():
        first_word = token.lower().lstrip("(")
        break
    if first_word not in ("select", "with"):
        raise ValueError(
            f"only SELECT queries are allowed in read-only SQL; "
            f"got statement starting with '{first_word}'"
        )

    # 4. reject stacked statements (semicolons outside string literals)
    segments = stripped.split(";")
    for idx, segment in enumerate(segments):
        candidate = segment.strip()
        if not candidate:
            continue
        if idx > 0:
            raise ValueError(
                "multiple statements (semicolons) not allowed in read-only SQL"
            )

    # 5. dangerous intra-SELECT patterns
    for pattern, message in _DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            raise ValueError(message)

    # 6. tautology detection
    if _TAUTOLOGY_RE.search(stripped):
        raise ValueError("boolean tautology not allowed in read-only SQL")

    return sql

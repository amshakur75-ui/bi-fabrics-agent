"""DAX construction guards for Power BI XMLA endpoint queries. Pure stdlib.

Follows the same pattern as ``kql_guard.py``: deterministic read-only enforcement with entity
escaping, length limits, tautology detection, and string-literal stripping so checks only
see code, never literal text.

DAX style:
- Table references are single-quoted: ``'My Table'``
- Column references use bracket notation: ``[Column Name]``
- String literals use double quotes: ``"value"``
- Only ``EVALUATE``-shaped queries are allowed -- DAX has no DDL/DML statements, but some
  admin commands (ALTER, CREATE, DELETE) exist in TMSL/XMLA context; we reject them.
"""

import re

_MAX_DAX_LENGTH = 10_000
_MAX_DAX_ROWS = 100_000  # practical ceiling consistent with the Execute Queries REST API

# DAX string literals use double quotes ("value"). Single quotes are for table references.
# We strip double-quoted strings so checks only see code.


def _strip_string_literals(text):
    """Replace the contents of double-quoted DAX string literals with spaces, preserving
    length and surrounding structure so keyword/tautology checks only see code, never literal
    text. DAX uses "" (doubled double-quote) as the escape inside strings."""
    s = str(text)
    out = []
    in_str = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            if ch == '"' and i + 1 < len(s) and s[i + 1] == '"':
                # escaped quote ("") inside a string -- emit spaces for both
                out.append("  ")
                i += 2
                continue
            elif ch == '"':
                # string close
                in_str = False
                out.append(ch)
            else:
                out.append(" ")
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def escape_dax_reference(name):
    """Single-quote escape for DAX table references: ``'name'``.

    Single quotes inside the name are doubled (``'`` -> ``''``), which is the standard DAX
    escaping for table/column references. Control characters are rejected outright.

    >>> escape_dax_reference("My Table")
    "'My Table'"
    >>> escape_dax_reference("It's a table")
    "'It''s a table'"
    """
    s = str(name)
    if any(c in s for c in ("\n", "\r", "\t", "\x00")):
        raise ValueError(f"invalid control character in DAX reference: {s!r}")
    return "'" + s.replace("'", "''") + "'"


# Boolean tautology patterns (after string-literal stripping).
# After stripping, "a" becomes " ", so string-vs-string equality is detected via the
# stripped form: two adjacent double-quoted tokens compared with =.
_TAUTOLOGY_RE = re.compile(
    r"""\|\|\s*TRUE\b|\|\|\s*1\s*=\s*1"""
    r"""|\bor\s+1\s*=\s*1|\bor\s+true\b"""
    r"""|\bor\s+"[^"]*"\s*=\s*"[^"]*\"""",
    re.IGNORECASE,
)

# DAX queries must start with one of these keywords. EVALUATE is the standard query keyword;
# DEFINE is used in combination with EVALUATE for local measures/tables.
_ALLOWED_STARTS = frozenset(("evaluate", "define"))

# Blocked keywords -- these can appear in XMLA/TMSL but are not valid in a read-only DAX
# evaluation context. Matched as word-boundary patterns against stripped code.
_BLOCKED_KEYWORDS_RE = re.compile(
    r"\b(?:alter|create|delete|drop|insert|update|refresh|process|backup|restore"
    r"|merge|truncate|exec|execute|grant|revoke)\b",
    re.IGNORECASE,
)


def assert_read_only_dax(dax):
    """Read-only gate for DAX queries: rejects oversized queries, any non-EVALUATE-shaped
    query, blocked keywords, and boolean tautologies.

    DAX is expression-only (no semicolon-based statement stacking like SQL), but we still
    validate that the query starts with EVALUATE or DEFINE and contains no admin commands.

    Returns the dax unchanged if clean; raises ``ValueError`` otherwise.
    """
    s = str(dax)

    # 1. length
    if len(s) > _MAX_DAX_LENGTH:
        raise ValueError(f"DAX exceeds maximum length of {_MAX_DAX_LENGTH} characters")

    # 2. strip string literals so checks only see code
    stripped = _strip_string_literals(s)

    # 3. blocked keywords FIRST (admin commands that could appear in XMLA/TMSL context).
    #    Checked before the first-word gate so "ALTER TABLE ..." gets the specific
    #    "'ALTER' is not allowed" error rather than the generic "only EVALUATE" one.
    m = _BLOCKED_KEYWORDS_RE.search(stripped)
    if m:
        raise ValueError(
            f"'{m.group().upper()}' is not allowed in read-only DAX"
        )

    # 4. first word must be EVALUATE or DEFINE
    first_word = ""
    for token in stripped.split():
        first_word = token.lower()
        break
    if first_word not in _ALLOWED_STARTS:
        raise ValueError(
            f"only EVALUATE queries are allowed in read-only DAX; "
            f"got statement starting with '{first_word}'"
        )

    # 5. tautology detection
    if _TAUTOLOGY_RE.search(stripped):
        raise ValueError("boolean tautology not allowed in read-only DAX")

    return dax

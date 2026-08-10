"""SQL construction guards for Fabric Lakehouse/Warehouse SQL endpoints. Pure stdlib.

Follows the same pattern as ``kql_guard.py``: deterministic read-only enforcement with entity
escaping, length/row limits, tautology detection, and string-literal stripping so checks only
see code, never literal text.

SQL Server / Fabric SQL style:
- Identifiers are bracket-escaped: ``[name]``
- String literals use single quotes: ``'value'``  (doubled-quote ``''`` escape, NOT backslash)
- Only ``SELECT``-shaped queries are allowed -- no DDL/DML of any kind.

Like ``kql_guard`` and ``firewall``, this gate FAILS CLOSED: an input the scrubber cannot
account for to the last character (unterminated literal, identifier or block comment) is
rejected instead of analysed.
"""

import re

_MAX_SQL_LENGTH = 10_000
_MAX_SQL_ROWS = 100_000  # consistent with Execute Queries REST API limits

# DML/DDL/admin statement keywords -- word-boundary matched ANYWHERE in the scrubbed query, not
# just at the start of a semicolon-separated segment. T-SQL does not require a semicolon between
# statements, so `SELECT 1 DROP TABLE dbo.Sales` and `SELECT * FROM t UPDATE dbo.x SET y=1` are
# two statements each and both passed the old first-token-only check.
_BLOCKED_KEYWORDS = frozenset((
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "exec", "execute", "grant", "revoke", "merge", "bulk", "dbcc",
    "backup", "restore", "deny", "declare", "set", "use",
    "kill", "shutdown", "reconfigure", "waitfor", "deallocate",
))

_BLOCKED_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_BLOCKED_KEYWORDS)) + r")\b", re.IGNORECASE
)

# Tokens a legitimate SELECT may follow: subquery/branch/set-operator positions. Anything else
# before a second SELECT (an identifier, a number, a closing quote) means the previous statement
# had already finished -- i.e. statements stacked without a semicolon.
_SELECT_MAY_FOLLOW = frozenset((
    "(", ")", ",", "=", "<", ">", "+", "-", "*", "/", "%",
    "union", "all", "intersect", "except", "as", "exists", "in", "and", "or", "not",
    "then", "else", "when", "case", "any", "some", "apply", "from", "by", "having",
    "where", "on", "top", "distinct", "is", "like", "between", "return",
))

# Identifiers, numbers, or a single punctuation character -- enough to ask "what precedes this
# SELECT?" without pretending to be a T-SQL parser.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$#@]*|\d+(?:\.\d+)?|[^\sA-Za-z0-9_]")

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


def _blank(text):
    """Same-length blanking that keeps newlines, so scrubbed regions can't merge two lines."""
    return "".join("\n" if c == "\n" else " " for c in text)


def _scrub_sql(text):
    """Blank out everything that is not executable code -- string-literal contents, ``--`` line
    comments, (nestable) ``/* */`` block comments, and the contents of delimited identifiers
    (``[name]`` / ``"name"``) -- replacing each character with a space so length, semicolons and
    surrounding keywords stay exactly where they were.

    Comments and delimited identifiers are scrubbed because a string-literals-only pass let a
    mutating statement hide behind either one. Both of these reached the gate as clean SELECTs:
    ``SELECT [col'umn] FROM t; DROP TABLE dbo.Sales`` and ``SELECT 1 -- '\\n; DROP TABLE
    dbo.Sales`` -- in each case an unpaired quote inside a comment or a bracket-quoted identifier
    desynchronised the literal state machine, so the whole DROP was blanked out as "string
    contents" before any check ran.

    SQL Server escapes are the doubled delimiter: ``''`` inside a literal, ``]]`` inside
    brackets, ``""`` inside a quoted identifier -- never a backslash.

    Raises ``ValueError`` on an unterminated literal, identifier or block comment: an input this
    scrubber cannot account for to the last character is rejected, never analysed.
    """
    s = str(text)
    n = len(s)
    out = []
    i = 0
    while i < n:
        pair = s[i:i + 2]
        ch = s[i]

        if pair == "--":
            nl = s.find("\n", i)
            end = n if nl == -1 else nl
            out.append(_blank(s[i:end]))
            i = end
            continue

        if pair == "/*":
            # T-SQL block comments nest, so track depth rather than scanning for the first `*/`.
            depth, j = 1, i + 2
            while j < n and depth:
                if s[j:j + 2] == "/*":
                    depth += 1
                    j += 2
                elif s[j:j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                raise ValueError("unterminated block comment in SQL — rejected as unparseable")
            out.append(_blank(s[i:j]))
            i = j
            continue

        if ch in ("'", "[", '"'):
            close = "]" if ch == "[" else ch
            j, closed = i + 1, False
            while j < n:
                if s[j] == close:
                    if s[j + 1:j + 2] == close:   # doubled delimiter = escaped, still inside
                        j += 2
                        continue
                    closed = True
                    j += 1
                    break
                j += 1
            if not closed:
                kind = "string literal" if ch == "'" else "delimited identifier"
                raise ValueError(f"unterminated {kind} in SQL — rejected as unparseable")
            # Delimiters survive; the tautology check reads adjacent quote pairs.
            out.append(s[i] + _blank(s[i + 1:j - 1]) + s[j - 1])
            i = j
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def _assert_no_stacked_select(scrubbed):
    """Reject a second statement stacked WITHOUT a semicolon -- T-SQL does not require one.

    A SELECT that follows a completed expression (an identifier, a number, a closing quote) is a
    new statement: ``SELECT 1 SELECT 2``, ``SELECT * FROM t SELECT 2``. A SELECT in a subquery,
    a CASE branch or after a set operator follows one of ``_SELECT_MAY_FOLLOW`` instead, so
    ordinary queries -- ``... WHERE x IN (SELECT ...)``, ``... UNION ALL SELECT ...``,
    ``WITH cte AS (SELECT ...) SELECT ...`` -- are untouched.
    """
    tokens = _TOKEN_RE.findall(scrubbed)
    for idx, token in enumerate(tokens):
        if idx == 0 or token.lower() != "select":
            continue
        if tokens[idx - 1].lower() not in _SELECT_MAY_FOLLOW:
            raise ValueError(
                "multiple statements (stacked SELECT without a semicolon) not allowed in "
                "read-only SQL"
            )


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
    stacked statements (with OR without a semicolon), any DML/DDL/admin keyword anywhere outside
    a literal or comment, boolean tautologies, and dangerous intra-SELECT constructs
    (SELECT INTO, EXEC, OPENROWSET, etc.).

    Returns the sql unchanged if clean; raises ``ValueError`` otherwise. Anything the scrubber
    cannot parse is rejected -- there is no "analyse it anyway" path.
    """
    s = str(sql)

    # 1. length
    if len(s) > _MAX_SQL_LENGTH:
        raise ValueError(f"SQL exceeds maximum length of {_MAX_SQL_LENGTH} characters")

    # 2. blank literals, comments and delimited identifiers so checks only see code
    stripped = _scrub_sql(s)

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

    # 4. reject stacked statements (semicolons outside literals/comments/identifiers)
    segments = stripped.split(";")
    for idx, segment in enumerate(segments):
        candidate = segment.strip()
        if not candidate:
            continue
        if idx > 0:
            raise ValueError(
                "multiple statements (semicolons) not allowed in read-only SQL"
            )

    # 5. any DML/DDL/admin keyword ANYWHERE, plus a SELECT stacked without a semicolon. Both
    # exist because T-SQL statement separation is optional: `SELECT 1 DROP TABLE dbo.Sales` is a
    # legal batch whose first token is SELECT.
    blocked = _BLOCKED_KEYWORD_RE.search(stripped)
    if blocked:
        raise ValueError(
            f"'{blocked.group(0).upper()}' is not allowed in read-only SQL "
            f"(DML/DDL/admin keyword outside a string literal or comment)"
        )
    _assert_no_stacked_select(stripped)

    # 6. dangerous intra-SELECT patterns
    for pattern, message in _DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            raise ValueError(message)

    # 7. tautology detection
    if _TAUTOLOGY_RE.search(stripped):
        raise ValueError("boolean tautology not allowed in read-only SQL")

    return sql

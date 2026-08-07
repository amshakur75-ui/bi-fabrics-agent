"""kql_format.py — pipe-per-line KQL indentation formatter.

Ports ``formatKql`` from the kql-mcp-server-v5 plugin (``services/format.ts``, MIT-licensed):
each top-level ``|`` pipe operator gets its own line, indented under the source table / ``let``
statement it follows, so a one-line query becomes readable multi-line KQL. Comment lines
(``//``) and blank lines pass through unchanged; consecutive ``let`` statements get a blank
line inserted between them and the first ``|`` that follows.

This is a DISPLAY-ONLY formatter — it never changes query semantics and is never used to build
or alter the query that actually executes. It is deliberately tolerant: any query it can't
confidently reformat (or that already has meaningful line structure) is returned unchanged
rather than risk mangling a string literal or comment. Pure stdlib, no dependencies.

Unlike format.ts (which only ever sees single-line query text coming out of an LLM), this port
adds one safeguard the original doesn't need: it refuses to touch a ``|`` that falls inside an
unterminated string literal on the same line, so a pipe character embedded in a quoted string
(single, double, or KQL's verbatim ``@"..."``/``@'...'``) is never mistaken for an operator
boundary.
"""
import re

_LET_RE = re.compile(r"^let\s+", re.IGNORECASE)
_COMMENT_RE = re.compile(r"^//")


def _split_top_level_pipes(line):
    """Split *line* on ``|`` characters that are NOT inside a string literal.

    Returns a list of segments (without the leading ``|``); the first segment is whatever
    precedes the first top-level pipe (often empty). Tracks single-quote, double-quote, and
    KQL verbatim-string (``@"..."``/``@'...'``) state char-by-char; verbatim strings don't
    support backslash escapes but a doubled quote (``""``) inside one is a literal quote, same
    as a normal string's backslash-escape does for ``\\"``.
    """
    segments = []
    current = []
    quote_char = None
    verbatim = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote_char is None:
            if ch == "@" and i + 1 < n and line[i + 1] in ("'", '"'):
                verbatim = True
                quote_char = line[i + 1]
                current.append(ch)
                current.append(quote_char)
                i += 2
                continue
            if ch in ("'", '"'):
                quote_char = ch
                verbatim = False
                current.append(ch)
                i += 1
                continue
            if ch == "|":
                segments.append("".join(current))
                current = []
                i += 1
                continue
            current.append(ch)
            i += 1
            continue
        else:
            # Inside a string literal.
            if not verbatim and ch == "\\" and i + 1 < n:
                current.append(ch)
                current.append(line[i + 1])
                i += 2
                continue
            if ch == quote_char:
                if verbatim and i + 1 < n and line[i + 1] == quote_char:
                    current.append(ch)
                    current.append(line[i + 1])
                    i += 2
                    continue
                current.append(ch)
                quote_char = None
                verbatim = False
                i += 1
                continue
            current.append(ch)
            i += 1
            continue
    segments.append("".join(current))
    if quote_char is not None:
        # Unterminated string literal on this line — bail so the caller falls back to the
        # original text rather than mis-splitting a pipe that's actually inside a multi-line
        # string.
        return None
    return segments


def format_kql(kql):
    """Reformat *kql* with one top-level ``|`` pipe operator per line.

    Tolerant by design: returns the input unchanged (after only a light strip) whenever it
    can't confidently reformat — ``None``/empty input, a query containing an unterminated
    string literal on some line (likely a multi-line string the formatter isn't equipped to
    parse), or any unexpected error. Never raises.
    """
    if kql is None:
        return kql
    if not isinstance(kql, str):
        return kql
    if kql.strip() == "":
        return kql

    try:
        lines = re.split(r"\r\n|\r|\n", kql)
        output = []
        in_let = False

        for raw_line in lines:
            trimmed = raw_line.strip()
            if trimmed == "":
                output.append("")
                continue

            if _COMMENT_RE.match(trimmed):
                output.append(trimmed)
                continue

            if _LET_RE.match(trimmed):
                if in_let:
                    output.append("")
                in_let = True
                output.append(trimmed)
                continue

            segments = _split_top_level_pipes(trimmed)
            if segments is None:
                # Unterminated string literal on this line — don't risk mangling it; fall
                # back to returning the original query untouched.
                return kql

            if len(segments) == 1:
                # No top-level pipe on this line at all.
                if trimmed.startswith("|"):
                    in_let = False
                    output.append("  " + trimmed)
                else:
                    in_let = False
                    output.append(trimmed)
                continue

            # One or more top-level pipes: emit the pre-pipe head (if any) then one indented
            # line per pipe stage, preserving the leading "|" for each.
            in_let = False
            head = segments[0].strip()
            if head:
                output.append(head)
            for seg in segments[1:]:
                output.append("  |" + seg.rstrip())

        result = "\n".join(output)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()
    except Exception:
        return kql

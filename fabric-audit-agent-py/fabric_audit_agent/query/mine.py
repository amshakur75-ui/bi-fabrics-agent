"""Query-library growth loop: pure log-parsing and shape-canonicalization foundation (Task 1).

No I/O here (stdlib ``json``/``re`` only). File reads (the audit log, the library) and the
``--write`` mutation live in ``entrypoints.py`` (later task) -- mirrors the "mine.py is pure"
architecture decision in the plan. Ranking (``rank_candidates``) and projection
(``to_library_entries``) land in later tasks; this module only exposes ``parse_audit_lines`` and
``shape_key``.
"""
import json
import re

from . import kql_guard

# The [adhoc-kql] audit line is `print("[adhoc-kql] " + json.dumps(rec, ...))` (tools.py). A
# logger prefix may precede the marker, so we match on the substring, not line-start.
_MARKER = "[adhoc-kql] "

_ALLOWED_ENGINES = ("capacity", "la")

# Trailing "| take <int>" / "| limit <int>" -- stripped in a loop so a doubled bound (the agent's
# own query already ended in take/limit, then the audit line appends its own) fully collapses to
# the agent's original base query. Case-insensitive so "TAKE"/"LIMIT" strip the same as lowercase.
_TRAILING_BOUND_RE = re.compile(r"\|\s*(?:take|limit)\s+\d+\s*$", re.IGNORECASE)

# Numeric RHS of a comparison operator -- longest operators first so ">=" is tried before ">".
# Scoped to comparisons only: never matches arithmetic (`* 1000 * 30`), bin() args, or take/limit/
# top N (none of those are preceded by a comparison operator).
_COMPARISON_NUM_RE = re.compile(r"(>=|<=|==|!=|>|<)(\s*)(-?\d+(?:\.\d+)?)")

# The argument INSIDE ago(...) / datetime(...) only -- scoped by the call syntax itself so a
# timespan literal elsewhere (e.g. inside bin(win, 1d)) is never touched.
_AGO_RE = re.compile(r"\bago\s*\(\s*[^()]*?\s*\)", re.IGNORECASE)
_DATETIME_RE = re.compile(r"\bdatetime\s*\(\s*[^()]*?\s*\)", re.IGNORECASE)

_WHITESPACE_RE = re.compile(r"\s+")

# KQL operator/keyword vocabulary that gets case-normalized. Deliberately NOT a blanket
# lowercase of the whole query -- table/column names (e.g. "ExecutingUser") must keep their
# authored case; only these recognized keywords/operators are folded to lowercase.
_KQL_KEYWORDS = (
    "where", "extend", "summarize", "project", "sort", "order", "by", "desc", "asc",
    "top", "take", "limit", "join", "union", "distinct", "count", "avg", "sum", "min", "max",
    "and", "or", "not", "in", "has", "contains", "startswith", "endswith", "matches",
    "let", "render", "evaluate", "parse", "bin", "ago", "datetime",
    "tostring", "tolong", "toint", "todatetime", "todouble", "ingestion_time",
    "isnotempty", "isempty", "isnull", "isnotnull",
)
_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _KQL_KEYWORDS) + r")\b", re.IGNORECASE
)


def parse_audit_lines(lines) -> list[dict]:
    """For each line containing the substring "[adhoc-kql] " (a logger prefix may precede it),
    json.loads the text after that marker. Skips non-marker lines and malformed JSON -- never
    raises. Returns only records with verdict == "allowed" AND engine in {"capacity", "la"}.
    ``lines`` may be a list of strings or any iterable of strings.
    """
    out = []
    for line in lines:
        if not isinstance(line, str):
            continue
        idx = line.find(_MARKER)
        if idx == -1:
            continue
        payload = line[idx + len(_MARKER):]
        try:
            rec = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("verdict") != "allowed":
            continue
        if rec.get("engine") not in _ALLOWED_ENGINES:
            continue
        out.append(rec)
    return out


def shape_key(kql: str) -> str:
    """Canonical grouping key for a (bounded, possibly-redacted) KQL query. Deterministic, pure.

    In order: (1) loop-strip a trailing "| take <int>" / "| limit <int>"; (2) blank string-literal
    content; (3) placeholder the numeric RHS of a comparison and the arg inside ago()/datetime()
    only; (4) collapse whitespace; (5) lowercase recognized KQL keywords.
    """
    s = str(kql)

    # (1) repeatedly strip a trailing take/limit bound.
    while True:
        stripped = _TRAILING_BOUND_RE.sub("", s)
        if stripped == s:
            break
        s = stripped.rstrip()

    # (2) blank string-literal content (same state machine kql_guard uses elsewhere).
    s = kql_guard._strip_string_literals(s)

    # (3a) placeholder the numeric RHS of a comparison operator only.
    s = _COMPARISON_NUM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<N>", s)

    # (3b) placeholder the argument inside ago(...) / datetime(...) only.
    s = _AGO_RE.sub("ago(<TS>)", s)
    s = _DATETIME_RE.sub("datetime(<DT>)", s)

    # (4) collapse whitespace runs.
    s = _WHITESPACE_RE.sub(" ", s).strip()

    # (5) lowercase recognized KQL operator keywords (not identifiers).
    s = _KEYWORD_RE.sub(lambda m: m.group(0).lower(), s)

    return s

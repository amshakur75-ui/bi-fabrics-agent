"""Query-library growth loop: pure log-parsing, shape-canonicalization, ranking, and library-entry
projection (Tasks 1-3).

No I/O here (stdlib ``json``/``re``/``hashlib``/``collections`` only). File reads (the audit log,
the library) and the ``--write`` mutation live in ``entrypoints.py`` (later task) -- mirrors the
"mine.py is pure" architecture decision in the plan. This module exposes ``parse_audit_lines``,
``shape_key``, ``rank_candidates``, and ``to_library_entries``.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict

from . import kql_guard
from .firewall import validate_adhoc_kql, FirewallRejection

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


def _strip_trailing_bounds(s: str) -> str:
    """Repeatedly strip a trailing "| take <int>" / "| limit <int>" until none remain. Shared by
    ``shape_key`` and ``rank_candidates`` so a mined representative and its shape_key can never
    drift apart (the precondition for idempotent dedup against the library)."""
    while True:
        stripped = _TRAILING_BOUND_RE.sub("", s)
        if stripped == s:
            return s
        s = stripped.rstrip()


def shape_key(kql: str) -> str:
    """Canonical grouping key for a (bounded, possibly-redacted) KQL query. Deterministic, pure.

    In order: (1) loop-strip a trailing "| take <int>" / "| limit <int>"; (2) blank string-literal
    content; (3) placeholder the numeric RHS of a comparison and the arg inside ago()/datetime()
    only; (4) collapse whitespace; (5) lowercase recognized KQL keywords.
    """
    s = _strip_trailing_bounds(str(kql))

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


# Redaction sentinel emitted by every redact.redact_secrets substitution (redact.py:29-31). A
# member containing this is treated as "not the real query that ran" and excluded from
# representative selection (a group with only such members is dropped). ACCEPTED FALSE-DROP: a
# legitimate query that happens to contain a literal "***" (e.g. a string constant, or an
# `| extend sig=value` that redact masks to `sig=***`) is indistinguishable from a redacted one and
# is dropped too. This fails closed (never promotes a non-running query) and is preferred over a
# fragile attempt to tell real redaction from a coincidental "***".
_REDACTED_MARKER = "***"


def rank_candidates(records, existing_templates, *, min_count=3, top_n=10) -> list[dict]:
    """Group *records* (allowed audit records, each with 'engine' and 'kql') by
    ``(engine, shape_key(kql))``. Drop any group whose ``(engine, shapeKey)`` is already covered by
    *existing_templates* (the same ``shape_key`` is applied to each template's 'kql', so dedup is
    symmetric). Keep groups with ``count >= min_count``.

    The representative is the most-frequent EXACT kql in the group -- each member's kql is first
    stripped of a trailing take/limit bound via ``_strip_trailing_bounds`` -- among members whose
    text does NOT contain the redaction sentinel ``"***"``; ties are broken lexicographically
    (ascending). If every member is redacted, the group is dropped. The representative is always a
    literal observed (post-strip) member, never synthesized from the shape key. The group is then
    dropped if ``validate_adhoc_kql(representative)`` raises ``FirewallRejection``.

    Survivors are sorted deterministically by ``(count DESC, shapeKey ASC)`` and the top *top_n*
    are returned as ``{"engine", "shapeKey", "kql", "hitCount"}`` dicts. Pure, no I/O.
    """
    if not records:
        return []

    existing_shapes = set()
    for tmpl in existing_templates or ():
        if not isinstance(tmpl, dict):
            continue
        engine = tmpl.get("engine")
        kql = tmpl.get("kql")
        if engine is None or kql is None:
            continue
        existing_shapes.add((engine, shape_key(kql)))

    # (engine, shapeKey) -> list of stripped exact kql strings, one per raw record, in input order.
    groups = defaultdict(list)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        engine = rec.get("engine")
        kql = rec.get("kql")
        if engine is None or kql is None:
            continue
        groups[(engine, shape_key(kql))].append(_strip_trailing_bounds(str(kql)))

    candidates = []
    for (engine, shape), members in groups.items():
        if (engine, shape) in existing_shapes:
            continue

        hit_count = len(members)
        if hit_count < min_count:
            continue

        clean_members = [m for m in members if _REDACTED_MARKER not in m]
        if not clean_members:
            continue

        freq = Counter(clean_members)
        max_freq = max(freq.values())
        representative = min(m for m, c in freq.items() if c == max_freq)

        try:
            validate_adhoc_kql(representative)
        except FirewallRejection:
            continue

        candidates.append({
            "engine": engine,
            "shapeKey": shape,
            "kql": representative,
            "hitCount": hit_count,
        })

    candidates.sort(key=lambda c: (-c["hitCount"], c["shapeKey"]))
    return candidates[:top_n]


# --------------------------------------------------------------------------------------------
# to_library_entries (Task 3)
# --------------------------------------------------------------------------------------------

# The token immediately following a top-level "|" -- letters/digits/underscore/hyphen so
# multi-word operators like "mv-expand" or "project-away" are captured whole.
_PIPE_TOKEN_RE = re.compile(r"\|\s*([A-Za-z][A-Za-z0-9_-]*)")

# A "reasonable operator set" (plan Task 3): only these are confidently named. Anything else
# (or no pipe at all) falls back to the honest 'query' label rather than guessing.
_KNOWN_OPERATORS = frozenset({
    "where", "summarize", "project", "extend", "join", "union", "distinct", "count",
    "top", "sort", "order", "take", "limit", "parse", "render", "mv-expand", "evaluate",
    "sample", "getschema", "project-away", "project-rename", "lookup", "make-series",
    "serialize",
})

_NAME_UNSAFE_RE = re.compile(r"[^a-z0-9-]+")


def _sanitize_name_part(s: str) -> str:
    """Fold to the [a-z0-9-] name charset; empty/unsafe input becomes 'query'."""
    s = s.lower().replace("_", "-")
    s = _NAME_UNSAFE_RE.sub("-", s).strip("-")
    return s or "query"


def _dominant_operator(kql: str) -> str:
    """The dominant top-level KQL pipe operator in *kql*: the token right after each '|',
    restricted to a known operator vocabulary (``_KNOWN_OPERATORS``) so an unrecognized token
    never wins. Highest count wins; ties are broken by first-appearance order in the text.
    Falls back to ``'query'`` when no known operator is found. Deterministic, pure.
    """
    counts = Counter()
    first_seen_order = []
    # Blank string-literal content first so a '|' inside a string (e.g. `has "a | b"`) can't be
    # miscounted as a pipe operator and flip the chosen label.
    scanned = kql_guard._strip_string_literals(str(kql))
    for match in _PIPE_TOKEN_RE.finditer(scanned):
        token = _sanitize_name_part(match.group(1))
        if token not in _KNOWN_OPERATORS:
            continue
        if token not in counts:
            first_seen_order.append(token)
        counts[token] += 1

    if not counts:
        return "query"

    max_count = max(counts.values())
    for token in first_seen_order:
        if counts[token] == max_count:
            return token
    return "query"  # pragma: no cover -- unreachable, defensive only


def to_library_entries(ranked, existing_templates) -> list[dict]:
    """Project each ranked group (``rank_candidates`` output: ``{engine, shapeKey, kql,
    hitCount}``) into a library-schema entry with keys in EXACTLY this order: ``name, category,
    engine, description, kql, groundedIn, hitCount`` (the existing on-disk key order plus a
    trailing ``hitCount``). Preserves the input order of *ranked*. Pure, no I/O.

    - ``category``   = ``"adhoc-mined"``
    - ``groundedIn`` = ``"mined from adhoc audit log"``
    - ``description``= ``f"Auto-mined {engine} query ({op}); seen {hitCount}x in the ad-hoc
      audit log."`` -- factual, never a placeholder.
    - ``kql``        = the group's representative kql, unchanged.
    - ``name``       = ``f"adhoc-{engine}-{op}-{h}"``, lowercase/no-spaces/kebab, where ``op`` is
      the dominant operator (``_dominant_operator``, derived from the representative kql text --
      not the shape key, which is already keyword-lowercased for a different purpose) and ``h``
      is ``hashlib.sha1(shapeKey.encode()).hexdigest()[:6]``. Name uniqueness is enforced both
      against *existing_templates*' names and against names already emitted earlier in this same
      batch; on collision, ``h`` is lengthened one hex character at a time (7, 8, ... up to the
      full 40-char digest) until unique.
    """
    if not ranked:
        return []

    used_names = set()
    for tmpl in existing_templates or ():
        if isinstance(tmpl, dict) and tmpl.get("name") is not None:
            used_names.add(tmpl["name"])

    entries = []
    for group in ranked:
        engine = group["engine"]
        shape = group["shapeKey"]
        kql = group["kql"]
        hit_count = group["hitCount"]

        op = _dominant_operator(kql)
        digest = hashlib.sha1(str(shape).encode("utf-8")).hexdigest()

        # Sanitize engine into the NAME (defense-in-depth: the name must stay kebab even if a
        # caller ever bypasses parse_audit_lines' engine allowlist). The entry's own `engine`
        # field below is left raw -- it must stay "capacity"/"la" for the enum + run_kql.
        safe_engine = _sanitize_name_part(str(engine))
        name = f"adhoc-{safe_engine}-{op}-{digest[:6]}"
        length = 6
        while name in used_names and length < len(digest):
            length += 1
            name = f"adhoc-{safe_engine}-{op}-{digest[:length]}"
        used_names.add(name)

        entries.append({
            "name": name,
            "category": "adhoc-mined",
            "engine": engine,
            "description": (
                f"Auto-mined {engine} query ({op}); seen {hit_count}x in the ad-hoc audit log."
            ),
            "kql": kql,
            "groundedIn": "mined from adhoc audit log",
            "hitCount": hit_count,
        })

    return entries

"""Read-only ad-hoc KQL firewall (pure). Adapted from microsoft/fabric-rti-mcp + 4R9UN/mcp-kql-server
(MIT). Static rejection for AGENT-AUTHORED KQL — stricter than the trusted-seam guards in kql_guard:
a top-level ``;`` is REJECTED (never truncated), verbatim string literals (``@"..."``/``@'...'``),
triple-backtick multiline string literals (```` ```...``` ````), and ``//`` line comments are
REJECTED outright (see below), and a dangerous-operator deny-list closes the cross-resource /
external-read escapes that a read-only control-command gate doesn't cover.

The engine's own binder (take-0 rehearsal, in the run_kql handler) is the live-schema check; this
module is the cheap static pass that runs first. Pure: no I/O, no engine calls, deterministic.

QUOTE-PARITY DESYNC CLASS (the general bug, not just string literals): ``first_statement``/
``_strip_string_literals``/``assert_read_only_kql`` all rely on a single ``'``/``"`` quote-parity
state machine to decide what is "inside a string" (and therefore safe to ignore). ANY KQL construct
whose CONTENT that state machine scans but does NOT correctly model can desync the parity counter —
an odd number of unescaped ``'``/``"`` inside the construct flips the machine's "am I in a string"
bit, so code appearing after the construct gets silently blanked from every later stage (deny-list,
multi-statement, control-command) while the real Kusto/Log Analytics engine executes it normally.

KQL's complete set of constructs whose content could plausibly hit this, and how each is handled:
  - regular ``'...'`` / ``"..."`` strings (with ``\\`` escapes) — MODELED by kql_guard's state
    machine directly; safe.
  - ``h``-prefixed "obfuscated" literals (``h"..."`` / ``h'...'``) — parse identically to regular
    quoted strings (the ``h`` is just a marker prefix, the quotes are real); MODELED, safe.
  - bracketed entity/column names (``['name']`` / ``["name"]``) — the bracket is decoration around
    a REAL quoted string the state machine already tracks; safe.
  - ``datetime(...)`` / ``guid(...)`` / ``dynamic(...)`` / ``dynamic({...})`` literals — their
    contents are ordinary ``"..."`` JSON/scalar text, already MODELED by the regular-string rule;
    safe.
  - VERBATIM strings (``@"..."`` / ``@'...'``) — backslash is a literal char and the string closes
    at the very next quote, a rule the state machine doesn't know; UNMODELED, REJECTED on raw text
    before the state machine runs (prior fix, ``_VERBATIM_MARKER``).
  - MULTILINE triple-backtick strings (```` ```...``` ````) — backtick isn't tracked as a quote
    character at all, so a stray ``'``/``"`` inside one desyncs quote parity; UNMODELED, REJECTED
    on raw text before the state machine runs (``_BACKTICK``).
  - ``//`` LINE COMMENTS — comment text is scanned by the state machine as if it were code (KQL has
    no ``/* */`` block comments, so ``//`` is the only comment form); a stray ``'``/``"`` inside a
    comment desyncs parity for the rest of the query even though the comment itself ends at the
    newline; UNMODELED, REJECTED on raw text before the state machine runs (``_LINE_COMMENT``).

CLOSURE ARGUMENT: verbatim strings, triple-backtick blocks, and ``//`` comments are the only three
constructs in this list whose content the state machine scans without modeling correctly — every
other construct either IS a real quoted string the machine tracks, or wraps one. Because all three
unmodeled constructs are rejected on the RAW text, before ``first_statement``/``_strip_string_literals``
ever run, those functions only ever receive input they model correctly. That means the multi-statement
gate, the control-command gate, and the denied-operator deny-list — all of which depend on those
functions' output — always operate on faithfully-stripped code. This closes the quote-parity-desync
bypass class in full."""
import re

from .kql_guard import assert_read_only_kql, first_statement, _strip_string_literals

_MAX_ADHOC_LEN = 10_000

# KQL verbatim-string marker: '@' immediately before a quote, e.g. @"..." / @'...'. In a verbatim
# string '\' is a LITERAL character (not an escape) and the string closes at the very next quote.
# first_statement/_strip_string_literals below (and in kql_guard) only model REGULAR strings with
# backslash escaping -- they do NOT know this rule. A verbatim string ending in a literal '\"'
# (e.g. @"x\") makes those state machines think the string is still open, so they blank/ignore
# everything after it -- but the Kusto/LA engine closes the string right there and EXECUTES the
# trailing text. That defeats the multi-statement gate, the control-command gate, AND the
# denied-operator deny-list below (cross-cluster/database reads, stacked ';' control commands,
# etc. all "hide" inside what looks like an unterminated string). Agent read-only ad-hoc queries
# never legitimately need verbatim strings, so we fail closed: reject any query containing this
# marker before it ever reaches the fooled state machines.
_VERBATIM_MARKER = re.compile(r'@[\'"]')

# KQL triple-backtick multiline string literal (```...```), the SAME bypass class as verbatim
# strings above: first_statement/_strip_string_literals (and kql_guard's state machine) track
# only '/" quote parity and treat backtick as an ordinary character. A stray '/" inside a
# backtick-delimited block desyncs that parity counter, so a call after the block (e.g.
# `union database(...)`) gets silently blanked before the denied-operator deny-list ever sees it
# -- e.g. T | where m == ```it's fine``` | union database('SecretDB').SecretTable passes the
# state machine untouched. Agent read-only ad-hoc queries never legitimately need a backtick
# (entity-name quoting uses ['name'], not backticks), so we fail closed: reject any backtick on
# the raw text, before the fooled state machines run.
_BACKTICK = re.compile(r"`")

# KQL '//' line comment, the SAME bypass class as verbatim strings and triple-backtick blocks
# above: first_statement/_strip_string_literals (and kql_guard's state machine) track only '/"
# quote parity and treat comment text as ordinary code. A stray '/" inside a '//' comment desyncs
# that parity counter -- the comment itself ends at the newline, but the desync persists into the
# NEXT line, so a call there (e.g. `| union database(...)`) gets silently blanked before the
# denied-operator deny-list ever sees it -- e.g. "T // it's\n| union database('SecretDB').SecretTable"
# passes the state machine untouched. KQL has no '/* */' block comments -- '//' is the only comment
# form. Agent read-only ad-hoc queries never legitimately need a comment, so we fail closed: reject
# any '//' on the raw text, before the fooled state machines run.
_LINE_COMMENT = re.compile(r"//")

# Cross-resource escapes + external reads + plugin surface, denied in BOTH KQL flavors
# (ADX/Eventhouse and Log Analytics), scanned AFTER blanking string literals so a literal can't
# false-reject. Word-boundary anchored so 'app(' can't match inside 'myapp('.
_DENIED_CALL = re.compile(r"\b(cluster|database|workspace|app)\s*\(", re.IGNORECASE)   # cross-resource
_DENIED_WORD = re.compile(r"\b(externaldata|external_table|evaluate)\b", re.IGNORECASE)  # ext-read / plugins


class FirewallRejection(Exception):
    """Raised when agent-authored KQL fails a static firewall stage. Carries a human ``reason``
    and a machine ``stage`` tag (length | verbatim-string | multiline-string | comment |
    multi-statement | control-command | denied-operator)."""

    def __init__(self, reason, stage):
        super().__init__(reason)
        self.reason = reason
        self.stage = stage


def validate_adhoc_kql(kql):
    """Return *kql* unchanged if it passes every static stage; else raise ``FirewallRejection``.
    Stages run in order, first failure wins: length -> verbatim-string -> multiline-string ->
    comment -> multi-statement -> control-command (delegated to ``assert_read_only_kql``: control
    commands + boolean tautology) -> denied-operator."""
    s = str(kql)

    # 1. length
    if len(s) > _MAX_ADHOC_LEN:
        raise FirewallRejection(
            f"query exceeds the {_MAX_ADHOC_LEN}-character ad-hoc limit", "length")

    # 2. verbatim strings — reject on the RAW text, before the state-machine-based stages below
    # (which model only regular strings and can be fooled by @"...\" into treating everything
    # after it as "inside a string"; see _VERBATIM_MARKER comment above).
    if _VERBATIM_MARKER.search(s):
        raise FirewallRejection(
            "verbatim string literals (@\"...\") are not allowed in ad-hoc queries — "
            "they defeat the read-only/deny-list parser; rephrase with a regular string",
            "verbatim-string")

    # 3. multiline (triple-backtick) strings — reject on the RAW text, same rationale as stage 2:
    # a stray '/" inside a backtick block desyncs the quote-parity state machines used below.
    if _BACKTICK.search(s):
        raise FirewallRejection(
            "backtick / triple-backtick multiline string literals are not allowed in ad-hoc "
            "queries — they defeat the read-only/deny-list parser; rephrase without backticks",
            "multiline-string")

    # 4. '//' line comments — reject on the RAW text, same rationale as stages 2-3: a stray '/"
    # inside a comment desyncs the quote-parity state machines used below, and the desync survives
    # past the comment's own newline. KQL has no '/* */' block comments.
    if _LINE_COMMENT.search(s):
        raise FirewallRejection(
            "comments (// ...) are not allowed in ad-hoc queries — a stray quote in a comment "
            "defeats the read-only/deny-list parser; remove the comment", "comment")

    # 5. single statement — a top-level ';' means first_statement truncated it (literals ignored).
    if first_statement(s) != s.rstrip():
        raise FirewallRejection(
            "multiple statements not allowed — submit a single read-only query", "multi-statement")

    # 6. read-only gate (control commands stacked via |/;/leading, boolean tautology, oversize).
    try:
        assert_read_only_kql(s)
    except ValueError as exc:
        raise FirewallRejection(str(exc), "control-command") from exc

    # 7. dangerous-operator deny-list (literals blanked first).
    code = _strip_string_literals(s)
    if _DENIED_CALL.search(code) or _DENIED_WORD.search(code):
        raise FirewallRejection(
            "query uses a denied operator (cross-cluster/database/workspace/app, externaldata, "
            "or evaluate) — not allowed in ad-hoc read-only queries", "denied-operator")

    return s

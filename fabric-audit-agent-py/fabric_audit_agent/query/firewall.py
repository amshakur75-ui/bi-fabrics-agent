"""Read-only ad-hoc KQL firewall (pure). Adapted from microsoft/fabric-rti-mcp + 4R9UN/mcp-kql-server
(MIT). Static rejection for AGENT-AUTHORED KQL — stricter than the trusted-seam guards in kql_guard:
a top-level ``;`` is REJECTED (never truncated), verbatim string literals (``@"..."``/``@'...'``)
and triple-backtick multiline string literals (```` ```...``` ````) are REJECTED outright (see
below), and a dangerous-operator deny-list closes the cross-resource / external-read escapes that
a read-only control-command gate doesn't cover.

The engine's own binder (take-0 rehearsal, in the run_kql handler) is the live-schema check; this
module is the cheap static pass that runs first. Pure: no I/O, no engine calls, deterministic.

KQL string-literal enumeration (auditable closure list — every form that can appear in a query,
and whether it can desync the ``'``/``"`` quote-parity state machine used by ``first_statement``/
``_strip_string_literals``/``assert_read_only_kql``):
  - regular ``'...'`` / ``"..."`` strings (with ``\\`` escapes) — MODELED by kql_guard's state
    machine; safe.
  - ``h``-prefixed "obfuscated" literals (``h"..."`` / ``h'...'``) — parse identically to regular
    quoted strings (the ``h`` is just a marker prefix); MODELED, safe.
  - VERBATIM strings (``@"..."`` / ``@'...'``) — backslash is a literal char and the string closes
    at the very next quote, a rule the state machine doesn't know; UNMODELED, REJECTED (prior fix,
    ``_VERBATIM_MARKER``).
  - MULTILINE triple-backtick strings (```` ```...``` ````) — backtick isn't tracked as a quote
    character at all, so a stray ``'``/``"`` inside one desyncs quote parity; UNMODELED, REJECTED
    here (``_BACKTICK``).
These four are KQL's complete set of string-literal forms; the last two are the only ones that can
desync the parity counter, so rejecting the ``@`` verbatim marker and any backtick closes the
"unmodeled-literal defeats the parser" bypass class in full."""
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

# Cross-resource escapes + external reads + plugin surface, denied in BOTH KQL flavors
# (ADX/Eventhouse and Log Analytics), scanned AFTER blanking string literals so a literal can't
# false-reject. Word-boundary anchored so 'app(' can't match inside 'myapp('.
_DENIED_CALL = re.compile(r"\b(cluster|database|workspace|app)\s*\(", re.IGNORECASE)   # cross-resource
_DENIED_WORD = re.compile(r"\b(externaldata|external_table|evaluate)\b", re.IGNORECASE)  # ext-read / plugins


class FirewallRejection(Exception):
    """Raised when agent-authored KQL fails a static firewall stage. Carries a human ``reason``
    and a machine ``stage`` tag (length | verbatim-string | multiline-string | multi-statement |
    control-command | denied-operator)."""

    def __init__(self, reason, stage):
        super().__init__(reason)
        self.reason = reason
        self.stage = stage


def validate_adhoc_kql(kql):
    """Return *kql* unchanged if it passes every static stage; else raise ``FirewallRejection``.
    Stages run in order, first failure wins: length -> verbatim-string -> multiline-string ->
    multi-statement -> control-command (delegated to ``assert_read_only_kql``: control commands +
    boolean tautology) -> denied-operator."""
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

    # 4. single statement — a top-level ';' means first_statement truncated it (literals ignored).
    if first_statement(s) != s.rstrip():
        raise FirewallRejection(
            "multiple statements not allowed — submit a single read-only query", "multi-statement")

    # 5. read-only gate (control commands stacked via |/;/leading, boolean tautology, oversize).
    try:
        assert_read_only_kql(s)
    except ValueError as exc:
        raise FirewallRejection(str(exc), "control-command") from exc

    # 6. dangerous-operator deny-list (literals blanked first).
    code = _strip_string_literals(s)
    if _DENIED_CALL.search(code) or _DENIED_WORD.search(code):
        raise FirewallRejection(
            "query uses a denied operator (cross-cluster/database/workspace/app, externaldata, "
            "or evaluate) — not allowed in ad-hoc read-only queries", "denied-operator")

    return s

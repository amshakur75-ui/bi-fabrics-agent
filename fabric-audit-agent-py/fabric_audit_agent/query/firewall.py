"""Read-only ad-hoc KQL firewall (pure). Adapted from microsoft/fabric-rti-mcp + 4R9UN/mcp-kql-server
(MIT). Static rejection for AGENT-AUTHORED KQL — stricter than the trusted-seam guards in kql_guard:
a top-level ``;`` is REJECTED (never truncated), and a dangerous-operator deny-list closes the
cross-resource / external-read escapes that a read-only control-command gate doesn't cover.

The engine's own binder (take-0 rehearsal, in the run_kql handler) is the live-schema check; this
module is the cheap static pass that runs first. Pure: no I/O, no engine calls, deterministic."""
import re

from .kql_guard import assert_read_only_kql, first_statement, _strip_string_literals

_MAX_ADHOC_LEN = 10_000

# Cross-resource escapes + external reads + plugin surface, denied in BOTH KQL flavors
# (ADX/Eventhouse and Log Analytics), scanned AFTER blanking string literals so a literal can't
# false-reject. Word-boundary anchored so 'app(' can't match inside 'myapp('.
_DENIED_CALL = re.compile(r"\b(cluster|database|workspace|app)\s*\(", re.IGNORECASE)   # cross-resource
_DENIED_WORD = re.compile(r"\b(externaldata|external_table|evaluate)\b", re.IGNORECASE)  # ext-read / plugins


class FirewallRejection(Exception):
    """Raised when agent-authored KQL fails a static firewall stage. Carries a human ``reason``
    and a machine ``stage`` tag (length | multi-statement | control-command | denied-operator)."""

    def __init__(self, reason, stage):
        super().__init__(reason)
        self.reason = reason
        self.stage = stage


def validate_adhoc_kql(kql):
    """Return *kql* unchanged if it passes every static stage; else raise ``FirewallRejection``.
    Stages run in order, first failure wins: length -> multi-statement -> control-command
    (delegated to ``assert_read_only_kql``: control commands + boolean tautology) -> denied-operator."""
    s = str(kql)

    # 1. length
    if len(s) > _MAX_ADHOC_LEN:
        raise FirewallRejection(
            f"query exceeds the {_MAX_ADHOC_LEN}-character ad-hoc limit", "length")

    # 2. single statement — a top-level ';' means first_statement truncated it (literals ignored).
    if first_statement(s) != s.rstrip():
        raise FirewallRejection(
            "multiple statements not allowed — submit a single read-only query", "multi-statement")

    # 3. read-only gate (control commands stacked via |/;/leading, boolean tautology, oversize).
    try:
        assert_read_only_kql(s)
    except ValueError as exc:
        raise FirewallRejection(str(exc), "control-command") from exc

    # 4. dangerous-operator deny-list (literals blanked first).
    code = _strip_string_literals(s)
    if _DENIED_CALL.search(code) or _DENIED_WORD.search(code):
        raise FirewallRejection(
            "query uses a denied operator (cross-cluster/database/workspace/app, externaldata, "
            "or evaluate) — not allowed in ad-hoc read-only queries", "denied-operator")

    return s

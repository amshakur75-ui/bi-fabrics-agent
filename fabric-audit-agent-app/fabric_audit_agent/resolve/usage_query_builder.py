"""usage_query_builder.py — deterministic, provenance-carrying usage-query builder.

Python port of the plugin's ``services/usage-query-builder.ts``.

WHY THIS EXISTS
    Field-usage, measure-usage, report/dataset lineage, model-dependency, and
    cross-model impact questions all reduce to the SAME shape: a
    ``PowerBIDatasetsWorkspace`` usage query whose WHERE clause is composed ONLY of
    authoritative filter fragments, grouped by a fixed set of safe columns. There is
    exactly one place a such query is assembled: here.

PROVENANCE GUARANTEE
    Every clause in the emitted query is traceable to an authoritative origin, and the
    builder emits a machine-readable provenance manifest alongside the query.

BRANDED FILTER (the runtime analog of the plugin's compile-time brand)
    ``authoritativeFilter`` must be an ``AuthoritativeFilter`` instance. Only
    ``mint_authoritative_filter`` (called by resolvers) can produce one — direct
    construction raises. The builder REJECTS a plain ``str`` (returns ``ok=False``),
    so a raw agent-supplied string can never be smuggled in as a filter.

The builder never constructs, interpolates, rewrites, normalizes, enriches, or falls
back to generating a filter fragment. If an input is not authoritative or not in the
safe enum, the builder REJECTS rather than repairs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

# PowerBIDatasetsWorkspace data-retention bound (constants.ts WORKSPACE_RETENTION_DAYS).
WORKSPACE_RETENTION_DAYS = 60


# ── Branded authoritative-filter type ─────────────────────────────────────────────
class AuthoritativeFilter:
    """A KQL filter fragment that only a resolver can mint.

    The runtime analog of the plugin's compile-time branded ``AuthoritativeFilter``
    string type. It cannot be constructed directly — ``mint_authoritative_filter`` is
    the ONLY sanctioned way to create one — so the "no free-text filter" rule is
    enforced structurally, not merely by convention.
    """

    __slots__ = ("_fragment",)
    # Private sentinel: only code holding this token may construct an instance.
    _MINT_TOKEN = object()

    def __init__(self, fragment: str, _token: object = None) -> None:
        if _token is not AuthoritativeFilter._MINT_TOKEN:
            raise TypeError(
                "AuthoritativeFilter cannot be constructed directly; a resolver must "
                "create one via mint_authoritative_filter()."
            )
        self._fragment = fragment

    @property
    def fragment(self) -> str:
        """The underlying KQL filter text."""
        return self._fragment

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self._fragment

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"AuthoritativeFilter({self._fragment!r})"


def mint_authoritative_filter(fragment: str) -> AuthoritativeFilter:
    """Mint an ``AuthoritativeFilter``. The ONLY sanctioned way to create one.

    Performs no transformation — it is a provenance stamp. Callers must only pass
    fragments they built themselves from authoritative sources; this function is never
    wired to any tool input, only resolver internals call it.
    """
    return AuthoritativeFilter(fragment, AuthoritativeFilter._MINT_TOKEN)


# ── Safe column vocabulary ─────────────────────────────────────────────────────────
# The fixed set of PowerBIDatasetsWorkspace columns that may appear in a groupBy or as
# an equality-filter column. Verified against getschema on ent-aas-workspace-prd
# (2026-07-28). "DatasetName" was REMOVED after that check — it is NOT a column on the
# table (ArtifactName is the dataset/artifact identifier). Do not add a column without
# confirming it against the live table schema first.
SAFE_USAGE_COLUMNS: Sequence[str] = (
    "ExecutingUser",
    "ArtifactName",
    "PowerBIWorkspaceName",
    "PowerBIWorkspaceId",
    "OperationName",
    "ApplicationName",
)

_SAFE_COLUMN_SET = frozenset(SAFE_USAGE_COLUMNS)
_USAGE_TABLE = "PowerBIDatasetsWorkspace"

# ── Provenance taxonomy ──────────────────────────────────────────────────────────
# Closed set of clause origins. Every emitted clause carries exactly one; there is no
# "unknown" origin — if the builder cannot attribute a clause, it does not emit it.
_CLAUSE_ORIGINS = frozenset(
    {"resolver", "artifact-inventory", "user-value", "builder-constant", "builder-derived"}
)
# Origins permitted for the VALUE of an equality filter.
_EQUALITY_VALUE_ORIGINS = frozenset({"artifact-inventory", "user-value"})

# Scope columns permitted for a workspace/artifact usage query.
_SCOPE_COLUMNS = frozenset({"ArtifactName", "PowerBIWorkspaceName"})


@dataclass
class ProvenanceEntry:
    """One traced clause: the exact text emitted, its origin, and an audit note."""

    clause: str
    origin: str
    note: str

    def to_dict(self) -> Dict[str, str]:
        return {"clause": self.clause, "origin": self.origin, "note": self.note}


@dataclass
class EqualityFilter:
    """An exact-match filter. ``origin`` must be 'artifact-inventory' or 'user-value'."""

    column: str
    value: str
    origin: str


@dataclass
class BuildUsageQueryResult:
    """Discriminated result: ``ok`` True carries query/provenance; False carries reason."""

    ok: bool
    query: Optional[str] = None
    provenance: Optional[List[ProvenanceEntry]] = None
    retention_warning: Optional[str] = None
    reason: Optional[str] = None


# ── KQL string escaping ────────────────────────────────────────────────────────────
def _kql_escape_double_quoted(value: str) -> str:
    r"""Escape a value for a KQL double-quoted string literal.

    Per the KQL spec: escape the backslash first (``\`` -> ``\\``) then the double quote
    (``"`` -> ``\"``). Order matters — escaping quotes first would corrupt the
    backslashes added afterward.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


_DURATION_RE = re.compile(r"^\d+[smhd]$")
_DURATION_PARTS_RE = re.compile(r"^(\d+)([smhd])$")


def _is_valid_kql_duration(s: str) -> bool:
    """A KQL timespan: an integer followed by s/m/h/d. Reject anything else."""
    return bool(_DURATION_RE.match(s))


def _timespan_to_days(timespan: str) -> float:
    """Express a validated timespan as fractional days (for retention comparison)."""
    m = _DURATION_PARTS_RE.match(timespan)
    if m is None:
        return 0.0
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return float(n)
    if unit == "h":
        return n / 24
    if unit == "m":
        return n / 1440
    if unit == "s":
        return n / 86400
    return 0.0


def _double_duration(timespan: str) -> str:
    """Double a validated KQL duration ("30d" -> "60d") for the compare window."""
    m = _DURATION_PARTS_RE.match(timespan)
    if m is None:
        return timespan
    return f"{int(m.group(1)) * 2}{m.group(2)}"


# ── Field-usage builder ──────────────────────────────────────────────────────────
def build_usage_query(
    authoritative_filter: AuthoritativeFilter,
    group_by: Sequence[str],
    timespan: str,
    equality_filters: Optional[Sequence[EqualityFilter]] = None,
    top_n: int = 5,
    title: Optional[str] = None,
) -> BuildUsageQueryResult:
    """Assemble a PowerBIDatasetsWorkspace usage query from authoritative inputs.

    Returns ``ok=True`` with query + provenance, or ``ok=False`` with a reason. Rejects
    (never repairs) on: a non-``AuthoritativeFilter`` / empty filter, empty/invalid
    groupBy, unknown columns, invalid equality-value origin, malformed timespan, or a
    non-positive topN.
    """
    equality_filters = list(equality_filters or [])

    # ── Validation: reject rather than guess ──
    # Reject a plain string (or anything not resolver-minted). This is the runtime
    # enforcement of the branded-type guarantee.
    if not isinstance(authoritative_filter, AuthoritativeFilter):
        return BuildUsageQueryResult(
            ok=False,
            reason=(
                "authoritativeFilter must be a resolver-minted AuthoritativeFilter, not "
                "a raw string — free-text filters are structurally forbidden."
            ),
        )
    fragment = authoritative_filter.fragment
    if not isinstance(fragment, str) or fragment.strip() == "":
        return BuildUsageQueryResult(
            ok=False,
            reason="authoritativeFilter is empty — a resolver must supply a non-empty fragment.",
        )
    if not isinstance(group_by, (list, tuple)) or len(group_by) == 0:
        return BuildUsageQueryResult(ok=False, reason="groupBy must contain at least one column.")
    for col in group_by:
        if col not in _SAFE_COLUMN_SET:
            return BuildUsageQueryResult(
                ok=False,
                reason=(
                    f"groupBy column '{col}' is not an allowed PowerBIDatasetsWorkspace "
                    f"column. Allowed: {', '.join(SAFE_USAGE_COLUMNS)}."
                ),
            )
    for f in equality_filters:
        if f.column not in _SAFE_COLUMN_SET:
            return BuildUsageQueryResult(
                ok=False,
                reason=f"equality-filter column '{f.column}' is not an allowed PowerBIDatasetsWorkspace column.",
            )
        if f.origin not in _EQUALITY_VALUE_ORIGINS:
            return BuildUsageQueryResult(
                ok=False,
                reason=(
                    f"equality-filter value for '{f.column}' has origin '{f.origin}', which is not "
                    "permitted. Value origins must be 'artifact-inventory' or 'user-value'."
                ),
            )
    if not _is_valid_kql_duration(timespan):
        return BuildUsageQueryResult(
            ok=False,
            reason=(
                f"timespan '{timespan}' is not a valid KQL duration (expected an integer followed "
                'by s, m, h, or d — e.g. "90d").'
            ),
        )
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        return BuildUsageQueryResult(
            ok=False, reason=f"topN must be a positive integer (received {top_n})."
        )

    # ── Retention awareness: warn, never clamp ──
    requested_days = _timespan_to_days(timespan)
    retention_warning: Optional[str] = None
    if requested_days > WORKSPACE_RETENTION_DAYS:
        retention_warning = (
            f"Requested timespan ({timespan}) exceeds workspace retention "
            f"({WORKSPACE_RETENTION_DAYS}d). Results cover only the available "
            f"{WORKSPACE_RETENTION_DAYS}-day window."
        )

    # ── Assembly: deterministic, provenance-tracked ──
    provenance: List[ProvenanceEntry] = []
    lines: List[str] = []

    def emit(clause: str, origin: str, note: str) -> None:
        provenance.append(ProvenanceEntry(clause=clause, origin=origin, note=note))
        lines.append(clause)

    if title is not None and title.strip() != "":
        # "//" only comments out a single line, so strip line breaks: a title
        # containing "\n" would otherwise inject live KQL after the comment.
        safe_title = re.sub(r"[\r\n]+", " ", title).strip()
        emit(f"// {safe_title}", "user-value", "title comment — not executed")

    if retention_warning is not None:
        emit(
            f"// NOTE: {retention_warning}",
            "builder-derived",
            f"retention warning: timespan '{timespan}' exceeds PowerBIDatasetsWorkspace "
            f"retention ({WORKSPACE_RETENTION_DAYS}d)",
        )

    emit(_USAGE_TABLE, "builder-constant", "fixed usage table for all Power BI usage/lineage queries")

    emit(
        f"| where TimeGenerated > ago({timespan})",
        "builder-derived",
        f"timespan '{timespan}' validated to KQL duration form",
    )

    for f in equality_filters:
        escaped = _kql_escape_double_quoted(f.value)
        emit(
            f'| where {f.column} == "{escaped}"',
            f.origin,
            "column from safe enum; value escaped per KQL string spec and embedded as a literal only",
        )

    # The authoritative filter is embedded verbatim, parenthesized so its internal `or`
    # chain cannot bleed into surrounding clauses.
    emit(
        f"| where ({fragment})",
        "resolver",
        "embedded verbatim from resolver output (branded AuthoritativeFilter)",
    )

    emit(
        f"| summarize QueryCount = count() by {', '.join(group_by)}",
        "builder-derived",
        "groupBy columns all validated against the safe enum",
    )

    emit(
        f"| top {top_n} by QueryCount desc",
        "builder-derived",
        "topN validated as a positive integer",
    )

    return BuildUsageQueryResult(
        ok=True,
        query="\n".join(lines),
        provenance=provenance,
        retention_warning=retention_warning,
    )


# ── Workspace/artifact usage builder ────────────────────────────────────────────
def build_workspace_usage_query(
    scope: EqualityFilter,
    timespan: str,
    group_by: Optional[Sequence[str]] = None,
    compare_periods: bool = False,
    top_n: int = 10,
    title: Optional[str] = None,
) -> BuildUsageQueryResult:
    """Assemble a workspace/artifact adoption query.

    Same reject-never-repair contract as ``build_usage_query``. The summarize ALWAYS
    includes ``DistinctUsers`` and (single-window only) ``LastUsed``, because adoption
    judgments need user counts and staleness, not just raw query volume. With
    ``compare_periods`` the query scans 2x the window and tags rows Period=current|prior.
    """
    group_by = list(group_by or [])

    if scope.column not in _SCOPE_COLUMNS:
        return BuildUsageQueryResult(
            ok=False, reason=f"scope column '{scope.column}' must be ArtifactName or PowerBIWorkspaceName."
        )
    if scope.origin not in _EQUALITY_VALUE_ORIGINS:
        return BuildUsageQueryResult(
            ok=False,
            reason=(
                f"scope value origin '{scope.origin}' is not permitted. Must be "
                "'artifact-inventory' or 'user-value'."
            ),
        )
    if not isinstance(scope.value, str) or scope.value.strip() == "":
        return BuildUsageQueryResult(
            ok=False, reason="scope value is empty — supply the artifact or workspace name."
        )
    for col in group_by:
        if col not in _SAFE_COLUMN_SET:
            return BuildUsageQueryResult(
                ok=False,
                reason=(
                    f"groupBy column '{col}' is not an allowed PowerBIDatasetsWorkspace column. "
                    f"Allowed: {', '.join(SAFE_USAGE_COLUMNS)}."
                ),
            )
    if not _is_valid_kql_duration(timespan):
        return BuildUsageQueryResult(
            ok=False,
            reason=(
                f"timespan '{timespan}' is not a valid KQL duration (expected an integer followed "
                'by s, m, h, or d — e.g. "30d").'
            ),
        )
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        return BuildUsageQueryResult(
            ok=False, reason=f"topN must be a positive integer (received {top_n})."
        )

    # Retention: a compare scans 2x the window, so gate on the doubled span.
    scan_span = _double_duration(timespan) if compare_periods else timespan
    scan_days = _timespan_to_days(scan_span)
    retention_warning: Optional[str] = None
    if scan_days > WORKSPACE_RETENTION_DAYS:
        if compare_periods:
            retention_warning = (
                f"A {timespan}-vs-{timespan} comparison scans {scan_span}, which exceeds workspace "
                f"retention ({WORKSPACE_RETENTION_DAYS}d). The prior period will be partially or fully "
                f"empty — shorten the window (e.g. 30d vs 30d fits in {WORKSPACE_RETENTION_DAYS}d retention)."
            )
        else:
            retention_warning = (
                f"Requested timespan ({timespan}) exceeds workspace retention "
                f"({WORKSPACE_RETENTION_DAYS}d). Results cover only the available "
                f"{WORKSPACE_RETENTION_DAYS}-day window."
            )

    provenance: List[ProvenanceEntry] = []
    lines: List[str] = []

    def emit(clause: str, origin: str, note: str) -> None:
        provenance.append(ProvenanceEntry(clause=clause, origin=origin, note=note))
        lines.append(clause)

    if title is not None and title.strip() != "":
        safe_title = re.sub(r"[\r\n]+", " ", title).strip()
        emit(f"// {safe_title}", "user-value", "title comment — not executed")
    if retention_warning is not None:
        emit(
            f"// NOTE: {retention_warning}",
            "builder-derived",
            f"retention warning: scan span '{scan_span}' vs retention {WORKSPACE_RETENTION_DAYS}d",
        )

    emit(_USAGE_TABLE, "builder-constant", "fixed usage table for all Power BI usage/lineage queries")
    emit(
        f"| where TimeGenerated > ago({scan_span})",
        "builder-derived",
        (
            f"doubled window ({timespan} current + {timespan} prior), duration validated"
            if compare_periods
            else f"timespan '{timespan}' validated to KQL duration form"
        ),
    )

    escaped_scope = _kql_escape_double_quoted(scope.value)
    emit(
        f'| where {scope.column} == "{escaped_scope}"',
        scope.origin,
        "scope column from safe enum; value escaped per KQL string spec and embedded as a literal only",
    )

    by_columns: List[str] = []
    if compare_periods:
        emit(
            f'| extend Period = iff(TimeGenerated > ago({timespan}), "current", "prior")',
            "builder-derived",
            f"two-window tag from the validated timespan '{timespan}'",
        )
        by_columns.append("Period")
    by_columns.extend(group_by)

    aggregates = (
        "QueryCount = count(), DistinctUsers = dcount(ExecutingUser)"
        if compare_periods
        else "QueryCount = count(), DistinctUsers = dcount(ExecutingUser), LastUsed = max(TimeGenerated)"
    )
    by_clause = f" by {', '.join(by_columns)}" if by_columns else ""
    emit(
        f"| summarize {aggregates}{by_clause}",
        "builder-derived",
        "fixed adoption aggregates; by-columns validated against the safe enum (plus builder-owned Period)",
    )

    if len(group_by) > 0:
        emit(f"| top {top_n} by QueryCount desc", "builder-derived", "topN validated as a positive integer")
    elif compare_periods:
        emit("| order by Period asc", "builder-constant", "stable current/prior ordering for period comparison")

    return BuildUsageQueryResult(
        ok=True,
        query="\n".join(lines),
        provenance=provenance,
        retention_warning=retention_warning,
    )


def format_provenance(provenance: Sequence[ProvenanceEntry]) -> str:
    """Render a provenance manifest as a compact, human-readable audit block.

    First-class tool output (tightening 26t): the audit trail confirming no improvised
    filters were introduced — not optional decoration. Pure; no side effects.
    """
    header = "Provenance (every clause traced to an authoritative origin):"
    rows = []
    for p in provenance:
        first_line = p.clause.split("\n")[0] if p.clause else p.clause
        rows.append(f"  [{p.origin}] {first_line}\n      ↳ {p.note}")
    return "\n".join([header, *rows])

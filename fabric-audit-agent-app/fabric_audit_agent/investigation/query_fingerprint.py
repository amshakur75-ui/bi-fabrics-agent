"""Query-SHAPE fingerprinting (KQL/DAX/MDX). tightening.md Part 1b (Sub-plan 1 of the alerting
redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``).

"The same expensive query SHAPE (e.g. nested Hierarchize/CrossJoin, or a recurring DAX pattern)
recurring across days from DIFFERENT users points at a model/report design problem, not a person
problem." (Part 12 Category 4.) This module produces the stable shape identity two differently-
parameterized executions of the same query collapse onto: strip string/numeric literals, GUIDs,
and ISO dates/timestamps, collapse whitespace, lowercase -- but KEEP every operator/function/
structure token (SUMX, FILTER, CALCULATE, Hierarchize, CrossJoin, |, summarize, join, ...), since
those are exactly what makes two queries the "same shape".

Pure stdlib, deterministic: no clock, no randomness -- identical structure with different literals
MUST hash identically; different structure MUST differ.
"""
import hashlib
import re

# ISO date/timestamp: 2026-08-07 or 2026-08-07T06:00:00(.123)?(Z|+00:00)? -- must run BEFORE the
# bare-number pattern below, since a date is itself a run of digits and separators.
_ISO_DATETIME_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
# GUID: 8-4-4-4-12 hex, with or without braces.
_GUID_RE = re.compile(
    r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?"
)
# String literals: 'single' or "double" quoted, non-greedy, tolerating no embedded escapes (the
# common case in KQL/DAX/MDX query text).
_STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# Bare numeric literal (int/float, optional sign/exponent) once dates/GUIDs are already gone.
_NUMBER_RE = re.compile(r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_shape(query_text):
    """Normalize ``query_text`` to its structural shape: literals stripped, whitespace collapsed,
    lowercased. Returns ``""`` for ``None``/empty/whitespace-only input. Pure.

    Exposed separately from :func:`fingerprint` so a caller can show WHY two queries share a shape
    (e.g. render the normalized form as evidence) rather than just the opaque hash.
    """
    if not query_text or not str(query_text).strip():
        return ""
    text = str(query_text)
    text = _ISO_DATETIME_RE.sub("?", text)
    text = _GUID_RE.sub("?", text)
    text = _STRING_LITERAL_RE.sub("?", text)
    text = _NUMBER_RE.sub("?", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.lower()


def fingerprint(query_text):
    """Return a short stable hex shape-hash for ``query_text``, or ``None`` for empty/None input.

    Two queries with identical structure but different literal values (strings, numbers, GUIDs,
    dates) hash identically; queries with different structure hash differently. Uses
    ``hashlib.sha1`` of the normalized form, first 16 hex chars -- a fingerprint, not a security
    boundary.
    """
    shape = normalize_shape(query_text)
    if not shape:
        return None
    return hashlib.sha1(shape.encode("utf-8")).hexdigest()[:16]

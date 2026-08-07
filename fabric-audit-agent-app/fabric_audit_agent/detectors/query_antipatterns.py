"""Query anti-pattern detector. tightening.md Part 12 Category 2 (Sub-plan 2 of the alerting
redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``,
Task 2b).

The flagship shape: the same expensive MDX query SHAPE (nested Hierarchize / CrossJoin /
GrandTotal — a heavy matrix-visual pattern) recurring across many events indicates a
model/report design problem worth flagging ONCE, not once per event. This mirrors
``detectors/query_shape.py``'s recurring-shape philosophy but targets specific KNOWN-EXPENSIVE
query shapes (MDX matrix cross-joins, DAX nested iterators / whole-table FILTER) rather than
"any shape that recurs a lot".

Contract: reads ``facts["events"]``, a list of normalize_event-shaped dicts (see
``investigation/events.py``: ``user``, ``item``, ``operation``, ``queryText``, ...) -- same
source ``detectors/absolute_cost.py`` / ``detectors/query_shape.py`` read. Events with no
usable ``queryText`` are skipped. Pure Log Analytics fact, zero capacity data: this detector
never reads ``facts["capacity"]`` and never computes or mentions a capacity percentage.

Dedupe: groups matches by (pattern-id, shape-fingerprint) using
``investigation.query_fingerprint.fingerprint`` (the same normalize-and-hash used by
``query_shape.py``) so the same anti-pattern shape recurring across many events emits ONE
finding with an ``occurrences`` count, not one finding per event.

Note (Task 2b step 1d): grepping the repo for a storage-engine-query-count-style field on
events (``storageEngineQueries`` / ``storage_engine`` / "SE query" / similar) turned up no such
field anywhere in code -- ``normalize_event`` (``investigation/events.py``) emits only ``ts``,
``user``, ``item``, ``workspace``, ``operation``, ``operationDetail``, ``kind``, ``cuSeconds``,
``durationMs``, ``throttled``, ``queryText``. So a "high storage-engine query count" rule is
skipped as specified -- there is no fact to detect it from.
"""
import re

from ..config import DEFAULT_CONFIG
from ..dax import analyze_dax
from ..investigation.query_fingerprint import fingerprint

_HIERARCHIZE_RE = re.compile(r"\bHierarchize\b", re.I)
_CROSSJOIN_RE = re.compile(r"\bCrossJoin\b", re.I)
_GRANDTOTAL_RE = re.compile(r"\bGrandTotal\b", re.I)


def _mdx_crossjoin_patterns(query_text):
    """Return the list of matched substring/pattern names for the MDX matrix cross-join shape,
    or ``[]`` if the query text doesn't match. Case-insensitive; matches Hierarchize + CrossJoin,
    or GrandTotal + CrossJoin (the heavy matrix-visual combination)."""
    has_hierarchize = bool(_HIERARCHIZE_RE.search(query_text))
    has_crossjoin = bool(_CROSSJOIN_RE.search(query_text))
    has_grandtotal = bool(_GRANDTOTAL_RE.search(query_text))

    matched = []
    if has_hierarchize and has_crossjoin:
        matched.extend(["Hierarchize", "CrossJoin"])
    if has_grandtotal and has_crossjoin:
        matched.extend(["GrandTotal", "CrossJoin"])
    # de-dupe while preserving order (e.g. CrossJoin only listed once even if both branches hit)
    seen = set()
    out = []
    for p in matched:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def detect_query_antipatterns(facts, config=None):
    config = config or DEFAULT_CONFIG
    facts = facts or {}
    events = facts.get("events") or []

    # groups[(pattern_id, shape_hash)] -> list of (event, matched_patterns)
    groups = {}
    for ev in events:
        query_text = ev.get("queryText")
        if not query_text:
            continue

        shape_hash = fingerprint(query_text)
        if shape_hash is None:
            continue

        mdx_patterns = _mdx_crossjoin_patterns(query_text)
        if mdx_patterns:
            key = ("query.mdx-crossjoin", shape_hash)
            groups.setdefault(key, []).append((ev, mdx_patterns))

        dax_patterns = analyze_dax(query_text)
        if dax_patterns:
            key = ("query.dax-antipattern", shape_hash)
            pattern_names = [p["pattern"] for p in dax_patterns]
            groups.setdefault(key, []).append((ev, pattern_names))

    flags = []
    for (finding_id, shape_hash), matches in groups.items():
        occurrences = len(matches)
        sample_ev, sample_patterns = matches[0]
        users = sorted({ev.get("user") for ev, _ in matches if ev.get("user")})
        items = sorted({ev.get("item") for ev, _ in matches if ev.get("item")})
        # union of every pattern name seen across occurrences of this group, order-preserving
        all_patterns = []
        seen_patterns = set()
        for _, pats in matches:
            for p in pats:
                if p not in seen_patterns:
                    seen_patterns.add(p)
                    all_patterns.append(p)

        sample_item = sample_ev.get("item") or "unknown item"
        sample_user = sample_ev.get("user") or "unknown user"
        sample_operation = sample_ev.get("operation") or "operation"
        sample_query_text = sample_ev.get("queryText")

        if finding_id == "query.mdx-crossjoin":
            what = (f"An expensive MDX matrix cross-join shape ({', '.join(all_patterns)}) ran "
                     f"{occurrences} time(s) (e.g. on \"{sample_item}\") — a heavy nested "
                     f"Hierarchize/CrossJoin matrix-visual pattern is a model/report design "
                     f"issue worth fixing once, not a one-off slow query.")
        else:
            what = (f"A recurring DAX anti-pattern ({', '.join(all_patterns)}) ran {occurrences} "
                     f"time(s) (e.g. on \"{sample_item}\") — likely a shared measure/report "
                     f"design issue.")

        flags.append({
            "type": finding_id,
            "resource": sample_item,
            "when": sample_ev.get("ts") or "",
            "evidence": {
                "item": sample_item,
                "user": sample_user,
                "operation": sample_operation,
                "sampleQueryText": sample_query_text,
                "patterns": all_patterns,
                "shapeHash": shape_hash,
                "occurrences": occurrences,
                "distinctUsers": len(users),
                "users": users[:5],
                "items": items[:5],
            },
            "what": what,
        })
    return flags

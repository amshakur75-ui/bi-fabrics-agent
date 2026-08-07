"""Query-SHAPE recurrence detector. tightening.md Part 1b / Part 12 Category 4 (Sub-plan 1 of the
alerting redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``).

"The same expensive query SHAPE (e.g. nested Hierarchize/CrossJoin, or a recurring DAX pattern)
recurring across days from DIFFERENT users points at a model/report design problem, not a person
problem." This is the flagship missing detector -- it clusters events by
``investigation.query_fingerprint.fingerprint`` and flags a shape that recurs across MULTIPLE
distinct users, which rules out "one person wrote one bad query" and points at the shared
report/model design instead. Pure Log Analytics fact, zero capacity data: this detector never
reads ``facts["capacity"]`` and never computes or mentions a capacity percentage.

Contract: reads ``facts["events"]``, a list of normalize_event-shaped dicts (see
``investigation/events.py``: ``user``, ``item``, ``operation``, ``durationMs``, ``cuSeconds``,
``queryText``, ...) -- the same source ``detectors/absolute_cost.py`` reads, for consistency.
Events with no usable ``queryText`` are skipped (fingerprint returns ``None`` for them).

NOTE (traced 2026-08-07): as of this writing, nothing in ``pipeline.py`` /
``adapters/collector_merge.py`` populates ``facts["events"]`` -- same gap ``absolute_cost.py``
documents. This detector is correctly implemented but will not fire in the production pipeline
until a caller wires raw events onto ``facts["events"]``. See the report for this task.
"""
from ..config import DEFAULT_CONFIG
from ..investigation.query_fingerprint import fingerprint, normalize_shape


def detect_query_shape(facts, config=None):
    config = config or DEFAULT_CONFIG
    facts = facts or {}
    events = facts.get("events") or []
    thr = (config.get("activity") or DEFAULT_CONFIG["activity"])
    min_count = thr["recurringShapeMinCount"] if thr.get("recurringShapeMinCount") is not None else DEFAULT_CONFIG["activity"]["recurringShapeMinCount"]
    min_users = thr["recurringShapeMinUsers"] if thr.get("recurringShapeMinUsers") is not None else DEFAULT_CONFIG["activity"]["recurringShapeMinUsers"]

    groups = {}   # shapeHash -> list[event]
    for ev in events:
        query_text = ev.get("queryText")
        if not query_text:
            continue
        shape_hash = fingerprint(query_text)
        if shape_hash is None:
            continue
        groups.setdefault(shape_hash, []).append(ev)

    flags = []
    for shape_hash, group_events in groups.items():
        occurrences = len(group_events)
        users = sorted({ev.get("user") for ev in group_events if ev.get("user")})
        distinct_users = len(users)
        if occurrences < min_count or distinct_users < min_users:
            continue

        sample = group_events[0]
        sample_item = sample.get("item") or "unknown item"
        sample_query_text = sample.get("queryText")

        flags.append({
            "type": "activity.recurring-shape",
            "resource": sample_item,
            "when": sample.get("ts") or "",
            "evidence": {
                "shapeHash": shape_hash,
                "occurrences": occurrences,
                "distinctUsers": distinct_users,
                "users": users[:5],
                "sampleItem": sample.get("item"),
                "sampleQueryText": sample_query_text,
                "normalizedShape": normalize_shape(sample_query_text),
            },
            "what": (f"A recurring query shape ran {occurrences} times across {distinct_users} "
                     f"users (e.g. on \"{sample_item}\") — likely a shared report/model design "
                     f"issue, not one person."),
        })
    return flags

from fabric_audit_agent.detectors.query_shape import detect_query_shape

_SHAPE_QUERY = "EVALUATE SUMMARIZE(Sales, Sales[Region], \"Total\", SUM(Sales[Amount]))"
_OTHER_QUERY = "EVALUATE FILTER(Sales, Sales[Region] = \"East\")"


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "durationMs": 1000, "cuSeconds": 1.0, "queryText": _SHAPE_QUERY}
    ev.update(overrides)
    return ev


def _varied(query_text, n, users, item="Sales Model"):
    """n events with the same *shape* but different literals, spread across the given users."""
    events = []
    for i in range(n):
        user = users[i % len(users)]
        # Vary a literal (the numeric total label) so shape-equivalence is actually exercised.
        varied_text = query_text.replace("Total", f"Total{i}")
        events.append(_event(queryText=varied_text, user=user, item=item))
    return events


def test_fires_at_min_count_and_min_users():
    events = _varied(_SHAPE_QUERY, 3, ["alice@corp.com", "bob@corp.com", "carol@corp.com"])
    flags = detect_query_shape({"events": events})
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.recurring-shape"
    assert f["evidence"]["occurrences"] == 3
    assert f["evidence"]["distinctUsers"] == 3
    assert set(f["evidence"]["users"]) == {"alice@corp.com", "bob@corp.com", "carol@corp.com"}


def test_does_not_fire_for_single_user_even_above_min_count():
    events = _varied(_SHAPE_QUERY, 5, ["alice@corp.com"])
    assert detect_query_shape({"events": events}) == []


def test_does_not_fire_below_min_count():
    events = _varied(_SHAPE_QUERY, 2, ["alice@corp.com", "bob@corp.com"])
    assert detect_query_shape({"events": events}) == []


def test_events_without_query_text_are_skipped():
    events = _varied(_SHAPE_QUERY, 2, ["alice@corp.com", "bob@corp.com"])
    events.append(_event(queryText=None, user="carol@corp.com"))
    events.append(_event(queryText="", user="dave@corp.com"))
    assert detect_query_shape({"events": events}) == []


def test_different_shapes_do_not_cross_contaminate():
    same_shape = _varied(_SHAPE_QUERY, 3, ["alice@corp.com", "bob@corp.com", "carol@corp.com"])
    other_shape = [_event(queryText=_OTHER_QUERY, user="dave@corp.com")]
    flags = detect_query_shape({"events": same_shape + other_shape})
    assert len(flags) == 1
    assert flags[0]["evidence"]["occurrences"] == 3


def test_flag_carries_no_capacity_percentage_key():
    events = _varied(_SHAPE_QUERY, 3, ["alice@corp.com", "bob@corp.com", "carol@corp.com"])
    f = detect_query_shape({"events": events})[0]
    evidence_keys = set(f["evidence"].keys())
    assert not any("pct" in k.lower() or "capacity" in k.lower() for k in evidence_keys)
    assert "capacity" not in f["what"].lower()
    assert "%" not in f["what"]


def test_empty_events_no_flags():
    assert detect_query_shape({}) == []
    assert detect_query_shape({"events": []}) == []
    assert detect_query_shape(None) == []


def test_custom_thresholds_from_config():
    events = _varied(_SHAPE_QUERY, 2, ["alice@corp.com", "bob@corp.com"])
    config = {"activity": {"recurringShapeMinCount": 2, "recurringShapeMinUsers": 2}}
    flags = detect_query_shape({"events": events}, config)
    assert len(flags) == 1


def test_users_list_capped_at_five_in_evidence():
    users = [f"user{i}@corp.com" for i in range(7)]
    events = _varied(_SHAPE_QUERY, 7, users)
    f = detect_query_shape({"events": events})[0]
    assert f["evidence"]["distinctUsers"] == 7
    assert len(f["evidence"]["users"]) == 5

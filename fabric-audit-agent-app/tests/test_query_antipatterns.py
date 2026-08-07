from fabric_audit_agent.detectors.query_antipatterns import detect_query_antipatterns

_MDX_CROSSJOIN_QUERY = (
    "SELECT NON EMPTY Hierarchize(CrossJoin({[Date].[Calendar Year].Members}, "
    "{[Product].[Category].Members})) ON ROWS, {[Measures].[Sales Amount]} ON COLUMNS "
    "FROM [Model] WHERE ([Region].[All])"
)
_BENIGN_QUERY = "EVALUATE SUMMARIZE(Sales, Sales[Region], \"Total\", SUM(Sales[Amount]))"
_NESTED_SUMX_QUERY = (
    "EVALUATE ROW(\"X\", SUMX(Sales, SUMX(RELATEDTABLE(SalesDetail), SalesDetail[Amount])))"
)


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "durationMs": 1000, "cuSeconds": 1.0, "queryText": _MDX_CROSSJOIN_QUERY}
    ev.update(overrides)
    return ev


def _varied(query_text, n, users, item="Sales Model", **kw):
    """n events with the same *shape* but different literals, spread across the given users.
    Vary a quoted string literal (stripped by normalize_shape's STRING_LITERAL regex) so the
    fingerprint stays identical across events while the raw queryText differs."""
    events = []
    for i in range(n):
        user = users[i % len(users)]
        varied_text = f"{query_text} -- run '{i}'"
        events.append(_event(queryText=varied_text, user=user, item=item, **kw))
    return events


def test_mdx_crossjoin_fires():
    events = [_event(queryText=_MDX_CROSSJOIN_QUERY)]
    flags = detect_query_antipatterns({"events": events})
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "query.mdx-crossjoin"
    assert "Hierarchize" in f["evidence"]["patterns"]
    assert "CrossJoin" in f["evidence"]["patterns"]
    assert f["evidence"]["item"] == "Sales Model"
    assert f["evidence"]["user"] == "alice@corp.com"
    assert f["evidence"]["operation"] == "QueryEnd"
    assert f["evidence"]["sampleQueryText"] == _MDX_CROSSJOIN_QUERY


def test_benign_query_does_not_fire():
    events = [_event(queryText=_BENIGN_QUERY)]
    assert detect_query_antipatterns({"events": events}) == []


def test_nested_sumx_fires_dax_antipattern_via_analyze_dax():
    events = [_event(queryText=_NESTED_SUMX_QUERY)]
    flags = detect_query_antipatterns({"events": events})
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "query.dax-antipattern"
    # pattern name must come straight from dax.analyze_dax's own id string, not reinvented.
    assert "nested-iterators" in f["evidence"]["patterns"]


def test_repeated_shape_dedupes_to_one_finding_with_occurrence_count():
    events = _varied(_MDX_CROSSJOIN_QUERY, 12, ["alice@corp.com", "bob@corp.com", "carol@corp.com"])
    flags = detect_query_antipatterns({"events": events})
    mdx_flags = [f for f in flags if f["type"] == "query.mdx-crossjoin"]
    assert len(mdx_flags) == 1
    assert mdx_flags[0]["evidence"]["occurrences"] == 12
    assert mdx_flags[0]["evidence"]["distinctUsers"] == 3


def test_events_missing_query_text_are_skipped_without_error():
    events = [
        _event(queryText=None),
        _event(queryText=""),
        {"ts": "2026-08-07T06:00:00Z", "user": "dave@corp.com", "item": "Sales Model", "operation": "QueryEnd"},
    ]
    assert detect_query_antipatterns({"events": events}) == []
    assert detect_query_antipatterns({}) == []
    assert detect_query_antipatterns(None) == []


def test_no_finding_has_capacity_percentage_key():
    events = [_event(queryText=_MDX_CROSSJOIN_QUERY), _event(queryText=_NESTED_SUMX_QUERY)]
    flags = detect_query_antipatterns({"events": events})
    assert len(flags) == 2
    for f in flags:
        evidence_keys = set(f["evidence"].keys())
        assert not any("pct" in k.lower() or "capacity" in k.lower() for k in evidence_keys)
        assert "capacity" not in f["what"].lower()
        assert "%" not in f["what"]

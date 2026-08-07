from fabric_audit_agent.investigation.query_fingerprint import fingerprint, normalize_shape


def test_none_and_empty_return_none():
    assert fingerprint(None) is None
    assert fingerprint("") is None
    assert fingerprint("   ") is None


def test_stable_across_literal_only_changes():
    a = fingerprint("EVALUATE FILTER('Sales', 'Sales'[Amount] > 100)")
    b = fingerprint("EVALUATE FILTER('Orders', 'Orders'[Amount] > 999999)")
    assert a is not None and b is not None
    assert a == b


def test_stable_across_string_literal_changes():
    a = fingerprint("| where UserId == '11111111-1111-1111-1111-111111111111'")
    b = fingerprint("| where UserId == '22222222-2222-2222-2222-222222222222'")
    assert a == b


def test_stable_across_date_literal_changes():
    a = fingerprint("| where TimeGenerated > datetime(2026-08-01T00:00:00Z)")
    b = fingerprint("| where TimeGenerated > datetime(2026-08-07T06:00:00.123Z)")
    assert a == b


def test_distinct_across_structural_changes():
    a = fingerprint("EVALUATE SUMMARIZE(Sales, Sales[Region])")
    b = fingerprint("EVALUATE FILTER(Sales, Sales[Region] = \"East\")")
    assert a != b


def test_distinct_for_nested_iterators_vs_flat():
    flat = fingerprint("EVALUATE SUMX(Sales, Sales[Amount])")
    nested = fingerprint("EVALUATE SUMX(Sales, SUMX(Orders, Orders[Amount]))")
    assert flat != nested


def test_hash_is_short_hex():
    h = fingerprint("EVALUATE Sales")
    assert h is not None
    assert len(h) == 16
    int(h, 16)   # raises if not valid hex


def test_no_clock_or_random_determinism():
    text = "SELECT Hierarchize(CrossJoin([Date].[Calendar], [Product].[Category]))"
    assert fingerprint(text) == fingerprint(text)


def test_normalize_shape_empty_is_empty_string():
    assert normalize_shape(None) == ""
    assert normalize_shape("") == ""


def test_normalize_shape_keeps_structural_tokens():
    shape = normalize_shape("EVALUATE SUMX(Sales, CALCULATE(SUM(Sales[Amount])))")
    assert "sumx" in shape
    assert "calculate" in shape
    assert "sum" in shape

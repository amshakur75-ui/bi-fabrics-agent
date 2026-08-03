"""Pure foundation for the query-library growth loop: the [adhoc-kql] audit-log parser, the
shape canonicalizer, candidate ranking, and library-entry projection. No I/O, no CLI yet
(later task)."""
import hashlib
import json

import fabric_audit_agent.query.mine as mine_module
from fabric_audit_agent.query.firewall import validate_adhoc_kql
from fabric_audit_agent.query.mine import (
    parse_audit_lines,
    rank_candidates,
    shape_key,
    to_library_entries,
)


def _line(engine="capacity", verdict="allowed", kql="CapacityEvents | take 50", **extra):
    rec = {"tag": "adhoc-kql", "engine": engine, "verdict": verdict}
    rec.update(extra)
    if kql is not None:
        rec["kql"] = kql
    return "[adhoc-kql] " + json.dumps(rec, ensure_ascii=False, separators=(",", ": "))


# ---------------------------------------------------------------------------
# parse_audit_lines
# ---------------------------------------------------------------------------

def test_parse_extracts_allowed_capacity_record():
    line = _line(
        engine="capacity", verdict="allowed", rowCount=12,
        kql="CapacityEvents\n| where ingestion_time() > ago(1d)\n| take 50",
    )
    recs = parse_audit_lines([line])
    assert len(recs) == 1
    assert recs[0]["engine"] == "capacity"
    assert recs[0]["verdict"] == "allowed"
    assert recs[0]["rowCount"] == 12


def test_parse_extracts_allowed_la_record():
    line = _line(
        engine="la", verdict="allowed",
        kql="PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n| take 20",
    )
    recs = parse_audit_lines([line])
    assert len(recs) == 1
    assert recs[0]["engine"] == "la"


def test_parse_skips_rejected_records():
    line = _line(
        engine="capacity", verdict="rejected", stage="denied-operator", reason="denied op",
        kql="CapacityEvents | union database('x').T",
    )
    assert parse_audit_lines([line]) == []


def test_parse_skips_non_marker_lines_without_raising():
    lines = ["just a regular log line", "another line with no marker at all", ""]
    assert parse_audit_lines(lines) == []


def test_parse_skips_malformed_json_without_raising():
    lines = [
        "[adhoc-kql] {this is not valid json",
        "[adhoc-kql] ",
        "[adhoc-kql] [1, 2, 3]",   # valid json, but not a dict -- must be skipped, not raise
        "[adhoc-kql] 42",          # valid json scalar -- must be skipped, not raise
    ]
    assert parse_audit_lines(lines) == []


def test_parse_drops_unknown_engine():
    line = _line(engine="sql", verdict="allowed", kql="SELECT 1")
    assert parse_audit_lines([line]) == []


def test_parse_extracts_line_with_logger_prefix_before_marker():
    raw = _line(engine="capacity", verdict="allowed", kql="CapacityEvents\n| take 50")
    line = "2026-07-08 12:00:00 INFO app.audit " + raw
    recs = parse_audit_lines([line])
    assert len(recs) == 1
    assert recs[0]["engine"] == "capacity"


def test_parse_accepts_any_iterable_of_strings_not_just_a_list():
    line = _line(engine="capacity", verdict="allowed", kql="CapacityEvents\n| take 50")

    def gen():
        yield line
        yield "not a marker line"

    recs = parse_audit_lines(gen())
    assert len(recs) == 1


def test_parse_returns_multiple_allowed_records_in_order():
    l1 = _line(engine="capacity", verdict="allowed", kql="CapacityEvents\n| take 50")
    l2 = _line(engine="la", verdict="allowed", kql="PowerBIDatasetsWorkspace\n| take 20")
    recs = parse_audit_lines([l1, "junk", l2])
    assert [r["engine"] for r in recs] == ["capacity", "la"]


# ---------------------------------------------------------------------------
# shape_key -- same-key cases
# ---------------------------------------------------------------------------

_PEAK_WINDOWS_24H = (
    "CapacityEvents\n| where ingestion_time() > ago(1d)\n"
    "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
    "win = tostring(data.windowStartTime)\n| where base > 0\n"
    "| summarize cuPct = max(100.0 * used / (base * 1000 * 30)) by win\n"
    "| top 50 by cuPct desc"
)

_PEAK_WINDOWS_7D = (
    "CapacityEvents\n| where ingestion_time() > ago(7d)\n"
    "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
    "win = tostring(data.windowStartTime)\n| where base > 0\n"
    "| summarize cuPct = max(100.0 * used / (base * 1000 * 30)) by win\n"
    "| top 50 by cuPct desc"
)


def test_shape_key_ago_window_difference_is_same_shape_on_real_library_queries():
    # Real templates from query_library.json: identical shape, differ only in the ago() window.
    assert shape_key(_PEAK_WINDOWS_24H) == shape_key(_PEAK_WINDOWS_7D)


def test_shape_key_date_threshold_whitespace_and_operator_case_are_same_shape():
    variant = (
        "CapacityEvents\n| WHERE ingestion_time()   >   ago(24h)\n"
        "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
        "win = tostring(data.windowStartTime)\n| where base > 5\n"
        "| summarize cuPct = max(100.0 * used / (base * 1000 * 30)) by win\n"
        "| TOP 50 BY cuPct DESC"
    )
    assert shape_key(_PEAK_WINDOWS_24H) == shape_key(variant)


def test_shape_key_take_count_does_not_affect_shape():
    base = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
    assert shape_key(base + "\n| take 100") == shape_key(base + "\n| take 500")


def test_shape_key_limit_and_take_are_the_same_shape():
    base = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
    assert shape_key(base + "\n| limit 100") == shape_key(base + "\n| take 100")


def test_shape_key_doubled_trailing_bound_matches_single_take_twin():
    base = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
    # Agent's own query already ended in "| take 50"; the audit line wraps it again in "| take 100"
    # (tools.py:1568 always appends its own bound) -- a doubled trailing bound.
    agent_query_already_bounded = base + "\n| take 50"
    doubled = agent_query_already_bounded + "\n| take 100"
    single_take_twin = base + "\n| take 100"
    assert shape_key(doubled) == shape_key(single_take_twin) == shape_key(base)


def test_shape_key_ago_1d_and_ago_24h_are_the_same_shape():
    assert shape_key("T | where x > ago(1d)") == shape_key("T | where x > ago(24h)")


# ---------------------------------------------------------------------------
# shape_key -- different-key cases
# ---------------------------------------------------------------------------

def test_shape_key_bin_hourly_vs_bin_daily_are_different_shapes():
    hourly = "CapacityEvents | summarize avg(x) by bin(win, 1h)"
    daily = "CapacityEvents | summarize avg(x) by bin(win, 1d)"
    assert shape_key(hourly) != shape_key(daily)


def test_shape_key_ago_is_normalized_but_bin_is_preserved():
    q_ago1d_bin1h = "CapacityEvents | where ingestion_time() > ago(1d) | summarize avg(x) by bin(win, 1h)"
    q_ago24h_bin1h = "CapacityEvents | where ingestion_time() > ago(24h) | summarize avg(x) by bin(win, 1h)"
    q_ago1d_bin1d = "CapacityEvents | where ingestion_time() > ago(1d) | summarize avg(x) by bin(win, 1d)"
    # ago() difference alone -> same shape (bin unchanged).
    assert shape_key(q_ago1d_bin1h) == shape_key(q_ago24h_bin1h)
    # bin() difference -> different shape, even though ago() is normalized in both.
    assert shape_key(q_ago1d_bin1h) != shape_key(q_ago1d_bin1d)


def test_shape_key_formula_constant_difference_is_a_different_shape():
    # Honesty guard: a wrong CU-formula constant must never merge with the correct one.
    correct = (
        "CapacityEvents | extend cuPct = 100.0 * used / (base * 1000 * 30) | where cuPct > 80"
    )
    wrong = (
        "CapacityEvents | extend cuPct = 100.0 * used / (base * 1000 * 60) | where cuPct > 80"
    )
    assert shape_key(correct) != shape_key(wrong)


def test_shape_key_top_n_is_pinned_as_a_different_shape():
    # Documented behavior: top N is never placeholdered, so different N -> different shape.
    top50 = "CapacityEvents | project win | top 50 by win desc"
    top20 = "CapacityEvents | project win | top 20 by win desc"
    assert shape_key(top50) != shape_key(top20)


def test_shape_key_genuinely_different_queries_are_different_shapes():
    trend = (
        "CapacityEvents\n| where ingestion_time() > ago(1d)\n"
        "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
        "win = todatetime(data.windowStartTime)\n| where base > 0\n"
        "| summarize avgCuPct = avg(100.0 * used / (base * 1000 * 30)) by bin(win, 1h)\n"
        "| sort by win asc"
    )
    la_query = (
        "PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n"
        "| where isnotempty(ExecutingUser)\n"
        "| summarize totalCpuMs = sum(CpuTimeMs), ops = count() by ExecutingUser\n"
        "| top 20 by totalCpuMs desc"
    )
    assert shape_key(_PEAK_WINDOWS_24H) != shape_key(trend)
    assert shape_key(trend) != shape_key(la_query)


def test_shape_key_is_pure_and_deterministic():
    assert shape_key(_PEAK_WINDOWS_24H) == shape_key(_PEAK_WINDOWS_24H)
    assert isinstance(shape_key(_PEAK_WINDOWS_24H), str)


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------

def _rec(engine, kql):
    return {"tag": "adhoc-kql", "engine": engine, "verdict": "allowed", "kql": kql}


_SHAPE_A_BASE = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
_SHAPE_B_BASE = "CapacityEvents | where used > 80 | project win"
_LA_SHAPE_BASE = "PowerBIDatasetsWorkspace | where TimeGenerated > ago(1d) | project ExecutingUser"


def test_rank_empty_records_returns_empty_list():
    assert rank_candidates([], []) == []


def test_rank_groups_by_engine_and_shape_and_honors_min_count():
    records = [
        _rec("capacity", _SHAPE_A_BASE + "\n| take 50"),
        _rec("capacity", _SHAPE_A_BASE + "\n| take 100"),
        _rec("capacity", _SHAPE_A_BASE + "\n| take 200"),
        # A second shape that only shows up twice -- below min_count=3, must be dropped.
        _rec("capacity", _SHAPE_B_BASE + "\n| take 50"),
        _rec("capacity", _SHAPE_B_BASE + "\n| take 50"),
    ]
    out = rank_candidates(records, [], min_count=3)
    assert len(out) == 1
    assert out[0]["engine"] == "capacity"
    assert out[0]["shapeKey"] == shape_key(_SHAPE_A_BASE)
    assert out[0]["hitCount"] == 3


def test_rank_per_engine_grouping_same_kql_text_different_engine():
    # Identical kql text but different engine must form two distinct groups, not merge.
    records = (
        [_rec("capacity", _LA_SHAPE_BASE + "\n| take 50")] * 3
        + [_rec("la", _LA_SHAPE_BASE + "\n| take 50")] * 3
    )
    out = rank_candidates(records, [], min_count=3)
    assert {c["engine"] for c in out} == {"capacity", "la"}
    assert len(out) == 2
    for c in out:
        assert c["hitCount"] == 3


def test_rank_honors_top_n():
    records = []
    # Distinguish shapes by projected column name (not a comparison threshold, which shape_key
    # placeholders) so each is genuinely a different shape_key.
    shapes = [f"CapacityEvents | where x > 1 | project col{i}" for i in range(5)]
    # Give each shape a distinct count (3..7) so ranking-by-count is unambiguous.
    for i, shape in enumerate(shapes):
        count = 3 + i
        records += [_rec("capacity", shape + "\n| take 50")] * count
    out = rank_candidates(records, [], min_count=3, top_n=2)
    assert len(out) == 2
    assert [c["hitCount"] for c in out] == [7, 6]


def test_rank_deterministic_order_count_desc_shapekey_asc_tie_break():
    # Two groups tied on count -- must break the tie by shapeKey ascending.
    shape_z = "CapacityEvents | where z > 1 | project win"
    shape_a = "CapacityEvents | where a > 1 | project win"
    records = (
        [_rec("capacity", shape_z + "\n| take 50")] * 3
        + [_rec("capacity", shape_a + "\n| take 50")] * 3
    )
    out = rank_candidates(records, [], min_count=3)
    assert len(out) == 2
    assert out[0]["hitCount"] == out[1]["hitCount"] == 3
    assert out[0]["shapeKey"] < out[1]["shapeKey"]
    assert out[0]["shapeKey"] == shape_key(shape_a)
    assert out[1]["shapeKey"] == shape_key(shape_z)


def test_rank_dedup_against_existing_template_after_trailing_bound_strip():
    # Existing template ends "| take 100"; mined records for the SAME shape end "| take 50" --
    # after looped trailing-bound strip on both sides, they must be recognized as the same shape
    # and the mined group deduped away entirely.
    existing = [{"engine": "capacity", "kql": _SHAPE_A_BASE + "\n| take 100"}]
    records = [_rec("capacity", _SHAPE_A_BASE + "\n| take 50")] * 5
    assert rank_candidates(records, existing, min_count=3) == []


def test_rank_keeps_shape_not_covered_by_existing():
    existing = [{"engine": "capacity", "kql": _SHAPE_A_BASE + "\n| take 100"}]
    records = [_rec("capacity", _SHAPE_B_BASE + "\n| take 50")] * 3
    out = rank_candidates(records, existing, min_count=3)
    assert len(out) == 1
    assert out[0]["shapeKey"] == shape_key(_SHAPE_B_BASE)


def test_rank_fail_closed_clean_minority_member_wins_over_redacted_modal():
    # Modal (most frequent) exact text is redacted; a less-frequent, unique clean member exists.
    # The redacted text lives inside a string literal so shape_key (which blanks string-literal
    # content) still groups the two together. The clean member must be chosen as representative.
    redacted = _SHAPE_A_BASE + "\n| extend tag='***'\n| take 50"
    clean = _SHAPE_A_BASE + "\n| extend tag='ok'\n| take 50"
    assert shape_key(redacted) == shape_key(clean)  # precondition: same group
    records = (
        [_rec("capacity", redacted)] * 3   # modal, but contains ***
        + [_rec("capacity", clean)]        # minority, but clean
    )
    out = rank_candidates(records, [], min_count=3)
    assert len(out) == 1
    assert "***" not in out[0]["kql"]
    assert out[0]["kql"] == _SHAPE_A_BASE + "\n| extend tag='ok'"
    assert out[0]["hitCount"] == 4


def test_rank_drops_group_where_every_member_is_redacted():
    redacted1 = _SHAPE_A_BASE.replace("ingestion_time()", "sig=***") + "\n| take 50"
    redacted2 = _SHAPE_A_BASE.replace("ingestion_time()", "sig=***") + "\n| take 100"
    records = [_rec("capacity", redacted1)] * 2 + [_rec("capacity", redacted2)] * 2
    assert rank_candidates(records, [], min_count=3) == []


def test_rank_drops_group_whose_representative_fails_firewall():
    # Every member of this group is identical and uses a denied cross-database call -- the
    # representative fails validate_adhoc_kql, so the whole group must be dropped.
    dangerous = "CapacityEvents | union database('X').Y | take 50"
    records = [_rec("capacity", dangerous)] * 3
    assert rank_candidates(records, [], min_count=3) == []


def test_rank_representative_is_a_literal_observed_member_not_synthesized():
    variant1 = _SHAPE_A_BASE + "\n| take 50"
    variant2 = _SHAPE_A_BASE + "\n| take 100"
    variant3 = _SHAPE_A_BASE + "\n| take 200"
    records = (
        [_rec("capacity", variant1)] * 3
        + [_rec("capacity", variant2)] * 1
        + [_rec("capacity", variant3)] * 1
    )
    out = rank_candidates(records, [], min_count=3)
    assert len(out) == 1
    # The representative must be the literal post-strip text of the modal member, not a form
    # derived from shape_key (which would blank/placeholder content).
    assert out[0]["kql"] == _SHAPE_A_BASE
    assert out[0]["kql"] != shape_key(variant1)


def test_rank_every_returned_kql_passes_the_real_firewall():
    records = (
        [_rec("capacity", _SHAPE_A_BASE + "\n| take 50")] * 4
        + [_rec("la", _LA_SHAPE_BASE + "\n| take 20")] * 3
    )
    out = rank_candidates(records, [], min_count=3)
    assert len(out) == 2
    for candidate in out:
        # Must not raise.
        assert validate_adhoc_kql(candidate["kql"]) == candidate["kql"]


def test_rank_legit_literal_triple_asterisk_is_a_documented_false_drop():
    # A legitimate, firewall-valid query that happens to contain a literal "***" is
    # INDISTINGUISHABLE from a redacted member and is dropped (accepted fail-closed trade-off,
    # documented on _REDACTED_MARKER). Guards against anyone "fixing" this into a false-promote.
    legit = 'CapacityEvents | where Name == "***" | project win'
    assert validate_adhoc_kql(legit) == legit          # it WOULD pass the firewall
    records = [_rec("capacity", legit + "\n| take 50")] * 3
    assert rank_candidates(records, [], min_count=3) == []   # ...but is dropped as if redacted


def test_rank_tolerates_malformed_records_and_none_existing():
    # Defensive: non-dict records, records missing engine/kql, and existing_templates=None must
    # never raise -- a malformed captured log degrades to "no candidates", not a crash.
    records = [{"kql": "x"}, None, {"engine": "capacity"}, "not-a-dict"]
    assert rank_candidates(records, None, min_count=1) == []


# ---------------------------------------------------------------------------
# to_library_entries
# ---------------------------------------------------------------------------

def _group(engine="capacity", shape="shape-x", kql=None, hit_count=5):
    if kql is None:
        kql = _SHAPE_A_BASE
    return {"engine": engine, "shapeKey": shape, "kql": kql, "hitCount": hit_count}


def test_to_library_entries_empty_input_returns_empty_list():
    assert to_library_entries([], []) == []
    assert to_library_entries([], None) == []


def test_to_library_entries_key_order_matches_schema_plus_hitcount():
    group = _group(kql=_SHAPE_A_BASE, hit_count=4)
    out = to_library_entries([group], [])
    assert len(out) == 1
    assert list(out[0].keys()) == [
        "name", "category", "engine", "description", "kql", "groundedIn", "hitCount",
    ]


def test_to_library_entries_provenance_and_description():
    group = _group(engine="capacity", kql=_SHAPE_A_BASE, hit_count=7)
    entry = to_library_entries([group], [])[0]
    assert entry["category"] == "adhoc-mined"
    assert entry["groundedIn"] == "mined from adhoc audit log"
    assert entry["description"].strip() == entry["description"]
    assert entry["description"]
    assert "capacity" in entry["description"]
    assert "7" in entry["description"]
    lowered = entry["description"].lower()
    assert "todo" not in lowered and "placeholder" not in lowered
    assert entry["kql"] == _SHAPE_A_BASE
    assert entry["engine"] == "capacity"
    assert entry["hitCount"] == 7


def test_to_library_entries_names_are_lowercase_spaceless_kebab_and_unique_in_batch():
    groups = [
        _group(engine="capacity", shape="shape-a", kql=_SHAPE_A_BASE, hit_count=5),
        _group(engine="la", shape="shape-b", kql=_LA_SHAPE_BASE, hit_count=3),
    ]
    out = to_library_entries(groups, [])
    names = [e["name"] for e in out]
    assert len(names) == len(set(names))
    for n in names:
        assert n == n.lower()
        assert " " not in n
        assert all(c.isalnum() or c == "-" for c in n)


def test_to_library_entries_preserves_input_order():
    groups = [
        _group(engine="capacity", shape="shape-a", kql=_SHAPE_A_BASE, hit_count=9),
        _group(engine="la", shape="shape-b", kql=_LA_SHAPE_BASE, hit_count=3),
    ]
    out = to_library_entries(groups, [])
    assert out[0]["hitCount"] == 9
    assert out[1]["hitCount"] == 3


def test_to_library_entries_dominant_op_two_where_one_summarize():
    kql = (
        "CapacityEvents | where a > 1 | where b > 2 | summarize count() by a"
    )
    group = _group(kql=kql, hit_count=3)
    entry = to_library_entries([group], [])[0]
    assert entry["name"].split("-")[2] == "where"


def test_to_library_entries_dominant_op_tie_breaks_first_appearing():
    kql = "CapacityEvents | where a > 1 | project a"
    group = _group(kql=kql, hit_count=3)
    entry = to_library_entries([group], [])[0]
    assert entry["name"].split("-")[2] == "where"


def test_to_library_entries_unknown_op_falls_back_to_query():
    # No recognized operator keyword at all -- falls back to 'query'.
    kql = "CapacityEvents"
    group = _group(kql=kql, hit_count=3)
    entry = to_library_entries([group], [])[0]
    assert entry["name"].split("-")[2] == "query"


def test_to_library_entries_name_format_matches_expected_pattern():
    group = _group(engine="capacity", shape="a-particular-shape", kql=_SHAPE_A_BASE, hit_count=3)
    entry = to_library_entries([group], [])[0]
    h = hashlib.sha1("a-particular-shape".encode("utf-8")).hexdigest()[:6]
    assert entry["name"] == f"adhoc-capacity-where-{h}"


def test_to_library_entries_collision_with_existing_template_lengthens_hash():
    shape = "collide-shape"
    kql = _SHAPE_A_BASE
    h6 = hashlib.sha1(shape.encode("utf-8")).hexdigest()[:6]
    would_be_name = f"adhoc-capacity-where-{h6}"
    existing = [{
        "name": would_be_name, "category": "adhoc-mined", "engine": "capacity",
        "description": "pre-existing", "kql": "T | where 1 == 1", "groundedIn": "test seed",
    }]
    group = _group(engine="capacity", shape=shape, kql=kql, hit_count=3)
    entry = to_library_entries([group], existing)[0]
    assert entry["name"] != would_be_name
    h7 = hashlib.sha1(shape.encode("utf-8")).hexdigest()[:7]
    assert entry["name"] == f"adhoc-capacity-where-{h7}"
    assert entry["name"] not in {t["name"] for t in existing}


def test_to_library_entries_within_batch_engineered_sha1_prefix_collision_stays_unique(monkeypatch):
    # Engineer two DISTINCT shapeKeys whose sha1 hexdigests share the same first-6-hex prefix (via
    # a monkeypatched hashlib.sha1) but diverge after -- proves the collision-resolution loop works
    # within a single batch, not just against existing_templates.
    fake_digests = {
        "shape-one": "aaaaaa1111111111111111111111111111111a",
        "shape-two": "aaaaaa2222222222222222222222222222222b",
    }

    class _FakeDigest:
        def __init__(self, text):
            self._text = text

        def hexdigest(self):
            return fake_digests[self._text]

    def fake_sha1(data):
        return _FakeDigest(data.decode("utf-8"))

    monkeypatch.setattr(mine_module.hashlib, "sha1", fake_sha1)

    same_op_kql = "CapacityEvents | where a > 1 | project a"
    groups = [
        _group(engine="capacity", shape="shape-one", kql=same_op_kql, hit_count=5),
        _group(engine="capacity", shape="shape-two", kql=same_op_kql, hit_count=4),
    ]
    out = to_library_entries(groups, [])
    names = [e["name"] for e in out]
    assert len(names) == len(set(names)) == 2
    # First group takes the 6-char prefix; second must lengthen since the prefixes collide.
    assert names[0] == "adhoc-capacity-where-aaaaaa"
    assert names[1] == "adhoc-capacity-where-aaaaaa2"


def test_to_library_entries_every_entry_passes_firewall_and_schema_gates():
    group = _group(engine="la", shape="la-shape", kql=_LA_SHAPE_BASE, hit_count=6)
    entry = to_library_entries([group], [])[0]
    assert validate_adhoc_kql(entry["kql"]) == entry["kql"]
    assert entry["engine"] in ("capacity", "la")
    assert entry["description"].strip()
    assert entry["groundedIn"].strip()


def test_to_library_entries_integration_with_rank_candidates():
    records = [_rec("capacity", _SHAPE_A_BASE + "\n| take 50")] * 5
    ranked = rank_candidates(records, [], min_count=3)
    assert len(ranked) == 1
    entries = to_library_entries(ranked, [])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kql"] == _SHAPE_A_BASE
    assert entry["hitCount"] == 5
    assert entry["engine"] == "capacity"
    assert list(entry.keys()) == [
        "name", "category", "engine", "description", "kql", "groundedIn", "hitCount",
    ]
    validate_adhoc_kql(entry["kql"])


def test_to_library_entries_tolerates_malformed_existing_templates():
    # Defensive (mirrors the Task-2 malformed-records test): non-dict entries, a dict missing
    # 'name', and name=None must not crash name-uniqueness seeding.
    ranked = [{"engine": "capacity", "shapeKey": "x", "kql": _SHAPE_A_BASE, "hitCount": 3}]
    existing = [None, "not-a-dict", {"engine": "capacity"}, {"name": None}]
    entries = to_library_entries(ranked, existing)
    assert len(entries) == 1
    assert entries[0]["name"].startswith("adhoc-capacity-")


def test_to_library_entries_string_embedded_pipe_does_not_flip_operator_label():
    # A '|' inside a string literal must not be counted as a pipe operator (name/description are
    # cosmetic, but should stay accurate). Real operator here is a single 'where'.
    kql = 'CapacityEvents | where Message has "a | project b | summarize c"'
    ranked = [{"engine": "capacity", "shapeKey": "s", "kql": kql, "hitCount": 3}]
    entries = to_library_entries(ranked, [])
    assert entries[0]["name"] == "adhoc-capacity-where-" + hashlib.sha1(b"s").hexdigest()[:6]

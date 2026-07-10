"""Eval-flywheel growth loop (mining half, Phase 5.4b): the [conversation] audit-log parser, the
question shape canonicalizer, candidate ranking, and eval-case-skeleton projection. Mirrors
tests/test_mine.py (3-A). No I/O; offline/deterministic throughout."""
import hashlib
import json

import pytest

import fabric_audit_agent.eval.mine_evals as mine_evals_module
from fabric_audit_agent.eval.mine_evals import (
    SCRIPT_PLACEHOLDER,
    parse_conversation_lines,
    rank_candidates,
    shape_key,
    to_eval_skeletons,
)
from fabric_audit_agent.eval.score_investigations import score_agent_case


def _line(question="why did capacity spike?", toolsCalled=None, abstainedHint=False,
          tag="conversation", **extra):
    rec = {
        "tag": tag,
        "ts": "2026-07-05T00:00:00Z",
        "question": question,
        "toolsCalled": toolsCalled or [],
        "toolCount": len(toolsCalled or []),
        "abstainedHint": abstainedHint,
        "answerChars": 42,
    }
    rec.update(extra)
    return "[conversation] " + json.dumps(rec, ensure_ascii=False)


# ---------------------------------------------------------------------------
# parse_conversation_lines
# ---------------------------------------------------------------------------

def test_parse_extracts_conversation_record():
    line = _line(question="why did capacity spike?", toolsCalled=["investigate_capacity_spike"])
    recs = parse_conversation_lines([line])
    assert len(recs) == 1
    assert recs[0]["tag"] == "conversation"
    assert recs[0]["question"] == "why did capacity spike?"
    assert recs[0]["toolsCalled"] == ["investigate_capacity_spike"]


def test_parse_skips_non_conversation_tag():
    line = _line(tag="something-else")
    assert parse_conversation_lines([line]) == []


def test_parse_skips_non_marker_lines_without_raising():
    lines = ["just a regular log line", "another line with no marker at all", ""]
    assert parse_conversation_lines(lines) == []


def test_parse_skips_malformed_json_without_raising():
    lines = [
        "[conversation] {this is not valid json",
        "[conversation] ",
        "[conversation] [1, 2, 3]",     # valid json, but not a dict
        "[conversation] 42",            # valid json scalar
        "[conversation] log failed: RuntimeError",  # the sibling failure line -- not JSON at all
    ]
    assert parse_conversation_lines(lines) == []


def test_parse_extracts_line_with_logger_prefix_before_marker():
    raw = _line(question="who is driving CU?")
    line = "2026-07-08 12:00:00 INFO app.audit " + raw
    recs = parse_conversation_lines([line])
    assert len(recs) == 1
    assert recs[0]["question"] == "who is driving CU?"


def test_parse_accepts_any_iterable_of_strings_not_just_a_list():
    line = _line(question="why did capacity spike?")

    def gen():
        yield line
        yield "not a marker line"

    recs = parse_conversation_lines(gen())
    assert len(recs) == 1


def test_parse_returns_multiple_records_in_order():
    l1 = _line(question="q1")
    l2 = _line(question="q2")
    recs = parse_conversation_lines([l1, "junk", l2])
    assert [r["question"] for r in recs] == ["q1", "q2"]


def test_parse_non_string_lines_are_skipped_without_raising():
    assert parse_conversation_lines([None, 42, {"tag": "conversation"}]) == []


# ---------------------------------------------------------------------------
# shape_key -- MERGE cases
# ---------------------------------------------------------------------------

def test_shape_key_time_of_day_3pm_and_9am_are_same_shape():
    assert shape_key("why did capacity spike at 3pm?") == shape_key("why did capacity spike at 9am?")


def test_shape_key_time_with_minutes_merges_with_bare_hour():
    assert shape_key("what ran at 9:05am today?") == shape_key("what ran at 3pm today?")


def test_shape_key_bare_number_difference_is_same_shape():
    assert shape_key("what ran between 12:45 and 13:00 today?") == \
        shape_key("what ran between 08:15 and 09:30 today?")


def test_shape_key_date_number_difference_is_same_shape():
    assert shape_key("why did capacity spike on 2026-06-08?") == \
        shape_key("why did capacity spike on 2026-07-01?")


def test_shape_key_quoted_double_value_difference_is_same_shape():
    assert shape_key('why is dataset "Sales" spiking?') == shape_key('why is dataset "Finance" spiking?')


def test_shape_key_quoted_single_value_difference_is_same_shape():
    assert shape_key("why is dataset 'Sales' spiking?") == shape_key("why is dataset 'Finance' spiking?")


def test_shape_key_whitespace_and_case_are_same_shape():
    a = "Why Did Capacity   Spike?"
    b = "why did capacity spike?"
    assert shape_key(a) == shape_key(b)


def test_shape_key_trailing_punctuation_variants_are_same_shape():
    assert shape_key("why did capacity spike") == shape_key("why did capacity spike?!")


def test_shape_key_is_pure_and_deterministic():
    q = "why did capacity spike at 3pm?"
    assert shape_key(q) == shape_key(q)
    assert isinstance(shape_key(q), str)


# ---------------------------------------------------------------------------
# shape_key -- NOT-merge cases
# ---------------------------------------------------------------------------

def test_shape_key_genuinely_different_questions_are_different_shapes():
    assert shape_key("why did capacity spike?") != shape_key("who is driving CU consumption?")


def test_shape_key_negation_apostrophe_form_is_a_different_shape():
    assert shape_key("why did capacity spike?") != shape_key("why didn't capacity spike?")


def test_shape_key_negation_not_word_is_a_different_shape():
    assert shape_key("did capacity spike today?") != shape_key("did capacity not spike today?")


def test_shape_key_negation_no_word_is_a_different_shape():
    assert shape_key("is there a spike?") != shape_key("is there no spike?")


def test_shape_key_contraction_is_not_mistaken_for_a_quoted_span():
    # Two different contractions must not have their intervening words swallowed as if the
    # apostrophes were a matched quote pair.
    a = "why didn't the capacity spike stop?"
    b = "why wasn't the capacity spike stopped?"
    assert shape_key(a) != shape_key(b)
    assert "didn't" in a and "n't" in shape_key(a)


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------

def _rec(question, toolsCalled=None, abstainedHint=False):
    return {
        "tag": "conversation",
        "question": question,
        "toolsCalled": toolsCalled or [],
        "abstainedHint": abstainedHint,
    }


def test_rank_empty_records_returns_empty_list():
    assert rank_candidates([], []) == []


def test_rank_honors_min_count():
    records = (
        [_rec("why did capacity spike?", ["investigate_capacity_spike"])] * 2
        # A second shape that only shows up once -- below min_count=2, must be dropped.
        + [_rec("who is driving CU?", ["user_activity"])] * 1
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["question"] == "why did capacity spike?"
    assert out[0]["hitCount"] == 2


def test_rank_honors_top_n():
    # Distinguish shapes by word, not digit (shape_key placeholders bare numbers), so each is
    # genuinely a different shape_key.
    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    records = []
    for i, word in enumerate(words):
        count = 2 + i
        records += [_rec(f"question variant {word}?", ["tool_a"])] * count
    out = rank_candidates(records, [], min_count=2, top_n=2)
    assert len(out) == 2
    assert [c["hitCount"] for c in out] == [6, 5]


def test_rank_dedup_against_existing_case_by_shape():
    existing = [{
        "name": "existing-case",
        "messages": [{"role": "user", "content": "why did capacity spike at 3pm?"}],
    }]
    # Same shape (time-token merge), different exact text -- must still be excluded.
    records = [_rec("why did capacity spike at 9am?", ["investigate_capacity_spike"])] * 3
    assert rank_candidates(records, existing, min_count=2) == []


def test_rank_keeps_shape_not_covered_by_existing():
    existing = [{
        "name": "existing-case",
        "messages": [{"role": "user", "content": "why did capacity spike?"}],
    }]
    records = [_rec("who is driving CU?", ["user_activity"])] * 3
    out = rank_candidates(records, existing, min_count=2)
    assert len(out) == 1
    assert out[0]["question"] == "who is driving CU?"


def test_rank_representative_is_most_frequent_exact_text():
    records = (
        [_rec("Why did capacity spike?", ["investigate_capacity_spike"])] * 2
        + [_rec("why did capacity spike??", ["investigate_capacity_spike"])] * 5
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["question"] == "why did capacity spike??"
    assert out[0]["hitCount"] == 7


def test_rank_expect_tool_is_most_common_across_group():
    records = (
        [_rec("why did capacity spike?", ["investigate_capacity_spike"])] * 3
        + [_rec("why did capacity spike?", ["spike_events"])] * 1
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["expectTool"] == "investigate_capacity_spike"
    assert out[0]["observedTools"] == {"investigate_capacity_spike": 3, "spike_events": 1}


def test_rank_expect_tool_is_none_for_a_no_tool_group():
    records = [_rec("what is going on?", toolsCalled=[])] * 2
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["expectTool"] is None
    assert out[0]["observedTools"] == {}


def test_rank_expect_abstain_is_majority_true():
    records = (
        [_rec("give me diagnostics", abstainedHint=True)] * 3
        + [_rec("give me diagnostics", abstainedHint=False)] * 1
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["expectAbstain"] is True
    assert out[0]["abstainHintCounts"] == {"true": 3, "false": 1}


def test_rank_expect_abstain_is_majority_false():
    records = (
        [_rec("give me diagnostics", abstainedHint=False)] * 3
        + [_rec("give me diagnostics", abstainedHint=True)] * 1
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["expectAbstain"] is False
    assert out[0]["abstainHintCounts"] == {"true": 1, "false": 3}


def test_rank_expect_abstain_tie_resolves_false():
    # 2-2 tie -> strict majority fails -> False (documented; also matches the scorer reading an
    # absent key as False, so a tie never over-claims abstain).
    records = (
        [_rec("give me diagnostics", abstainedHint=True)] * 2
        + [_rec("give me diagnostics", abstainedHint=False)] * 2
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 1
    assert out[0]["expectAbstain"] is False
    assert out[0]["abstainHintCounts"] == {"true": 2, "false": 2}


def test_rank_expect_tool_tie_breaks_lexicographically():
    # A 1-1(-1) tool tie resolves to the lexicographically smallest name (deterministic).
    records = [
        _rec("why did capacity spike?", ["spike_events"]),
        _rec("why did capacity spike?", ["investigate_capacity_spike"]),
        _rec("why did capacity spike?", ["capacity_patterns"]),
    ]
    out = rank_candidates(records, [], min_count=3)
    assert len(out) == 1
    assert out[0]["expectTool"] == "capacity_patterns"  # min of the three tied names


def test_rank_deterministic_order_count_desc_shapekey_asc_tie_break():
    records = (
        [_rec("z question here?", ["tool_a"])] * 2
        + [_rec("a question here?", ["tool_a"])] * 2
    )
    out = rank_candidates(records, [], min_count=2)
    assert len(out) == 2
    assert out[0]["hitCount"] == out[1]["hitCount"] == 2
    assert shape_key(out[0]["question"]) < shape_key(out[1]["question"])
    assert out[0]["question"] == "a question here?"
    assert out[1]["question"] == "z question here?"


def test_rank_tolerates_malformed_records_and_none_existing():
    # Non-dict records, a record missing 'question', and existing_cases=None must never raise --
    # a malformed captured log degrades to "no candidates" (the one well-formed record here is
    # legitimately below the default min_count=2), not a crash.
    records = [{"question": "x"}, None, {"toolsCalled": []}, "not-a-dict"]
    assert rank_candidates(records, None) == []


# ---------------------------------------------------------------------------
# to_eval_skeletons
# ---------------------------------------------------------------------------

def _candidate(question="why did capacity spike?", expectTool="investigate_capacity_spike",
               expectAbstain=False, hitCount=5, observedTools=None, abstainHintCounts=None):
    return {
        "question": question,
        "expectTool": expectTool,
        "expectAbstain": expectAbstain,
        "hitCount": hitCount,
        "observedTools": observedTools if observedTools is not None else {expectTool: hitCount},
        "abstainHintCounts": abstainHintCounts if abstainHintCounts is not None else {"true": 0, "false": hitCount},
    }


def test_to_eval_skeletons_empty_input_returns_empty_list():
    assert to_eval_skeletons([], []) == []
    assert to_eval_skeletons([], None) == []


def test_to_eval_skeletons_keys_present():
    out = to_eval_skeletons([_candidate()], [])
    assert len(out) == 1
    skel = out[0]
    assert set(skel.keys()) == {"name", "messages", "expectTool", "expectAbstain", "script", "_minedFrom"}
    assert skel["messages"] == [{"role": "user", "content": "why did capacity spike?"}]
    assert skel["expectTool"] == "investigate_capacity_spike"
    assert skel["expectAbstain"] is False


def test_to_eval_skeletons_script_is_the_error_if_run_placeholder():
    out = to_eval_skeletons([_candidate()], [])
    assert out[0]["script"] == SCRIPT_PLACEHOLDER
    assert isinstance(out[0]["script"], str)


def test_to_eval_skeletons_minedfrom_carries_provenance():
    cand = _candidate(hitCount=9, observedTools={"investigate_capacity_spike": 7, "spike_events": 2},
                       abstainHintCounts={"true": 2, "false": 7})
    out = to_eval_skeletons([cand], [])
    minedFrom = out[0]["_minedFrom"]
    assert minedFrom["hitCount"] == 9
    assert minedFrom["observedTools"] == {"investigate_capacity_spike": 7, "spike_events": 2}
    assert minedFrom["abstainHintCounts"] == {"true": 2, "false": 7}


def test_to_eval_skeletons_name_is_lowercase_kebab():
    out = to_eval_skeletons([_candidate(question="Why Did Capacity Spike?")], [])
    name = out[0]["name"]
    assert name == name.lower()
    assert " " not in name
    assert all(c.isalnum() or c == "-" for c in name)
    assert name.startswith("mined-")


def test_to_eval_skeletons_names_unique_within_batch():
    out = to_eval_skeletons(
        [_candidate(question="question one?"), _candidate(question="question two?")], [],
    )
    names = [s["name"] for s in out]
    assert len(names) == len(set(names))


def test_to_eval_skeletons_collision_with_existing_case_lengthens_hash():
    question = "why did capacity spike?"
    base = "why-did-capacity-spike"
    h6 = hashlib.sha1(question.encode("utf-8")).hexdigest()[:6]
    would_be_name = f"mined-{base}-{h6}"
    existing = [{
        "name": would_be_name,
        "messages": [{"role": "user", "content": "unrelated pre-existing case"}],
    }]
    out = to_eval_skeletons([_candidate(question=question)], existing)
    assert out[0]["name"] != would_be_name
    h7 = hashlib.sha1(question.encode("utf-8")).hexdigest()[:7]
    assert out[0]["name"] == f"mined-{base}-{h7}"
    assert out[0]["name"] not in {c["name"] for c in existing}


def test_to_eval_skeletons_within_batch_engineered_sha1_prefix_collision_stays_unique(monkeypatch):
    # Two DISTINCT questions that share the same kebab base ("same-base") -- e.g. punctuation-only
    # difference -- but whose (mocked) sha1 hexdigests also share the same first-6-hex prefix.
    # Proves the collision-resolution loop lengthens on a genuine full-name collision, not just
    # against existing_cases.
    q1 = "same base?"
    q2 = "same, base!"
    assert mine_evals_module._kebab(q1) == mine_evals_module._kebab(q2) == "same-base"

    fake_digests = {
        q1: "aaaaaa1111111111111111111111111111111a",
        q2: "aaaaaa2222222222222222222222222222222b",
    }

    class _FakeDigest:
        def __init__(self, text):
            self._text = text

        def hexdigest(self):
            return fake_digests[self._text]

    def fake_sha1(data):
        return _FakeDigest(data.decode("utf-8"))

    monkeypatch.setattr(mine_evals_module.hashlib, "sha1", fake_sha1)

    out = to_eval_skeletons([_candidate(question=q1), _candidate(question=q2)], [])
    names = [s["name"] for s in out]
    assert len(names) == len(set(names)) == 2
    assert names[0] == "mined-same-base-aaaaaa"
    assert names[1] == "mined-same-base-aaaaaa2"


def test_to_eval_skeletons_tolerates_malformed_existing_cases():
    existing = [None, "not-a-dict", {"messages": []}, {"name": None}]
    out = to_eval_skeletons([_candidate()], existing)
    assert len(out) == 1
    assert out[0]["name"].startswith("mined-")


def test_to_eval_skeletons_preserves_input_order():
    out = to_eval_skeletons(
        [_candidate(question="first?", hitCount=9), _candidate(question="second?", hitCount=3)], [],
    )
    assert out[0]["_minedFrom"]["hitCount"] == 9
    assert out[1]["_minedFrom"]["hitCount"] == 3


def test_to_eval_skeletons_integration_with_rank_candidates():
    records = [_rec("why did capacity spike?", ["investigate_capacity_spike"])] * 3
    ranked = rank_candidates(records, [], min_count=2)
    out = to_eval_skeletons(ranked, [])
    assert len(out) == 1
    assert out[0]["messages"][0]["content"] == "why did capacity spike?"
    assert out[0]["expectTool"] == "investigate_capacity_spike"
    assert out[0]["script"] == SCRIPT_PLACEHOLDER


# ---------------------------------------------------------------------------
# Fail-loud pin: an unedited skeleton can never be scored/pass.
# ---------------------------------------------------------------------------

def test_unedited_skeleton_raises_when_fed_to_score_agent_case():
    records = [_rec("why did capacity spike?", ["investigate_capacity_spike"])] * 3
    ranked = rank_candidates(records, [], min_count=2)
    skeleton = to_eval_skeletons(ranked, [])[0]

    with pytest.raises(Exception):
        score_agent_case(skeleton)

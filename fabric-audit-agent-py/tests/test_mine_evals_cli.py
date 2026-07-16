"""``mine-evals`` CLI tests (Phase 5.4b Task 2) -- the eval-flywheel mining CLI seam. Mirrors
tests/test_entrypoints.py's mine-queries CLI tests, adapted for the ``[conversation]`` log and the
PREVIEW-ONLY (no ``--write``) contract. Offline/deterministic; never touches the real package
``fabric_audit_agent/eval/agent_cases.json`` unless a test explicitly targets it read-only."""
import io
import json
import os

from fabric_audit_agent.entrypoints import run_mine_evals_cli
from fabric_audit_agent.eval.mine_evals import SCRIPT_PLACEHOLDER
from fabric_audit_agent.tools import create_tool_definitions

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_AGENT_CASES = os.path.join(_REPO, "fabric_audit_agent", "eval", "agent_cases.json")


def _conv_line(question, toolsCalled=None, abstainedHint=False, tag="conversation", **extra):
    """Reproduce the real ``[conversation] `` audit-log line format (agent_server/agent.py
    ``_conversation_audit_log``), matching tests/test_mine_evals.py's ``_line`` helper."""
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


_NEW_QUESTION = "did the marketing workspace exceed its cu budget yesterday?"


def _new_shape_log_text(n=2, question=_NEW_QUESTION, toolsCalled=None, abstainedHint=False):
    return "\n".join(_conv_line(question, toolsCalled=toolsCalled, abstainedHint=abstainedHint) for _ in range(n))


def _write_cases(path, cases=None):
    if cases is None:
        cases = []
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cases, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return cases


# ---- preview writes nothing + lists candidate + skeleton + header ----

def test_mine_evals_preview_writes_nothing_and_lists_candidate_and_skeleton(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)
    before = cases_path.read_bytes()

    logfile = tmp_path / "conversations.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")

    out = run_mine_evals_cli([str(logfile)], cases_path=str(cases_path))

    assert cases_path.read_bytes() == before   # preview never mutates the cases file
    assert not (tmp_path / "runs").exists()    # and creates nothing else

    assert "mined-did-the-marketing-workspace" in out
    assert '"hitCount": 2' in out
    assert _NEW_QUESTION in out


def test_mine_evals_skeleton_placeholder_and_header_present(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)
    logfile = tmp_path / "conversations.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")

    out = run_mine_evals_cli([str(logfile)], cases_path=str(cases_path))

    # the ERROR-if-run placeholder, never a real script
    assert SCRIPT_PLACEHOLDER in out
    # the header: author-a-script / abstain-token / strip-_minedFrom / hints-unverified
    assert "script" in out.lower() and "ERRORS if" in out.lower() or "errors if" in out.lower()
    assert "_minedFrom" in out
    assert "can't" in out or "cannot" in out
    assert "enable monitoring" in out
    assert "unverified" in out.lower()


# ---- zero candidates ----

def test_mine_evals_preview_zero_candidates_message(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)

    logfile = tmp_path / "conversations.log"
    logfile.write_text("", encoding="utf-8")   # empty log -> zero candidates

    out = run_mine_evals_cli([str(logfile)], cases_path=str(cases_path))

    assert "No promotable conversation shapes found" in out


# ---- --min-count / --top take effect ----

def test_mine_evals_min_count_override_excludes_below_threshold(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)
    logfile = tmp_path / "conversations.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")   # 2 hits

    # default min_count=2 -> promotable
    out_default = run_mine_evals_cli([str(logfile)], cases_path=str(cases_path))
    assert "No promotable conversation shapes found" not in out_default

    # raised --min-count 3 -> below threshold -> excluded
    out_raised = run_mine_evals_cli([str(logfile), "--min-count", "3"], cases_path=str(cases_path))
    assert "No promotable conversation shapes found" in out_raised


def test_mine_evals_top_limits_candidate_count(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)
    logfile = tmp_path / "conversations.log"

    shape_a = _new_shape_log_text(n=3, question="did dataset a exceed its refresh window?")
    shape_b = _new_shape_log_text(n=2, question="did dataset b exceed its refresh window?")
    logfile.write_text(shape_a + "\n" + shape_b, encoding="utf-8")

    out_all = run_mine_evals_cli([str(logfile)], cases_path=str(cases_path))
    assert "dataset a" in out_all and "dataset b" in out_all

    out_top1 = run_mine_evals_cli([str(logfile), "--top", "1"], cases_path=str(cases_path))
    assert "dataset a" in out_top1   # higher hitCount survives
    assert "dataset b" not in out_top1


# ---- stdin / missing file ----

def test_mine_evals_reads_stdin(monkeypatch, tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(_new_shape_log_text(n=2)))

    out = run_mine_evals_cli(["-"], cases_path=str(cases_path))

    assert cases_path.read_bytes()   # unchanged, still valid
    assert "mined-did-the-marketing-workspace" in out


def test_mine_evals_missing_logfile_returns_clean_error(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)

    out = run_mine_evals_cli([str(tmp_path / "does-not-exist.log")], cases_path=str(cases_path))

    assert isinstance(out, str)
    assert "mine-evals" in out
    assert "does-not-exist.log" in out


def test_mine_evals_bad_args_return_clean_strings_never_exit(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path)
    logfile = tmp_path / "conversations.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")

    for rest in (
        [],                                             # missing required positional logfile
        [str(logfile), "--min-count", "notanumber"],    # non-int value
        [str(logfile), "--nope"],                       # unknown flag
    ):
        out = run_mine_evals_cli(rest, cases_path=str(cases_path))
        assert isinstance(out, str) and out.startswith("mine-evals:")


# ---- dedup vs an existing case (same shape) ----

def test_mine_evals_dedup_against_existing_case_same_shape(tmp_path):
    cases_path = tmp_path / "agent_cases.json"
    _write_cases(cases_path, cases=[
        {
            "name": "already-covered",
            "messages": [{"role": "user", "content": "did the marketing workspace exceed its cu budget yesterday?"}],
            "script": [{"type": "text", "text": "yes"}],
            "expectTool": None,
            "expectAbstain": False,
        },
    ])

    logfile = tmp_path / "conversations.log"
    # same shape, different literal wording via number swap ("yesterday" -> a date is a closer real
    # scenario, but the important bit is shape_key equality; reuse the identical question text is
    # sufficient since shape_key is deterministic on identical input too).
    logfile.write_text(_new_shape_log_text(n=3), encoding="utf-8")

    out = run_mine_evals_cli([str(logfile)], cases_path=str(cases_path))

    assert "No promotable conversation shapes found" in out


# ---- missing/malformed cases file degrades to [] ----

def test_mine_evals_missing_or_malformed_cases_degrades_to_empty(tmp_path):
    logfile = tmp_path / "conversations.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")

    missing = tmp_path / "absent.json"
    out = run_mine_evals_cli([str(logfile)], cases_path=str(missing))
    assert "mined-did-the-marketing-workspace" in out
    assert not missing.exists()

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not valid json", encoding="utf-8")
    out2 = run_mine_evals_cli([str(logfile)], cases_path=str(malformed))
    assert "mined-did-the-marketing-workspace" in out2


# ---- __main__ dispatch ----

def test_main_dispatch_mine_evals_preview_does_not_touch_real_agent_cases(tmp_path, capsys):
    from fabric_audit_agent.__main__ import main

    before = open(_REAL_AGENT_CASES, "rb").read()
    logfile = tmp_path / "conversations.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")

    main(["mine-evals", str(logfile)])

    out = capsys.readouterr().out
    assert "mined-did-the-marketing-workspace" in out or "No promotable conversation shapes found" in out
    after = open(_REAL_AGENT_CASES, "rb").read()
    assert after == before   # dispatch must never mutate the real package file (no --write exists)


def test_tool_count_unaffected_by_mine_evals(tmp_path):
    assert len(create_tool_definitions(base_dir=str(tmp_path))) == 20

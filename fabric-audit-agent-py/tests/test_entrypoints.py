"""Entry-point tests — cluster 9. CLIs (audit/eval/whatif/triggers/lifecycle/dax/mine-queries),
the MCP tool/manifest, and the Databricks job wiring (injected fake ports, no real SDK/network)."""
import io
import json
import os
import shutil

import pytest

from fabric_audit_agent.entrypoints import (
    run_audit_cli, run_eval_cli, run_whatif_cli, run_triggers_cli, run_lifecycle_cli, run_dax_cli,
    run_mine_queries_cli,
)
from fabric_audit_agent.tools import create_tool_definitions
from fabric_audit_agent.mcp_server import manifest
from fabric_audit_agent.job import run_job, build_rest_config
from fabric_audit_agent.adapters import create_stub_reasoner
from fabric_audit_agent.config import DEFAULT_CONFIG
from fabric_audit_agent.query.firewall import validate_adhoc_kql

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_LIBRARY = os.path.join(_REPO, "fabric_audit_agent", "query_library.json")


def _temp_base(tmp_path):
    """A base dir with the real fixtures copied in, so runs/ writes land under tmp."""
    base = tmp_path / "base"
    (base / "fixtures" / "golden").mkdir(parents=True)
    shutil.copy(os.path.join(_REPO, "fixtures", "estate.json"), base / "fixtures" / "estate.json")
    shutil.copy(os.path.join(_REPO, "fixtures", "golden", "cases.json"), base / "fixtures" / "golden" / "cases.json")
    return str(base)


# ---- audit ----
def test_audit_cli_writes_outputs(tmp_path):
    base = _temp_base(tmp_path)
    out = run_audit_cli(base_dir=base)
    assert "Verdict:" in out and "Findings written to" in out and "Report written to" in out
    assert os.path.exists(os.path.join(base, "runs", "latest.json"))
    assert os.path.exists(os.path.join(base, "runs", "report.md"))


# ---- eval ----
def test_eval_cli_scores_golden_suite(tmp_path):
    out = run_eval_cli(base_dir=_temp_base(tmp_path))
    assert "Suite:" in out and "PASS" in out
    assert "recall 1," in out and "avgRecall 1," in out   # JS-style integers, not 1.0
    assert "1.0" not in out


# ---- whatif ----
def test_whatif_cli(tmp_path):
    out = run_whatif_cli("model", 5.0, "06:00", base_dir=_temp_base(tmp_path))
    assert out.startswith("What-if verdict:")
    assert "5 GB" in out and "5.0 GB" not in out   # whole sizeGB renders JS-style


# ---- triggers ----
def test_triggers_cli_returns_text(tmp_path):
    out = run_triggers_cli(base_dir=_temp_base(tmp_path))
    assert isinstance(out, str) and len(out) > 0


# ---- lifecycle ----
def test_lifecycle_cli_acknowledge_roundtrip(tmp_path):
    base = _temp_base(tmp_path)
    out = run_lifecycle_cli("acknowledged", "capacity.throttle::X", now="2026-06-11T00:00:00Z", base_dir=base)
    assert out == "Set capacity.throttle::X -> acknowledged"
    assert os.path.exists(os.path.join(base, "runs", "lifecycle.json"))


def test_lifecycle_cli_snoozed_requires_until(tmp_path):
    with pytest.raises(ValueError):
        run_lifecycle_cli("snoozed", "k", base_dir=_temp_base(tmp_path))


def test_lifecycle_cli_unknown_action(tmp_path):
    with pytest.raises(ValueError):
        run_lifecycle_cli("bogus", "k", base_dir=str(tmp_path))


# ---- dax ----
def test_dax_cli_clean_and_flagged():
    assert run_dax_cli("Total := 1") == "No obvious DAX anti-patterns detected."
    flagged = run_dax_cli("M := CALCULATE([Sales], FILTER(T, T[Year] = 2026)) / [Count]")
    assert flagged != "No obvious DAX anti-patterns detected." and flagged.startswith("[")


# ---- tools + MCP manifest ----
def test_tool_definitions_handler_runs_audit(tmp_path):
    base = _temp_base(tmp_path)
    defs = create_tool_definitions(base_dir=base)
    assert defs[0]["name"] == "run_audit"
    assert defs[0]["input_schema"] == {"type": "object", "properties": {}, "required": []}
    res = defs[0]["handler"]()
    assert "summary" in res and "verdict" in res and "findings" in res
    # read-and-return: the tool writes no files (history/reports are the scheduled Job's role)
    assert not os.path.exists(os.path.join(base, "runs", "latest.json"))


def test_mcp_manifest_is_read_only_and_strips_handler(tmp_path):
    m = manifest(base_dir=_temp_base(tmp_path))
    assert m["readOnly"] is True and m["name"] == "fabric-audit-agent"
    tool = m["tools"][0]
    assert tool["name"] == "run_audit" and "handler" not in tool and "_handler" not in tool


def test_build_mcp_server_if_mcp_installed(tmp_path):
    pytest.importorskip("mcp")
    from fabric_audit_agent.mcp_server import build_mcp_server
    assert build_mcp_server(base_dir=_temp_base(tmp_path)) is not None


def test_mcp_advertised_schemas_mirror_input_schema(tmp_path):
    """FastMCP derives the client-visible schema from the wrapper signature, NOT from the
    input_schema dict -- so the wrapper signature must mirror each tool's schema exactly.
    Regression: a union-signature wrapper used to advertise phantom params on every tool and
    lose the `required` constraint on user_spike_history."""
    pytest.importorskip("mcp")
    from fabric_audit_agent.mcp_server import build_mcp_server

    server = build_mcp_server(base_dir=_temp_base(tmp_path))
    tools = {t.name: t.parameters for t in server._tool_manager.list_tools()}

    assert set(tools) == {"run_audit", "list_workspaces", "user_activity", "investigate_user",
                          "investigate_capacity_spike", "user_spike_history", "spike_events",
                          "capacity_peaks", "raw_events", "capacity_patterns", "describe_source",
                          "sample_events", "capacity_diagnostics", "analyze_dax", "diagnose",
                          "whats_changed", "user_timeline", "run_kql", "query_library"}

    # capacity_peaks: calendar-day timepoint-peak lens -- date/threshold/scope, none required
    assert set(tools["capacity_peaks"]["properties"]) == {
        "date", "minPctBase", "topN", "user", "item", "includeRefresh"}
    assert "required" not in tools["capacity_peaks"] or not tools["capacity_peaks"]["required"]

    # user_spike_history: user REQUIRED; window props + item optional -- no phantom topN/when
    ush = tools["user_spike_history"]
    assert set(ush["properties"]) == {"user", "days", "hours", "start", "end", "item"}
    assert "user" in ush.get("required", [])

    # capacity_patterns: window props + threshold overrides -- no phantom user/when/topN
    assert set(tools["capacity_patterns"]["properties"]) == {
        "days", "hours", "start", "end", "surgeUsers", "cuSpikePct"}

    # spike_events: window props + topN + item + format, none required
    se = tools["spike_events"]
    assert set(se["properties"]) == {"days", "hours", "start", "end", "topN", "item", "format"}
    assert "required" not in se or not se["required"]

    # raw_events: the complete-stream tool -- full scope surface, none required
    assert set(tools["raw_events"]["properties"]) == {
        "user", "item", "days", "hours", "start", "end", "topN", "order", "format"}

    # investigate_capacity_spike: when + days + windowMinutes
    assert set(tools["investigate_capacity_spike"]["properties"]) == {"when", "days", "windowMinutes"}

    # grounding tools (describe_source also carries the estimateKql pre-flight cost primitive)
    assert set(tools["describe_source"]["properties"]) == {"source", "table", "estimateKql"}
    assert set(tools["sample_events"]["properties"]) == {"source", "table", "n"}
    assert not tools["capacity_diagnostics"].get("properties")

    # query firewall tools (PR #11): run_kql requires kql + engine; query_library takes optional name
    rk = tools["run_kql"]
    assert set(rk["properties"]) == {"kql", "engine", "maxRows", "format"}
    assert set(rk.get("required", [])) == {"kql", "engine"}
    assert set(tools["query_library"]["properties"]) == {"name"}

    # no-arg tools advertise no properties
    assert not tools["run_audit"].get("properties")
    assert not tools["list_workspaces"].get("properties")


def test_mcp_required_param_enforced_and_call_flows(tmp_path):
    """Through FastMCP's own validation layer: a missing required param must be rejected,
    and a valid call must reach the real handler."""
    pytest.importorskip("mcp")
    import anyio
    from fabric_audit_agent.mcp_server import build_mcp_server

    server = build_mcp_server(base_dir=_temp_base(tmp_path))

    async def _run():
        # valid call reaches the handler (offline -> mock-labeled result)
        ok = await server._tool_manager.call_tool("user_spike_history", {"user": "alice@co"})
        # missing required user -> rejected by validation, not silently zeros
        try:
            await server._tool_manager.call_tool("user_spike_history", {"days": 7})
            rejected = False
        except Exception:
            rejected = True
        return ok, rejected

    ok, rejected = anyio.run(_run)
    assert ok is not None
    assert rejected is True


# ---- Databricks job wiring ----
def _opt_facts():
    return {"capacity": {"tenant": "Acme", "capacityId": "P", "sku": "F64", "memoryGB": 64,
                         "peakCuPct": 95, "peakAt": "t", "throttleMinutes": 20,
                         "refreshes": [{"workspace": "Fin", "dataset": "A", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 6},
                                       {"workspace": "Fin", "dataset": "B", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1}]}}


def test_run_job_with_injected_ports():
    delivered = {}
    appended = []
    env = run_job(
        collector={"collect": lambda: _opt_facts()},
        reasoner=create_stub_reasoner(),
        delivery={"deliver": lambda e: delivered.update(e)},
        store={"history": lambda: [], "append": lambda r: appended.append(r)},
        config=DEFAULT_CONFIG, now="2026-06-11T00:00:00Z", tenant="Acme",
    )
    assert env["success"] is True and env["data"]["tenant"] == "Acme"
    assert delivered and len(appended) == 1


def test_build_rest_config_filters_unset_urls():
    cfg = build_rest_config({"FABRIC_CAPACITY_URL": "u1", "FABRIC_DATASETS_URL": "u2", "UNRELATED": "x"})
    assert cfg == {"capacityUrl": "u1", "datasetsUrl": "u2"}


def test_run_job_missing_config_raises_with_empty_env():
    # collector unset -> _default_collector requires FABRIC_TENANT_ID etc.; empty env -> RuntimeError.
    with pytest.raises(RuntimeError):
        run_job(
            reasoner=create_stub_reasoner(),
            delivery={"deliver": lambda e: None},
            store={"history": lambda: [], "append": lambda r: None},
            config=DEFAULT_CONFIG, env={},
        )


# ---- dispatcher (__main__) ----
def test_main_dispatch_dax(capsys):
    from fabric_audit_agent.__main__ import main
    main(["dax", "Total := 1"])
    assert "No obvious DAX anti-patterns" in capsys.readouterr().out


def test_main_dispatch_no_args_prints_usage(capsys):
    from fabric_audit_agent.__main__ import main
    main([])
    assert capsys.readouterr().out.strip()


# ---- mine-queries (query-library growth loop, Task 4) ----

def _adhoc_line(kql, engine="capacity", verdict="allowed", **extra):
    """Reproduce the real `[adhoc-kql] ` audit-log line format (tools.py:114-131)."""
    rec = {"tag": "adhoc-kql", "engine": engine, "verdict": verdict, "kql": kql}
    rec.update(extra)
    return "[adhoc-kql] " + json.dumps(rec, ensure_ascii=False, separators=(",", ": "))


_NEW_SHAPE_KQL = (
    "CapacityEvents\n| where ingestion_time() > ago(1d)\n"
    "| where Foo == 1\n| project Foo\n| take 50"
)


def _new_shape_log_text(n=3, kql=_NEW_SHAPE_KQL):
    return "\n".join(_adhoc_line(kql) for _ in range(n))


def _write_small_library(path, entries=None):
    """A tiny, schema-valid, firewall-passing library file used as a temp `library_path` so a
    test never risks mutating the real package `query_library.json`."""
    if entries is None:
        entries = [
            {
                "name": "capacity-peak-windows-24h",
                "category": "capacity",
                "engine": "capacity",
                "description": "Per-30s-window CU% over the last 24h, highest first.",
                "kql": "CapacityEvents\n| where ingestion_time() > ago(1d)\n| take 50",
                "groundedIn": "test fixture",
            },
        ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return entries


def test_mine_queries_preview_writes_nothing_and_lists_candidate(tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)
    before = lib_path.read_bytes()

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    out = run_mine_queries_cli([str(logfile)], library_path=str(lib_path))

    assert lib_path.read_bytes() == before   # preview writes NOTHING
    assert "adhoc-capacity-" in out
    assert "hitCount" in out or "3" in out
    assert "Re-run with --write" in out


def test_mine_queries_preview_zero_candidates_message(tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)
    before = lib_path.read_bytes()

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")   # below default min_count=3

    out = run_mine_queries_cli([str(logfile)], library_path=str(lib_path))

    assert lib_path.read_bytes() == before
    assert "No promotable query shapes found" in out


def test_mine_queries_write_appends_preserves_existing_and_is_schema_valid(tmp_path):
    lib_path = tmp_path / "query_library.json"
    existing = _write_small_library(lib_path)

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    out = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))

    assert "Wrote 1 new query" in out
    updated = json.loads(lib_path.read_text(encoding="utf-8"))
    assert len(updated) == len(existing) + 1
    # existing entries preserved, in order, byte-for-byte as dicts
    assert updated[: len(existing)] == existing

    new_entry = updated[-1]
    assert new_entry["category"] == "adhoc-mined"
    assert new_entry["groundedIn"] == "mined from adhoc audit log"
    assert new_entry["engine"] == "capacity"
    assert new_entry["description"].strip()
    validate_adhoc_kql(new_entry["kql"])

    # same assertions test_query_library.py makes, applied to the mutated file
    names = [x["name"] for x in updated]
    assert len(names) == len(set(names))
    assert all(n == n.lower() and " " not in n for n in names)
    for x in updated:
        assert set(x) >= {"name", "category", "engine", "description", "kql", "groundedIn"}
        assert x["engine"] in ("capacity", "la")
        assert x["description"].strip() and x["groundedIn"].strip()
        validate_adhoc_kql(x["kql"])


def test_mine_queries_write_is_idempotent_byte_identical_on_rerun(tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))
    after_first = lib_path.read_bytes()

    out2 = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))
    after_second = lib_path.read_bytes()

    assert after_first == after_second   # re-run dedups vs the now-updated library -> no dupes
    assert "No promotable query shapes found" in out2


def test_mine_queries_write_zero_candidates_leaves_file_byte_identical(tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)
    before = lib_path.read_bytes()

    logfile = tmp_path / "audit.log"
    logfile.write_text("", encoding="utf-8")   # empty log -> zero candidates

    out = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))

    assert lib_path.read_bytes() == before
    assert "No promotable query shapes found" in out


def test_mine_queries_dedup_against_existing_same_resolved_path(tmp_path):
    """Proves the dedup-existing read and the --write target are the SAME resolved path: a
    candidate already present in the temp library (same shape once trailing take/limit is
    stripped) must NOT be re-added, even though the mined kql text differs cosmetically."""
    lib_path = tmp_path / "query_library.json"
    already_present = [
        {
            "name": "existing-foo-shape",
            "category": "adhoc-mined",
            "engine": "capacity",
            "description": "Pre-existing entry covering the same shape.",
            "kql": (
                "CapacityEvents\n| where ingestion_time() > ago(7d)\n"
                "| where Foo == 999\n| project Foo\n| take 999"
            ),
            "groundedIn": "mined from adhoc audit log",
        },
    ]
    _write_small_library(lib_path, entries=already_present)
    before = lib_path.read_bytes()

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")   # same shape, different literals

    out = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))

    assert lib_path.read_bytes() == before
    assert "No promotable query shapes found" in out


def test_mine_queries_reads_stdin(monkeypatch, tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(_new_shape_log_text()))

    out = run_mine_queries_cli(["-"], library_path=str(lib_path))

    assert lib_path.read_bytes()   # unchanged / still valid, no exception
    assert "adhoc-capacity-" in out


def test_mine_queries_missing_logfile_returns_clean_error(tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)

    out = run_mine_queries_cli([str(tmp_path / "does-not-exist.log")], library_path=str(lib_path))

    assert isinstance(out, str)
    assert "mine-queries" in out
    assert "does-not-exist.log" in out


def test_main_dispatch_mine_queries_preview_does_not_touch_real_library(tmp_path, capsys):
    from fabric_audit_agent.__main__ import main

    before = open(_REAL_LIBRARY, "rb").read()
    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    main(["mine-queries", str(logfile)])

    out = capsys.readouterr().out
    assert "Re-run with --write" in out or "No promotable query shapes found" in out
    after = open(_REAL_LIBRARY, "rb").read()
    assert after == before   # dispatch with no --write must never mutate the real package file


def test_mine_queries_bad_args_return_clean_strings_never_exit(tmp_path):
    # The load-bearing "never kill the process" property: argparse errors must come back as a
    # string, not a SystemExit/traceback that would take down `python -m fabric_audit_agent`.
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)
    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    for rest in (
        [],                                              # missing required positional logfile
        [str(logfile), "--min-count", "notanumber"],     # non-int value
        [str(logfile), "--nope"],                        # unknown flag
    ):
        out = run_mine_queries_cli(rest, library_path=str(lib_path))
        assert isinstance(out, str) and out.startswith("mine-queries:")


def test_mine_queries_missing_or_malformed_library_degrades_to_empty(tmp_path):
    # A missing or unreadable library must be treated as an empty catalog (existing=[]), not crash.
    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    # (a) library file absent
    missing = tmp_path / "absent.json"
    out = run_mine_queries_cli([str(logfile)], library_path=str(missing))
    assert "adhoc-capacity-" in out          # candidate found against an empty existing set
    assert not missing.exists()              # preview created nothing

    # (b) library file malformed JSON
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not valid json", encoding="utf-8")
    out2 = run_mine_queries_cli([str(logfile)], library_path=str(malformed))
    assert "adhoc-capacity-" in out2


def test_mine_queries_min_count_override_takes_effect(tmp_path):
    lib_path = tmp_path / "query_library.json"
    _write_small_library(lib_path)
    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(n=2), encoding="utf-8")   # only 2 hits

    # default min_count=3 -> below threshold -> nothing
    assert "No promotable query shapes found" in run_mine_queries_cli(
        [str(logfile)], library_path=str(lib_path))
    # --min-count 2 lowers the bar -> the shape is now promotable
    out = run_mine_queries_cli([str(logfile), "--min-count", "2"], library_path=str(lib_path))
    assert "adhoc-capacity-" in out


def test_mine_queries_write_refuses_to_clobber_present_but_malformed_library(tmp_path):
    # DATA-LOSS GUARD (opus final-review finding): a present-but-unparseable library must NOT be
    # silently overwritten with just the mined entries. --write must abort and leave it untouched.
    lib_path = tmp_path / "query_library.json"
    lib_path.write_text(
        '[{"name": "human-a", "category": "capacity", "engine": "capacity", '
        '"description": "d", "kql": "CapacityEvents | take 1", "groundedIn": "human"}] OOPS',
        encoding="utf-8",
    )  # valid list + trailing garbage -> malformed JSON
    before = lib_path.read_bytes()

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    out = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))

    assert "refusing to --write" in out
    assert lib_path.read_bytes() == before          # NOT clobbered
    assert b"human-a" in lib_path.read_bytes()       # the curated content survives


def test_mine_queries_write_creates_absent_library(tmp_path):
    # Absent file is the OK case: --write creates a new library with the mined entries.
    lib_path = tmp_path / "absent_library.json"
    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    out = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))

    assert "Wrote 1 new quer" in out
    created = json.loads(lib_path.read_text(encoding="utf-8"))
    assert isinstance(created, list) and len(created) == 1
    assert created[0]["name"].startswith("adhoc-capacity-")
    assert created[0]["category"] == "adhoc-mined"


def test_mine_queries_write_preserves_crlf_newlines(tmp_path):
    # NEWLINE GUARD (opus final-review finding): a CRLF library must stay CRLF after --write, so a
    # rewrite doesn't flip every line to LF and bury the one intended addition in the PR diff.
    lib_path = tmp_path / "query_library.json"
    entries = _write_small_library(lib_path)   # returns the entry list...
    with open(lib_path, "w", encoding="utf-8", newline="\r\n") as fh:   # ...rewrite it as CRLF
        json.dump(entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    logfile = tmp_path / "audit.log"
    logfile.write_text(_new_shape_log_text(), encoding="utf-8")

    out = run_mine_queries_cli([str(logfile), "--write"], library_path=str(lib_path))
    assert "Wrote 1 new quer" in out

    raw = lib_path.read_bytes()
    assert b"\r\n" in raw                                  # CRLF preserved
    assert b"\n" not in raw.replace(b"\r\n", b"")          # no bare LF introduced

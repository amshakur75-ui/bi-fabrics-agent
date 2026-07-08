"""Entry-point tests — cluster 9. CLIs (audit/eval/whatif/triggers/lifecycle/dax), the MCP
tool/manifest, and the Databricks job wiring (injected fake ports, no real SDK/network)."""
import os
import shutil

import pytest

from fabric_audit_agent.entrypoints import (
    run_audit_cli, run_eval_cli, run_whatif_cli, run_triggers_cli, run_lifecycle_cli, run_dax_cli,
)
from fabric_audit_agent.tools import create_tool_definitions
from fabric_audit_agent.mcp_server import manifest
from fabric_audit_agent.job import run_job, build_rest_config
from fabric_audit_agent.adapters import create_stub_reasoner
from fabric_audit_agent.config import DEFAULT_CONFIG

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
                          "raw_events", "capacity_patterns", "describe_source", "sample_events",
                          "capacity_diagnostics", "analyze_dax", "diagnose", "whats_changed",
                          "user_timeline"}

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

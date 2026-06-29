"""Tool definitions (Anthropic/MCP format) exposing the read-only audit as ``run_audit``.

Port of ``tools.js``. Each tool carries a ``handler(input)`` the host invokes; the audit is
READ-ONLY — the handler only reads (mock) telemetry and writes findings to local files, never
mutating any estate. ``data_agent.build_data_agent_manifest`` strips the handler for the
published manifest (keeps name/description/input_schema).
"""
import json
import os

from .adapters import create_mock_collector, create_stub_reasoner
from .pipeline import run_audit

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_real_or_mock(base, env):
    """Run the audit and RETURN the envelope — read-only and **write-free**. A Databricks App
    container can't write to /Volumes, and the interactive tool doesn't need to persist: history
    and report files are the scheduled Job's role. Uses live sources when configured
    (FABRIC_CAPACITIES_URL / FABRIC_KUSTO_* / FABRIC_CSV_PATHS), else the offline mock."""
    from .config import DEFAULT_CONFIG, merge_config
    raw = env.get("FABRIC_AUDIT_CONFIG")
    config = merge_config(json.loads(raw)) if raw else DEFAULT_CONFIG

    if (env.get("FABRIC_CSV_PATHS") or env.get("FABRIC_CLIENT_ID")
            or env.get("FABRIC_KUSTO_CLUSTER") or env.get("FABRIC_CAPACITY_EVENTS_CLUSTER")):
        from .job import build_collector_from_env, _default_reasoner, _wants_llm
        collector = build_collector_from_env(env)
        reasoner = _default_reasoner(env, config) if _wants_llm(env) else create_stub_reasoner(config)
    else:
        collector = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))
        reasoner = create_stub_reasoner(config)

    return run_audit(collector, reasoner, {"deliver": lambda e: None}, store=None,
                     config=config, agent_id="fabric-audit-agent")


def _build_collector(env):
    """Return a live collector if any source is configured, else None."""
    if not (env.get("FABRIC_CSV_PATHS") or env.get("FABRIC_CLIENT_ID")
            or env.get("FABRIC_KUSTO_CLUSTER") or env.get("FABRIC_CAPACITY_EVENTS_CLUSTER")
            or env.get("FABRIC_LA_WORKSPACE_ID")):
        return None
    from .job import build_collector_from_env
    return build_collector_from_env(env)


def create_tool_definitions(base_dir=None):
    base = base_dir if base_dir is not None else _BASE

    def run_audit_handler(_input=None):
        envelope = _run_real_or_mock(base, os.environ)
        d = envelope["data"]
        result = {
            "summary": envelope["summary"],
            "verdict": d["verdict"],
            "findings": d["findings"],
        }
        for key in ("digest", "narrative", "roadmap", "healthScore", "staggerPlan", "correlations", "forecast"):
            if d.get(key):
                result[key] = d[key]
        return result

    def list_workspaces_handler(_input=None):
        """Return all workspaces, items, and users from live sources (LA + Eventhouse).
        Use this to answer questions about workspace inventory, activity, or top users
        across the full estate without running the full audit pipeline."""
        collector = _build_collector(os.environ)
        if collector is None:
            mock_path = os.path.join(base, "fixtures", "estate.json")
            collector = create_mock_collector(mock_path)
        facts = collector["collect"]()

        items = facts.get("items") or []
        users = facts.get("users") or []

        # Group items by workspace
        ws_map = {}
        for item in items:
            ws = item.get("workspace") or "Unknown"
            entry = ws_map.setdefault(ws, {"workspace": ws, "items": [], "totalCuSeconds": 0})
            entry["items"].append({
                "name": item.get("name"),
                "cuSeconds": item.get("cuSeconds", 0),
                "sharePct": round(item.get("sharePct", 0), 1),
                "topUsers": item.get("topUsers", []),
                "userCount": item.get("userCount", 0),
            })
            entry["totalCuSeconds"] += item.get("cuSeconds", 0)

        workspaces = sorted(ws_map.values(), key=lambda x: -x["totalCuSeconds"])
        return {
            "workspaces": workspaces,
            "topUsers": users[:10],
            "totalWorkspaces": len(workspaces),
            "totalItems": len(items),
            "source": "Log Analytics + Eventhouse (merged)",
        }

    return [
        {
            "name": "run_audit",
            "description": (
                "Run a read-only Fabric/Power BI capacity audit and return prioritized findings, "
                "capacity verdict (optimize vs size-up), health score, and per-user attribution. "
                "Use this for capacity health questions, throttling analysis, and optimization advice. "
                "Read-only: never modifies anything."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": run_audit_handler,
        },
        {
            "name": "list_workspaces",
            "description": (
                "List all workspaces, their items, and top users from live sources (Log Analytics "
                "and/or Workspace Monitoring Eventhouse). Use this to answer questions about workspace "
                "inventory, activity across the estate, who is using which workspace, or to find a "
                "specific workspace before drilling into it with run_audit."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "handler": list_workspaces_handler,
        },
    ]

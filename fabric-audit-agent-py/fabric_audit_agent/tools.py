"""Tool definitions (Anthropic/MCP format) exposing the read-only audit as ``run_audit``.

Port of ``tools.js``. Each tool carries a ``handler(input)`` the host invokes; the audit is
READ-ONLY — the handler only reads (mock) telemetry and writes findings to local files, never
mutating any estate. ``data_agent.build_data_agent_manifest`` strips the handler for the
published manifest (keeps name/description/input_schema).
"""
import os

from .adapters import (
    create_mock_collector, create_stub_reasoner, create_file_delivery, create_local_store,
)
from .pipeline import run_audit

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_tool_definitions(base_dir=None):
    base = base_dir if base_dir is not None else _BASE

    def run_audit_handler(_input=None):
        collector = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))
        reasoner = create_stub_reasoner()
        store = create_local_store(os.path.join(base, "runs", "history.json"))
        delivery = create_file_delivery(os.path.join(base, "runs", "latest.json"))
        envelope = run_audit(collector, reasoner, delivery, store=store, agent_id="fabric-audit-agent")
        return {
            "summary": envelope["summary"],
            "verdict": envelope["data"]["verdict"],
            "digest": envelope["data"].get("digest"),
            "findings": envelope["data"]["findings"],
        }

    return [{
        "name": "run_audit",
        "description": (
            "Run a read-only Fabric/Power BI audit over the current estate and return prioritized "
            "findings, a digest, and the capacity verdict (optimize vs size-up). Read-only: never "
            "modifies anything."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "handler": run_audit_handler,
    }]

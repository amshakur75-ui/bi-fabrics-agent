"""Fabric Data Agent / MCP manifest from tool definitions. Port of ``core/data-agent.js``. Pure."""


def build_data_agent_manifest(tool_definitions=None):
    tool_definitions = tool_definitions or []
    return {
        "name": "fabric-audit-agent",
        "displayName": "[C] Fabric Audit Agent",
        "description": "Read-only Microsoft Fabric / Power BI capacity & performance advisor. Ask it to audit the estate or explain an issue.",
        "instructions": "Call run_audit to sweep the estate and return prioritized findings, a digest, and the capacity verdict. The agent is strictly read-only.",
        "readOnly": True,
        "tools": [{"name": t.get("name"), "description": t.get("description"), "input_schema": t.get("input_schema")} for t in tool_definitions],
    }

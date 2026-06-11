"""MCP server exposing the read-only audit as the ``run_audit`` tool (the pull surface).

Port of the Node MCP/data-agent wiring. Uses the Python ``mcp`` package (lazy import, optional
``mcp`` extra). The exact server wiring is finalized against the MCP host at deploy; the tool
*logic* lives in ``tools.create_tool_definitions`` and is fully testable offline.
"""
from .tools import create_tool_definitions
from .data_agent import build_data_agent_manifest


def manifest(base_dir=None):
    """Fabric Data Agent / MCP manifest (read-only) for host registration."""
    return build_data_agent_manifest(create_tool_definitions(base_dir))


def build_mcp_server(base_dir=None):
    """Build a FastMCP server registering ``run_audit``. Requires the optional ``mcp`` dep."""
    from mcp.server.fastmcp import FastMCP  # lazy: optional `mcp` extra

    run_audit_def = next(d for d in create_tool_definitions(base_dir) if d["name"] == "run_audit")
    server = FastMCP("fabric-audit-agent")

    @server.tool(name="run_audit", description=run_audit_def["description"])
    def run_audit():
        return run_audit_def["handler"]()

    return server


def main():
    build_mcp_server().run()


if __name__ == "__main__":
    main()

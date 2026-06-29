"""MCP server exposing the read-only audit as the ``run_audit`` tool (the pull surface).

Port of the Node MCP/data-agent wiring. Uses the Python ``mcp`` package (lazy import, optional
``mcp`` extra). The exact server wiring is finalized against the MCP host at deploy; the tool
*logic* lives in ``tools.create_tool_definitions`` and is fully testable offline.
"""
import os

from .tools import create_tool_definitions
from .data_agent import build_data_agent_manifest


def manifest(base_dir=None):
    """Fabric Data Agent / MCP manifest (read-only) for host registration."""
    return build_data_agent_manifest(create_tool_definitions(base_dir))


def build_mcp_server(base_dir=None, host="0.0.0.0", port=8000):
    """Build a FastMCP server registering EVERY tool from ``create_tool_definitions``
    (``run_audit``, ``list_workspaces``, …). Requires the optional ``mcp`` dep."""
    from mcp.server.fastmcp import FastMCP  # lazy: optional `mcp` extra

    server = FastMCP("fabric-audit-agent", host=host, port=port)

    for _def in create_tool_definitions(base_dir):
        def _make(handler):
            def _tool():
                return handler()
            return _tool
        # register each tool's no-arg handler under its own name/description
        server.tool(name=_def["name"], description=_def["description"])(_make(_def["handler"]))

    return server


def main():
    """Local default: stdio. On a Databricks App set MCP_TRANSPORT=streamable-http (served at /mcp,
    port 8000) so a Mosaic AI agent / MCP client can reach it."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    server = build_mcp_server(port=int(os.environ.get("MCP_PORT", "8000")))
    server.run(transport=transport)


if __name__ == "__main__":
    main()

"""MCP server exposing the read-only audit as the ``run_audit`` tool (the pull surface).

Port of the Node MCP/data-agent wiring. Uses the Python ``mcp`` package (lazy import, optional
``mcp`` extra). The exact server wiring is finalized against the MCP host at deploy; the tool
*logic* lives in ``tools.create_tool_definitions`` and is fully testable offline.
"""
import inspect
import os

from .tools import create_tool_definitions
from .data_agent import build_data_agent_manifest

_JSON_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _make_tool_fn(handler, input_schema):
    """Return a wrapper whose SIGNATURE mirrors *input_schema* exactly.

    FastMCP derives the schema MCP clients see from the function signature (via
    ``inspect.signature``, which honors ``__signature__``) — the ``input_schema`` dict in the
    tool definition is never read by FastMCP. A previous union-signature wrapper therefore
    advertised phantom params on every tool (e.g. ``capacity_patterns`` showing ``user``/``topN``
    it ignores) and lost ``required`` enforcement (``user_spike_history`` without ``user``
    returned zeros instead of a validation error). Mirroring the schema per tool fixes both:
    required props have no default (client MUST supply them), optional props default to None
    and are dropped from the payload so handler-side defaults apply.
    """
    props = (input_schema or {}).get("properties") or {}
    required = set((input_schema or {}).get("required") or [])

    if not props:
        def _tool():
            return handler()
        return _tool

    params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=(inspect.Parameter.empty if name in required else None),
            annotation=_JSON_TYPE_MAP.get(spec.get("type"), str),
        )
        for name, spec in props.items()
    ]

    def _tool(**kwargs):
        payload = {k: v for k, v in kwargs.items() if v is not None}
        return handler(payload)

    _tool.__signature__ = inspect.Signature(params)
    _tool.__annotations__ = {p.name: p.annotation for p in params}
    return _tool


def manifest(base_dir=None):
    """Fabric Data Agent / MCP manifest (read-only) for host registration."""
    return build_data_agent_manifest(create_tool_definitions(base_dir))


def build_mcp_server(base_dir=None, host="0.0.0.0", port=8000):
    """Build a FastMCP server registering EVERY tool from ``create_tool_definitions``
    (``run_audit``, ``list_workspaces``, ``user_activity``, ``investigate_user``,
    ``investigate_capacity_spike``, ``user_spike_history``, ``spike_events``, ``raw_events``,
    ``capacity_patterns``, ``describe_source``, ``sample_events``, ``capacity_diagnostics``).
    Each tool's advertised MCP schema mirrors its authored ``input_schema`` exactly (per-tool
    signature derived by ``_make_tool_fn``), so required props are enforced and no phantom params
    are advertised. Requires the optional ``mcp`` dep."""
    from mcp.server.fastmcp import FastMCP  # lazy: optional `mcp` extra

    server = FastMCP("fabric-audit-agent", host=host, port=port)

    for _def in create_tool_definitions(base_dir):
        server.tool(name=_def["name"], description=_def["description"])(
            _make_tool_fn(_def["handler"], _def.get("input_schema")))

    return server


def main():
    """Local default: stdio. On a Databricks App set MCP_TRANSPORT=streamable-http (served at /mcp,
    port 8000) so a Mosaic AI agent / MCP client can reach it."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    server = build_mcp_server(port=int(os.environ.get("MCP_PORT", "8000")))
    server.run(transport=transport)


if __name__ == "__main__":
    main()

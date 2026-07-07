"""MCP server exposing the read-only audit as the ``run_audit`` tool (the pull surface).

Port of the Node MCP/data-agent wiring. Uses the Python ``mcp`` package (lazy import, optional
``mcp`` extra). The exact server wiring is finalized against the MCP host at deploy; the tool
*logic* lives in ``tools.create_tool_definitions`` and is fully testable offline.
"""
import os

from .tools import create_tool_definitions
from .data_agent import build_data_agent_manifest


def _make_no_arg(handler):
    """Return a zero-parameter tool function wrapping *handler*."""
    def _tool():
        return handler()
    return _tool


def _make_with_args(handler):
    """Return a union-signature tool function wrapping *handler*.

    Covers the union of all arg-taking tool schemas:
      user     (user_activity, investigate_user, user_spike_history)
      days     (investigate_user, user_spike_history, spike_events, capacity_patterns)
      when     (investigate_capacity_spike)
      topN     (spike_events)
      hours, start, end   (sub-day / absolute time windows -- user_spike_history, spike_events,
                            capacity_patterns)
      format, order, source, table, n   (additional per-tool options)

    ``days`` and ``topN`` default to None (NOT 30/5) so a handler can tell "omitted" from
    "explicitly requested" and apply its OWN real default -- forcing 30/5 here meant a handler's
    own default (e.g. spike_events' topN=100 raw-event path, capacity_patterns' days=1
    special-casing) could never trigger, since this wrapper always sent a non-None value.

    Only non-None values are forwarded (nullish, not falsy -- 0/""/False are meaningful and
    still forwarded) so handlers can apply their own defaults for anything omitted.
    """
    def _tool(user: str = None, days: int = None, when: str = None, topN: int = None,
              hours: float = None, start: str = None, end: str = None,
              format: str = None, order: str = None, source: str = None,
              table: str = None, n: int = None):
        payload = {k: v for k, v in {
            "user": user, "days": days, "when": when, "topN": topN,
            "hours": hours, "start": start, "end": end,
            "format": format, "order": order, "source": source,
            "table": table, "n": n,
        }.items() if v is not None}
        return handler(payload)
    return _tool


def manifest(base_dir=None):
    """Fabric Data Agent / MCP manifest (read-only) for host registration."""
    return build_data_agent_manifest(create_tool_definitions(base_dir))


def build_mcp_server(base_dir=None, host="0.0.0.0", port=8000):
    """Build a FastMCP server registering EVERY tool from ``create_tool_definitions``
    (``run_audit``, ``list_workspaces``, ``user_activity``, ``investigate_user``,
    ``investigate_capacity_spike``, ``user_spike_history``, ``spike_events``,
    ``capacity_patterns``). No-arg tools are registered without parameters; arg-taking
    tools expose the union of ``user``, ``days``, ``when``, ``topN``, ``hours``, ``start``,
    ``end``, ``format``, ``order``, ``source``, ``table``, and ``n`` as optional FastMCP params
    (only non-None values reach the handler; see ``_make_with_args``).
    Requires the optional ``mcp`` dep."""
    from mcp.server.fastmcp import FastMCP  # lazy: optional `mcp` extra

    server = FastMCP("fabric-audit-agent", host=host, port=port)

    for _def in create_tool_definitions(base_dir):
        props = (_def.get("input_schema") or {}).get("properties") or {}
        if not props:
            # No-arg tools: register a zero-parameter function.
            server.tool(name=_def["name"], description=_def["description"])(_make_no_arg(_def["handler"]))
        else:
            # Arg-taking tools (user_activity, investigate_user, investigate_capacity_spike):
            # expose the union of possible params; FastMCP will pass only those provided.
            # The shared signature covers all three tools' schemas (user: str, days: int, when: str).
            server.tool(name=_def["name"], description=_def["description"])(_make_with_args(_def["handler"]))

    return server


def main():
    """Local default: stdio. On a Databricks App set MCP_TRANSPORT=streamable-http (served at /mcp,
    port 8000) so a Mosaic AI agent / MCP client can reach it."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    server = build_mcp_server(port=int(os.environ.get("MCP_PORT", "8000")))
    server.run(transport=transport)


if __name__ == "__main__":
    main()

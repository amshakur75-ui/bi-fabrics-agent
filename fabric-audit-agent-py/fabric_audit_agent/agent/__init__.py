"""Tools-side agent helpers. Per ADR-001 (2026-07-29) the system prompt, tool loop, and
investigator all live in the chat app (``fabric-audit-agent-app``); this subpackage now
contains only ``tools_anthropic.py`` (the Anthropic tool-def / dispatch adapter), which the
chat app's investigator imports to convert MCP tool defs into Anthropic tool-use shape."""

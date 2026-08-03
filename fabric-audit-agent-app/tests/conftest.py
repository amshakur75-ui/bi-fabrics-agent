"""Test-time path setup.

Since ADR-002, both the core logic (``fabric_audit_agent`` — including ``tools.py`` and
``create_tool_definitions``) and the chat app (``agent_server``) live in this repo. Add the app
root to ``sys.path`` so tests import them zero-config from the repo root, without requiring
``pip install -e .`` first. (The MCP protocol server lives in the sibling ``fabric-audit-mcp``
repo and is exercised by that repo's own tests — the agent app never imports it.)
"""
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1]   # fabric-audit-agent-app/
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

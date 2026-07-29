"""Test-time path setup for the chat app.

The chat app talks to the MCP tools package over HTTP in production
(``DatabricksMCPClient``). But the offline eval harness (``eval_score.py`` →
``investigator.investigate`` → ``create_tool_definitions`` from ``fabric_audit_agent.tools``)
imports the tools package in-process to build the dispatch table for scripted-client tests.

That import only resolves when ``fabric-audit-agent-py`` is on ``sys.path``. In the monorepo
layout it sits as a sibling directory; this conftest adds it if it isn't already importable,
so tests run zero-config from the repo root.

In CI or a shipped dev env, `pip install -e ../fabric-audit-agent-py` is the preferred setup;
this fallback exists so a fresh clone doesn't fail on import before that step happens."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_APP_ROOT = _HERE.parents[1]                     # fabric-audit-agent-app/
_REPO_ROOT = _APP_ROOT.parent                    # bi-fabrics-agent/
_PKG_ROOT = _REPO_ROOT / "fabric-audit-agent-py"

try:
    import fabric_audit_agent  # noqa: F401
except ImportError:
    if _PKG_ROOT.exists():
        sys.path.insert(0, str(_PKG_ROOT))

# Make the chat app's own package importable too, without requiring the app to be pip-installed.
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

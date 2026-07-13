"""Investigation-harness loop supports (B2): pre-call step budget + plain-language trail +
playbook rule-locks. Offline — reads the module the same stubbed way as test_agent_server."""
import re
from pathlib import Path

from tests.test_agent_server import _agent  # module loaded with stubbed databricks/mlflow


# ---- B2: pre-call deterministic budget classifier ----
def test_budget_boundaries_pinned():
    # Review-pinned boundary cases: a status lookup stays lean; an investigation earns depth.
    assert _agent._step_budget("is capacity healthy?") == 6
    assert _agent._step_budget("list the workspaces") == 6
    assert _agent._step_budget("why did the capacity throttle yesterday?") == 12
    assert _agent._step_budget("investigate the spike at 2pm") == 12
    assert _agent._step_budget("has this happened before?") == 12
    assert _agent._step_budget("who is driving the load this week") == 12
    assert _agent._step_budget("") == 6
    assert _agent._step_budget(None) == 6


# ---- B2: trail is plain language, never tool names/inputs ----
def test_trail_is_plain_language_no_tool_names():
    traj = [{"tool": "run_audit", "input": {}},
            {"tool": "investigate_capacity_spike", "input": {"topN": 25}},
            {"tool": "user_timeline", "input": {"user": "alice@co"}}]
    trail = _agent._plain_trail(traj)
    assert len(trail) == 3
    joined = " ".join(trail)
    assert "run_audit" not in joined and "investigate_capacity_spike" not in joined
    assert "running the capacity audit" in joined          # the progress phrase, not the tool name
    assert "alice@co" in joined                            # scope hint survives (viewer == requester)


def test_trail_none_safe():
    assert _agent._plain_trail(None) == []
    assert _agent._plain_trail([{"bad": "entry"}]) != None  # noqa: E711 — must not raise


# ---- B1: playbook rule-locks (markers must survive any future prompt edit) ----
_MARKERS = [
    "Investigation Mode",
    "CONFIRM the problem exists",
    "RULED",                                # ruled-out is a finding
    "different claims",                     # throttle vs pressure
    "never billed CU",
    "INCONCLUSIVE",
    "read/query only",
    "Narrate the chase",
    "who should act",
]


def test_playbook_markers_locked_in_system_prompt():
    for m in _MARKERS:
        assert m in _agent._SYSTEM, f"playbook marker missing: {m!r}"


def test_playbook_keeps_lean_default_for_lookups():
    # The precedence rule: narration is for investigations; lookups keep the lean default.
    assert "simple lookups keep the lean default" in _agent._SYSTEM

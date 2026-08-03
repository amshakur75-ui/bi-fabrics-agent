from fabric_audit_agent.tools import create_tool_definitions
from fabric_audit_agent.agent.tools_anthropic import to_anthropic_tools, build_dispatch


def test_to_anthropic_tools_strips_handler_keeps_schema():
    defs = create_tool_definitions()
    tools = to_anthropic_tools(defs)
    names = {t["name"] for t in tools}
    assert {"run_audit", "list_workspaces", "user_activity",
            "investigate_user", "investigate_capacity_spike"} <= names
    for t in tools:
        assert set(t.keys()) == {"name", "description", "input_schema"}   # NO handler leaks to the model


def test_build_dispatch_maps_names_to_callables():
    dispatch = build_dispatch(create_tool_definitions())
    assert callable(dispatch["investigate_user"])
    out = dispatch["investigate_user"]({"user": "nobody@co", "days": 30})   # offline -> mock -> abstain
    assert out["abstained"] is True and out["source"] == "mock"

"""The framework-agnostic agent core: assemble the system prompt + read-only tools, run the loop,
return the answer + trajectory. The MLflow ResponsesAgent wrapper (responses_agent.py) is a thin
adapter over this; tests drive it directly with a fake client."""
from ..tools import create_tool_definitions
from .system_prompt import build_system_prompt
from .tools_anthropic import to_anthropic_tools, build_dispatch
from .loop import run_tool_loop


def investigate(messages, client, *, model="fabric-claude", base_dir=None, max_steps=6):
    defs = create_tool_definitions(base_dir)
    result = run_tool_loop(
        client, model=model, system=build_system_prompt(), messages=list(messages),
        tools=to_anthropic_tools(defs), dispatch=build_dispatch(defs), max_steps=max_steps,
    )
    return {"output_text": result["text"], "trajectory": result["trajectory"],
            "stoppedReason": result["stoppedReason"]}

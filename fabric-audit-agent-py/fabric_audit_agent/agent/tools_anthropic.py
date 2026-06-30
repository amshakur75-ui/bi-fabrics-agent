"""Adapt the read-only tool definitions to the Anthropic Messages `tools` format + a dispatch map.
The handler is NEVER exposed to the model — only name/description/input_schema."""


def to_anthropic_tools(tool_defs):
    return [{"name": d["name"], "description": d["description"], "input_schema": d["input_schema"]}
            for d in tool_defs]


def build_dispatch(tool_defs):
    return {d["name"]: d["handler"] for d in tool_defs}

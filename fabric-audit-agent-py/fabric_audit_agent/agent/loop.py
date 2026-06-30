"""The read-only ReAct tool-loop: plan -> targeted tool call -> feed result back -> repeat under a
hard step budget, forcing an answer on the last step. Identical tool calls are de-duplicated (sound
because every tool is read-only). Pure: the Anthropic client is injected."""
import json


def _blocks_to_dicts(content):
    out = []
    for b in content:
        t = getattr(b, "type", None)
        if t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


def run_tool_loop(client, *, model, system, messages, tools, dispatch, max_steps=6):
    messages = list(messages)
    trajectory, cache = [], {}
    for step in range(max_steps):
        use_tools = tools if step < max_steps - 1 else []   # force-answer on the last allowed step
        resp = client.messages.create(model=model, max_tokens=4096, system=system,
                                      messages=messages, tools=use_tools)
        if getattr(resp, "stop_reason", None) != "tool_use":
            text = "".join(getattr(b, "text", "") for b in resp.content
                           if getattr(b, "type", None) == "text")
            return {"text": text, "trajectory": trajectory, "stoppedReason": "answer"}

        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        results = []
        for b in resp.content:
            if getattr(b, "type", None) != "tool_use":
                continue
            key = (b.name, json.dumps(b.input, sort_keys=True, ensure_ascii=False))
            if key in cache:
                result = {"note": "duplicate read-only tool call skipped; see earlier result",
                          "cached": cache[key]}
            else:
                handler = dispatch.get(b.name)
                result = handler(b.input) if handler else {"error": f"unknown tool {b.name}"}
                cache[key] = result
            trajectory.append({"tool": b.name, "input": b.input})
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": json.dumps(result, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})

    return {"text": "", "trajectory": trajectory, "stoppedReason": "budget"}

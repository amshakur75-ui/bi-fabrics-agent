"""Sync ReAct tool-loop used by the eval harness. Ported from
``fabric_audit_agent/agent/loop.py`` per ADR-001.

Production runs the ASYNC twin ``_run_tool_loop`` in ``agent.py`` (uvicorn / streaming). This
sync version is only used by ``investigator.investigate()``, which the offline eval harness
drives with a ``ScriptedClient``. The two loops must stay structurally in sync: dedup of
identical read-only calls, budget-exhaustion nudge injected before the forced-answer step,
and ``wrap_untrusted`` on every tool result.

Pure: the Anthropic client is injected."""
import json
from .system_prompt import wrap_untrusted
from .loop_hooks import pretool_pbi_usage_redirect, post_tool_result


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
    trajectory, cache, tool_results = [], {}, []
    for step in range(max_steps):
        use_tools = tools if step < max_steps - 1 else []   # force-answer on the last allowed step
        if not use_tools and tools and step == max_steps - 1 and trajectory:
            # Withholding tools alone doesn't tell the model WHY -- observed live, it narrated
            # its next intended tool call ("Let me pull...") instead of answering. Say it plainly.
            messages.append({"role": "user", "content": (
                "[SYSTEM] Tool budget exhausted -- no more tool calls are possible. Give your "
                "complete final answer NOW from the evidence already gathered. Do not propose, "
                "describe, or promise further tool calls.")})
        resp = client.messages.create(model=model, max_tokens=4096, system=system,
                                      messages=messages, tools=use_tools)
        if getattr(resp, "stop_reason", None) != "tool_use":
            text = "".join(getattr(b, "text", "") for b in resp.content
                           if getattr(b, "type", None) == "text")
            if not text.strip():
                # Observed live: a slow/loaded serving endpoint ended the turn with NO text after
                # the tool work, so the user saw progress then a blank answer. One bounded, tool-less
                # retry that demands a plain-text answer; then a non-empty fallback. Never blank.
                messages.append({"role": "user", "content": (
                    "[SYSTEM] Your previous reply was empty. Write your COMPLETE final answer now, "
                    "in plain text, from the evidence already gathered. Do not call any tools.")})
                resp = client.messages.create(model=model, max_tokens=4096, system=system,
                                              messages=messages, tools=[])
                text = "".join(getattr(b, "text", "") for b in resp.content
                               if getattr(b, "type", None) == "text")
            if not text.strip():
                text = ("I pulled the capacity and activity readings but couldn't compose a written "
                        "conclusion this time — the model returned an empty response. Nothing was "
                        "modified (all tools are read-only). Please ask again, or say "
                        "\"summarize what you found\".")
            return {"text": text, "trajectory": trajectory, "toolResults": tool_results,
                    "stoppedReason": "answer"}

        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        results = []
        for b in resp.content:
            if getattr(b, "type", None) != "tool_use":
                continue
            # Hook (c) PRE-tool-execution (plan 3.10 / 24b): redirect a hand-authored Power-BI
            # field/measure usage query to the authoritative resolver instead of executing it.
            # Shared with the async twin via loop_hooks so the two loops cannot drift.
            redirect = pretool_pbi_usage_redirect(b.name, b.input)
            key = (b.name, json.dumps(b.input, sort_keys=True, ensure_ascii=False))
            if redirect is not None:
                result = redirect
            elif key in cache:
                result = {"note": "duplicate read-only tool call skipped; see earlier result",
                          "cached": cache[key]}
            else:
                handler = dispatch.get(b.name)
                result = handler(b.input) if handler else {"error": f"unknown tool {b.name}"}
                # Hooks (a)+(b) POST-tool-execution: auto-analysis nudge on row results +
                # ExecutingUser identity-display normalization at the structured-row layer.
                result = post_tool_result(b.name, b.input, result)
                cache[key] = result
                tool_results.append({"tool": b.name, "result": result})
            trajectory.append({"tool": b.name, "input": b.input})
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": wrap_untrusted(json.dumps(result, ensure_ascii=False))})
        messages.append({"role": "user", "content": results})

    return {"text": "Investigation stopped at the step budget without a conclusion.",
            "trajectory": trajectory, "toolResults": tool_results, "stoppedReason": "budget"}

"""Databricks **App** agent handler (Phase-2 deploy) — hosts our tested investigation loop on a
Databricks App and reaches the read-only tools through the **existing MCP App**.

This is the `agent_server/agent.py` you drop into the official template
`databricks/app-templates/agent-openai-advanced` (the lowest-error path — it bundles the
@invoke/@stream server, chat UI, OBO, and DABs deploy). DEPLOY-ONLY: imports `mlflow.genai.*`,
`databricks.*`, `databricks_mcp`, and an LLM client — none are needed to run the test suite, and
this file is NOT imported by the `fabric_audit_agent` package.

WHAT STAYS THE SAME (tested, untouched): the loop + system prompt —
`fabric_audit_agent.agent.loop.run_tool_loop` + `...system_prompt.build_system_prompt`.
WHAT THIS FILE DOES (deploy glue only): per request, build the OBO user client, the Claude client,
and source the tools from the MCP server, then run the loop.

⚠️ VERIFY AT DEPLOY (3 integration points — these are env/SDK specific; confirm against the cloned
template + the `databricks_mcp` package + your workspace; see docs/PHASE2-DEPLOY.md):
  (A) the `mlflow.genai.agent_server` decorators (`invoke`/`stream`) + the Responses request/response
      types — copy them from the template you clone, don't assume.
  (B) `DatabricksMCPClient` method/field names (`list_tools()` / `call_tool(...)` and the tool
      schema fields).
  (C) how this workspace's Claude serving endpoint wants to be called (Anthropic Messages vs OpenAI
      chat-completions) — the B1 smoke; the loop only needs an object with `.messages.create(...)`
      returning content blocks + `stop_reason` (adapter in PHASE2-DEPLOY.md §B1-alt).
"""
import os

from mlflow.genai.agent_server import invoke, stream            # (A) verify import path in the template
from mlflow.types.responses import (ResponsesAgentRequest, ResponsesAgentResponse,
                                    ResponsesAgentStreamEvent)
from databricks.sdk import WorkspaceClient
from databricks_ai_bridge import get_user_workspace_client      # OBO: build INSIDE the handler

from fabric_audit_agent.agent.loop import run_tool_loop          # tested, unchanged
from fabric_audit_agent.agent.system_prompt import build_system_prompt

_MODEL = os.environ.get("DATABRICKS_CLAUDE_ENDPOINT", "databricks-claude-opus-4-7")
_MCP_URL = os.environ["FABRIC_MCP_URL"]                          # e.g. https://mcp-fabric-audit.<host>/mcp


def _build_claude_client(ws):
    """Anthropic-Messages-shaped client for the in-tenant Claude serving endpoint, under the user's
    identity. (C) VERIFY the protocol with the B1 smoke; swap in the §B1-alt adapter if the endpoint
    only speaks OpenAI chat-completions. The loop only needs `.messages.create(...)`."""
    import anthropic
    return anthropic.Anthropic(base_url=f"{ws.config.host}/serving-endpoints/{_MODEL}",
                               api_key=ws.config.token)


def _mcp_tools_and_dispatch(ws):
    """Source the read-only tools from the EXISTING MCP App (not in-process). Returns the Anthropic
    `tools` list + a name→callable dispatch that calls the MCP over HTTP. (B) VERIFY the
    DatabricksMCPClient API against the `databricks_mcp` package."""
    from databricks_mcp import DatabricksMCPClient
    mcp = DatabricksMCPClient(server_url=_MCP_URL, workspace_client=ws)
    listed = mcp.list_tools()                                   # (B) verify return shape
    tools = [{"name": t.name, "description": t.description, "input_schema": t.inputSchema}
             for t in listed]
    dispatch = {t.name: (lambda name: (lambda inp: mcp.call_tool(name, inp or {})))(t.name)
                for t in listed}
    return tools, dispatch


def _messages_from_request(request):
    msgs = []
    for item in getattr(request, "input", None) or []:
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role:
            msgs.append({"role": role, "content": content})
    return msgs or [{"role": "user", "content": ""}]


def _run(request):
    """Sync core: OBO client → Claude client + MCP tools → our tested loop. (For a long investigation
    you'd run this off the event loop; v1 sync is fine under the step budget.)"""
    ws = get_user_workspace_client()                            # OBO — only valid inside the handler
    tools, dispatch = _mcp_tools_and_dispatch(ws)
    result = run_tool_loop(_build_claude_client(ws), model=_MODEL, system=build_system_prompt(),
                           messages=_messages_from_request(request), tools=tools, dispatch=dispatch,
                           max_steps=6)
    return result


@invoke()
async def non_streaming(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    r = _run(request)
    from mlflow.types.responses import ResponsesAgent           # for create_text_output_item helper
    text_item = ResponsesAgent.create_text_output_item(text=r["text"], id="msg_1")
    return ResponsesAgentResponse(
        output=[text_item],
        custom_outputs={"trajectory": r["trajectory"], "toolResults": r.get("toolResults"),
                        "stoppedReason": r["stoppedReason"]},
    )


@stream()
async def streaming(request: ResponsesAgentRequest):
    """Minimal streaming wrapper (the ~120s gateway timeout is beaten by streaming + the step budget).
    v1 emits the final answer as one event; refine to token streaming later. VERIFY the event type
    against the cloned template."""
    r = _run(request)
    from mlflow.types.responses import ResponsesAgent
    yield ResponsesAgentStreamEvent(
        type="response.output_item.done",
        item=ResponsesAgent.create_text_output_item(text=r["text"], id="msg_1"),
    )

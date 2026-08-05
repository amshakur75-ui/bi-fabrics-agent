"""Databricks App agent handler — hosts the read-only Fabric/Power BI audit as a Responses Agent.

Drop-in for agent_server/agent.py in the agent-openai-agents-sdk template. Owns three things
and nothing else: (1) request/streaming transport, (2) the async tool loop against MCP-sourced
tools, (3) the Claude client adapter. The system prompt itself lives in the sibling
system_prompt.py — canonical single home per ADR-001. MCP tools are sourced via
DatabricksMCPClient over OAuth (no direct HTTP JSON-RPC). Claude endpoint bridged via the
§B1-alt adapter (OpenAI chat-completions → Anthropic shape).
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    create_text_output_item,
)

# Per-request rebuilds (WorkspaceClient + MCP client + a tools/list round-trip) added seconds of
# latency to EVERY message for state that only changes on an MCP redeploy. Cached with a TTL.
# NOTE: this is valid precisely BECAUSE the app runs as its service principal today — when OBO
# lands, ws/tools must be keyed per-user (or the cache dropped for user-scoped state).
_STATE = {"ws": None, "tools": None, "dispatch": None, "tools_at": 0.0}
_TOOLS_TTL_SEC = 300.0


def get_user_workspace_client() -> WorkspaceClient:
    # OBO pending admin action; SP auth is the correct fallback.
    if _STATE["ws"] is None:
        _STATE["ws"] = WorkspaceClient()
    return _STATE["ws"]

_MODEL = os.environ.get("DATABRICKS_CLAUDE_ENDPOINT", "databricks-claude-opus-4-7")
_MCP_URL = os.environ.get("FABRIC_MCP_URL", "")


# ---------------------------------------------------------------------------
# System prompt + untrusted-telemetry spotlighting.
#
# The prompt string itself lives in agent_server/system_prompt.py — the canonical single home
# per ADR-001 (2026-07-29). Before that ADR the prompt existed twice: once here (inlined) and
# once in the MCP package's fabric_audit_agent/agent/system_prompt.py. The two copies drifted
# and every SP1–SP7 fix landed only in the package, invisible to the deployed app for weeks
# (tracked as gap C2-REOPENED). Now there is exactly one copy, and both the async loop below
# and the offline eval harness read the same string.
# ---------------------------------------------------------------------------

from .system_prompt import build_system_prompt, wrap_untrusted as _wrap_untrusted
from .chart_stream import append_chart_fences




# ---------------------------------------------------------------------------
# Tool loop -- async twin of agent_server/loop.py's sync run_tool_loop.
#
# The two must stay structurally in sync: dedup of identical read-only calls, budget-exhaustion
# nudge injected before the forced-answer step, and wrap_untrusted on every tool result. The
# async version below carries the extra runtime concerns (asyncio.to_thread on the sync
# Claude POST + on_tool progress callback for streaming); the sync twin is used only by the
# offline eval harness in eval_score.py, which drives a ScriptedClient.
# ---------------------------------------------------------------------------

def _blocks_to_dicts(content):
    out = []
    for b in content:
        t = getattr(b, "type", None)
        if t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


async def _run_tool_loop(client, *, model, system, messages, tools, dispatch, max_steps=6,
                         on_tool=None):
    """``on_tool(name, input)`` (async, optional) fires before each tool executes — the streaming
    handler uses it to emit progress events so long investigations aren't silent for minutes."""
    messages = list(messages)
    trajectory, cache, tool_results = [], {}, []
    for step in range(max_steps):
        use_tools = tools if step < max_steps - 1 else []
        if not use_tools and tools and step == max_steps - 1 and trajectory:
            # Withholding tools alone doesn't tell the model WHY -- observed live, it narrated
            # its next intended tool call ("Let me pull...") instead of answering. Say it plainly.
            messages.append({"role": "user", "content": (
                "[SYSTEM] Tool budget exhausted -- no more tool calls are possible. Give your "
                "complete final answer NOW from the evidence already gathered. Do not propose, "
                "describe, or promise further tool calls.")})
        # The Claude call is sync (blocking requests.post) — run it OFF the event loop, or one
        # user's multi-second model call stalls every concurrent request in the app.
        resp = await asyncio.to_thread(client.messages.create, model=model, max_tokens=4096,
                                       system=system, messages=messages, tools=use_tools)
        if getattr(resp, "stop_reason", None) != "tool_use":
            text = "".join(getattr(b, "text", "") for b in resp.content
                           if getattr(b, "type", None) == "text")
            return {"text": text, "trajectory": trajectory, "toolResults": tool_results,
                    "stoppedReason": "answer"}

        messages.append({"role": "assistant", "content": _blocks_to_dicts(resp.content)})
        results = []
        for b in resp.content:
            if getattr(b, "type", None) != "tool_use":
                continue
            if on_tool is not None:
                await on_tool(b.name, b.input)
            key = (b.name, json.dumps(b.input, sort_keys=True, ensure_ascii=False))
            if key in cache:
                result = {"note": "duplicate read-only tool call skipped", "cached": cache[key]}
            else:
                handler = dispatch.get(b.name)
                result = await handler(b.input) if handler else {"error": f"unknown tool {b.name}"}
                cache[key] = result
                tool_results.append({"tool": b.name, "callId": b.id, "input": b.input,
                                     "result": result})
            trajectory.append({"tool": b.name, "input": b.input})
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": _wrap_untrusted(json.dumps(result, ensure_ascii=False))})
        messages.append({"role": "user", "content": results})

    return {"text": "Investigation stopped at the step budget without a conclusion.",
            "trajectory": trajectory, "toolResults": tool_results, "stoppedReason": "budget"}


# ---------------------------------------------------------------------------
# §B1-alt: OpenAI chat-completions endpoint → Anthropic Messages shape
# ---------------------------------------------------------------------------

def _build_claude_client(ws):
    import json as _json, requests as _req, time as _time

    endpoint_url = f"{ws.config.host}/serving-endpoints/{_MODEL}/invocations"
    # ws.config.token is a PAT-only field; for the app's SP (M2M OAuth) it's empty,
    # which silently sent "Authorization: Bearer None" and 401'd every call.
    # ws.config.authenticate() returns valid headers for whatever auth strategy is
    # active (PAT or OAuth) — the same mechanism the SDK's own HTTP client relies on.

    def _post(body, headers):
        """POST with a hard timeout (a hung endpoint call must not outlive the request) and BOUNDED
        retry on transient failures (connection reset / 429 / 5xx), with exponential backoff and
        honoring ``Retry-After`` on a 429. Backoff is capped (≤6s/attempt) so the up-to-6 calls the
        loop makes stay inside the Apps proxy's 120s ceiling — a sustained 429 (endpoint over its
        rate limit) still surfaces after the retries rather than hanging."""
        delays = (1.5, 3.0, 6.0)  # up to 3 retries (4 attempts)
        for i in range(len(delays) + 1):
            try:
                r = _req.post(endpoint_url, json=body, headers=headers, timeout=(10, 90))
            except _req.exceptions.ConnectionError:
                if i < len(delays):
                    _time.sleep(delays[i])
                    continue
                raise
            if r.status_code in (429, 500, 502, 503, 504) and i < len(delays):
                wait = delays[i]
                if r.status_code == 429:
                    ra = r.headers.get("Retry-After")
                    if ra:
                        try:
                            wait = min(float(ra), 6.0)  # cap so retries don't blow the request budget
                        except (TypeError, ValueError):
                            pass
                _time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    class _Block:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _Resp:
        def __init__(self, content, stop_reason):
            self.content = content
            self.stop_reason = stop_reason

    class _Messages:
        def create(self, model=None, max_tokens=4096, system=None, messages=None, tools=None):
            oai_msgs = []
            if system:
                sys_text = system if isinstance(system, str) else " ".join(
                    b.get("text", "") for b in (system or []) if isinstance(b, dict))
                oai_msgs.append({"role": "system", "content": sys_text})
            for m in (messages or []):
                role, content = m.get("role"), m.get("content")
                if role == "user" and isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            tc = b.get("content", "")
                            if isinstance(tc, list):
                                tc = " ".join(c.get("text","") if isinstance(c,dict) else str(c) for c in tc)
                            oai_msgs.append({"role": "tool", "tool_call_id": b.get("tool_use_id",""), "content": tc})
                    texts = [b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text"]
                    if texts:
                        oai_msgs.append({"role": "user", "content": " ".join(texts)})
                elif role == "assistant" and isinstance(content, list):
                    texts, tcs = [], []
                    for b in content:
                        if isinstance(b, dict):
                            if b.get("type") == "text": texts.append(b.get("text",""))
                            elif b.get("type") == "tool_use":
                                tcs.append({"id": b.get("id",""), "type": "function",
                                            "function": {"name": b.get("name",""),
                                                         "arguments": _json.dumps(b.get("input",{}))}})
                    d = {"role": "assistant",
                         "content": " ".join(t if isinstance(t, str) else str(t) for t in texts)
                         if texts else None}
                    if tcs: d["tool_calls"] = tcs
                    oai_msgs.append(d)
                else:
                    oai_msgs.append({"role": role, "content": content if isinstance(content, str) else str(content or "")})
            body = {"messages": oai_msgs, "max_tokens": max_tokens}
            if tools:
                body["tools"] = [{"type": "function", "function": {
                    "name": t.get("name"), "description": t.get("description",""),
                    "parameters": t.get("input_schema", {"type":"object","properties":{}})
                }} for t in tools]
            headers = {**ws.config.authenticate(), "Content-Type": "application/json"}
            r = _post(body, headers)
            data = r.json()
            choice = data["choices"][0]
            msg = choice["message"]
            blocks = []
            _content = msg.get("content")
            if isinstance(_content, list):
                # Some serving endpoints (e.g. Claude Sonnet 5) return `content` as structured
                # parts [{type,text},...] rather than a plain string. Flatten to text so the
                # assistant message stays a str — otherwise the next turn's join hits a list.
                _content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in _content
                ).strip()
            if _content:
                blocks.append(_Block(type="text", text=_content))
            for tc in (msg.get("tool_calls") or []):
                inp = tc["function"].get("arguments", "{}")
                if isinstance(inp, str):
                    try: inp = _json.loads(inp)
                    except: inp = {}
                blocks.append(_Block(type="tool_use", id=tc.get("id",""),
                                     name=tc["function"]["name"], input=inp))
            stop_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
            return _Resp(blocks, stop_map.get(choice.get("finish_reason","stop"), "end_turn"))

    class _Client:
        messages = _Messages()

    return _Client()


# ---------------------------------------------------------------------------
# MCP tool sourcing — app-to-app call via the databricks-mcp SDK client.
# DatabricksMCPClient wraps DatabricksOAuthClientProvider, which negotiates the
# OAuth handshake another Databricks App's URL requires; a plain SP bearer
# token (ws.config.token) is NOT sufficient and gets a 401 from the target app.
# Uses the async variants (alist_tools/acall_tool): the sync list_tools/call_tool
# call asyncio.run() internally, which fails since our handlers already run
# inside uvicorn's event loop ("asyncio.run() cannot be called from a running
# event loop").
# ---------------------------------------------------------------------------

async def _mcp_tools_and_dispatch(ws):
    # Tool definitions only change on an MCP redeploy — serve from the TTL cache instead of
    # paying an MCP client build + tools/list round-trip on every message.
    now = time.monotonic()
    if _STATE["tools"] is not None and now - _STATE["tools_at"] < _TOOLS_TTL_SEC:
        return _STATE["tools"], _STATE["dispatch"]

    try:
        from mcp.client import streamable_http as _sh
        if not hasattr(_sh, "streamablehttp_client") and hasattr(_sh, "streamable_http_client"):
            _sh.streamablehttp_client = _sh.streamable_http_client
    except Exception:
        pass
    from databricks_mcp import DatabricksMCPClient
    mcp = DatabricksMCPClient(server_url=_MCP_URL, workspace_client=ws)
    listed = await mcp.alist_tools()
    tools = [{"name": t.name, "description": t.description or "",
               "input_schema": t.inputSchema or {}} for t in listed]

    async def _call(name, inp):
        result = await mcp.acall_tool(name, inp or {})
        for c in (result.content or []):
            text = getattr(c, "text", None)
            if text is not None:
                try:
                    return json.loads(text)
                except Exception:
                    return text
        return {}

    dispatch = {t["name"]: (lambda n: lambda inp: _call(n, inp))(t["name"]) for t in tools}
    _STATE.update(tools=tools, dispatch=dispatch, tools_at=now)
    return tools, dispatch


def _messages_from_request(request):
    # mlflow's Message.content is `str | list[ResponseInputTextParam | dict]` -- real
    # Responses-API clients (the chat UI) send content blocks that mlflow parses into
    # ResponseInputTextParam *objects*, not dicts. Filtering on isinstance(c, dict) alone
    # silently dropped every block, sending Claude an empty message (400 Bad Request).
    msgs = []
    for item in getattr(request, "input", None) or []:
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else "")
        if isinstance(content, list):
            texts = []
            for c in content:
                text = c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
                if text:
                    texts.append(text)
            content = " ".join(texts)
        if role:
            msgs.append({"role": role, "content": content})
    return msgs or [{"role": "user", "content": ""}]


# ---------------------------------------------------------------------------
# Conversation-logging seam (Phase 5.4a, Task 1) — observability-only.
#
# Emits one `[conversation]` audit line per `_run` turn capturing the mineable eval signals
# (question, tools called, abstain hint, answer length) for a LATER logs -> candidate-eval-case
# miner (not built yet). Self-contained: the agent app does not import fabric_audit_agent, so the
# scrub below is inlined -- and deliberately MORE aggressive than query/redact.py because it runs
# over a free-text user question rather than KQL/URLs (redact.py's tight allowlist exists so a
# blanket mask doesn't corrupt a KQL predicate like `where Status=200`; that constraint doesn't
# apply here).
# ---------------------------------------------------------------------------

# scheme://user:pass@host -- mask both the user and the password, keep the host.
_URL_CREDENTIALS_RE = re.compile(r'(://)[^/\s:@]+:[^/\s@]+@')

# "bearer <token>" (case-insensitive) -- mask the token, preserve the word "bearer".
_BEARER_TOKEN_RE = re.compile(r'(?i)(bearer)\s+\S+')

# key=value where key is a known secret-like name (case-insensitive) -- mask the VALUE only.
# Restricted to an allowlist so a benign "key=value" (e.g. `foo=bar`, a KQL predicate) is untouched.
_SECRET_KV_RE = re.compile(
    r'(?i)\b(password|pwd|secret|token|apikey|api_key|key|client_secret|sig|access_token)=[^&\s]+'
)

# Same allowlisted names but COLON-delimited (JSON / header form, e.g. a pasted app-registration
# block `"client_secret": "abc"` or `x-api-key: <val>`) -- the `=`-only rule above misses these.
# Bare "key" is deliberately EXCLUDED here (a plain `key: value` in prose/JSON is common and benign;
# masking it would needlessly corrupt the mined question) -- only the specific secret names.
_SECRET_KV_COLON_RE = re.compile(
    r'(?i)(["\']?(?:password|pwd|secret|token|api[-_]?key|client[-_]?secret|sig|access[-_]?token)["\']?\s*:\s*)'
    r'["\']?[^"\',}\s]+["\']?'
)

# JWT shape (three base64url segments) -- catches a bare token pasted into free text, which the
# key=value allowlist above would miss entirely.
_JWT_RE = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b')

# Connection-string secrets (Azure/Fabric style, e.g. `AccountKey=...`/`SharedAccessKey=...`):
# `\bkey=` above only matches the whole-word key name "key", so it MISSES "AccountKey=" --
# the highest-realism leak when a user pastes a connection string.
_CONN_STRING_RE = re.compile(r'(?i)\b(accountkey|sharedaccesskey|password)\s*=[^;&\s]+')

_QUESTION_MAX_CHARS = 500

# Coarse abstain-phrasing heuristic on the ANSWER text -- an author-guessed approximation of the
# system prompt's ABSTAIN wording (the model answers in free prose, not the prompt's literal
# vocabulary), so this is a HINT for a future human/miner to refine into `expectAbstain`, never a
# verdict.
_ABSTAIN_HINT_RE = re.compile(
    r"(?i)\b(insufficient|cannot|can'?t|couldn'?t|don'?t have|not able|unable|no data)\b"
)


def _scrub_secrets(text):
    """Mask credentials in *text* before it is logged. Never raises -- non-``str`` input is
    coerced via ``str(text)`` first. Returns the (possibly unchanged) string."""
    out = str(text)
    out = _URL_CREDENTIALS_RE.sub(r'\1***:***@', out)
    out = _BEARER_TOKEN_RE.sub(r'\1 ***', out)
    out = _CONN_STRING_RE.sub(r'\1=***', out)
    out = _SECRET_KV_RE.sub(r'\1=***', out)
    out = _SECRET_KV_COLON_RE.sub(r'\1***', out)
    out = _JWT_RE.sub('***', out)
    return out


def _conversation_audit_log(question, trajectory, text, duration_ms=None, errored=False):
    """Print one `[conversation]` audit line for the eval-flywheel miner (built later). NEVER
    include tool `input`/args (a trajectory entry is `{"tool","input"}` -- only the name is
    extracted) or the full answer (only its length) -- see the anti-exfil discipline in the
    Phase-5.4a spec/plan."""
    scrubbed_question = _scrub_secrets(question)[:_QUESTION_MAX_CHARS]
    tools_called = [t["tool"] for t in trajectory]
    payload = {
        "tag": "conversation",
        "ts": datetime.now(timezone.utc).isoformat(),
        "question": scrubbed_question,
        "toolsCalled": tools_called,
        "toolCount": len(tools_called),
        "abstainedHint": bool(_ABSTAIN_HINT_RE.search(text or "")),
        "answerChars": len(text or ""),
        "durationMs": duration_ms,
        "errored": errored,
    }
    print(f"[conversation] {json.dumps(payload, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# Responses Agent handlers
# ---------------------------------------------------------------------------

# Investigation harness (B2): a PRE-CALL deterministic classifier sets the step budget — an
# investigation ("why/what caused/who is driving/has this happened") earns a deeper loop than a
# status lookup. Deliberately keyword-based: the budget must exist before the first model call.
_INVESTIGATION_HINTS = (
    "investigate", "why ", "why?", "root cause", "what caused", "what happened", "diagnose",
    "spike", "recurring", "what's causing", "what is causing", "has this happened", "happened before", "who is driving", "who's driving",
    "dig into", "deep dive", "walk me through", "find out what",
)
_LOOKUP_BUDGET = 6
_INVESTIGATION_BUDGET = 12


def _step_budget(question):
    q = f" {str(question or '').lower()} "
    return _INVESTIGATION_BUDGET if any(h in q for h in _INVESTIGATION_HINTS) else _LOOKUP_BUDGET


def _plain_trail(trajectory):
    """The investigation trail in PLAIN LANGUAGE (progress phrases; never tool names/inputs)."""
    out = []
    for step in trajectory or []:
        try:
            out.append(_progress_text(step.get("tool"), step.get("input")))
        except Exception:
            continue
    return out


async def _run(request, on_tool=None):
    ws = get_user_workspace_client()
    tools, dispatch = await _mcp_tools_and_dispatch(ws)
    # Feature 2: add the direct read-only Fabric REST tools ALONGSIDE the MCP tools, so the model can
    # choose the best path per task. Inert (adds nothing) unless the SP creds are configured, so the
    # MCP path is unaffected when direct access isn't set up.
    from .fabric_direct import direct_tools_and_dispatch
    direct_tools, direct_dispatch = direct_tools_and_dispatch(os.environ)
    if direct_tools:
        tools = tools + direct_tools
        dispatch = {**dispatch, **direct_dispatch}
    # render_chart as a DIRECT tool so the agent can actually draw charts (it wasn't in the MCP
    # toolset, so the model improvised chart JSON as text). Skip if MCP already provides it, so we
    # never send Claude two tools with the same name.
    if "render_chart" not in dispatch:
        from .chart_tool import chart_tool_and_dispatch
        chart_tools, chart_dispatch = chart_tool_and_dispatch()
        tools = tools + chart_tools
        dispatch = {**dispatch, **chart_dispatch}
    messages = _messages_from_request(request)
    question = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    budget = _step_budget(question)
    t0 = time.monotonic()
    errored = False
    try:
        result = await _run_tool_loop(
            _build_claude_client(ws), model=_MODEL, system=build_system_prompt(),
            messages=messages, tools=tools, dispatch=dispatch,
            max_steps=budget, on_tool=on_tool)
    except Exception:
        errored = True
        raise
    finally:
        duration_ms = round((time.monotonic() - t0) * 1000)
    # N22 fix: the step budget is a silent, keyword-based classifier (see _INVESTIGATION_HINTS) --
    # a two-part question phrased just differently enough to miss every keyword gets only 6 steps
    # with zero indication anything was capped. Disclose ONLY when the shallow budget was actually
    # exhausted (stoppedReason == "budget") -- a lookup that concludes cleanly within 6 steps has
    # nothing hidden and doesn't need a disclosure tacked on (that would just be noise against the
    # "lean, not a data dump" rule). The exhausted-budget case is the one that actually matters:
    # something was cut short by a limit the user never knew existed.
    if budget == _LOOKUP_BUDGET and result.get("stoppedReason") == "budget":
        result["text"] = (result.get("text") or "") + (
            "\n\n_That ran under a quick-lookup budget and hit its step limit before fully "
            "concluding. Ask me to \"investigate\" or \"dig deeper\" for a fuller multi-step pass._"
        )
    # Plain-language investigation trail (no tool names/inputs) — surfaced via custom_outputs.
    result["trail"] = _plain_trail(result.get("trajectory"))
    try:
        _conversation_audit_log(question, result.get("trajectory") or [], result.get("text") or "",
                                duration_ms=duration_ms, errored=errored)
    except Exception as exc:
        # Failure-isolated: the emit must never break a conversation, and the except must never
        # log the raw question or `str(exc)` (which could echo the offending input back into the
        # log) -- at most the exception TYPE name.
        print(f"[conversation] log failed: {type(exc).__name__}")
    return result


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    r = await _run(request)
    # Append render_chart specs as ```fabric-chart fences so the chat UI renders the <Chart> (parity
    # with the streaming path).
    text_item = create_text_output_item(
        text=append_chart_fences(r["text"], r.get("toolResults")), id="msg_1")
    return ResponsesAgentResponse(
        output=[text_item],
        custom_outputs={"trajectory": r["trajectory"], "toolResults": r.get("toolResults"),
                        "stoppedReason": r["stoppedReason"], "trail": r.get("trail")},
    )


# Plain-phrase progress map (Phase 5.1, Task 2). Presentation-only: keys are the 18 tool names
# from tools.py::create_tool_definitions; values are the user-finalized plain-English wording.
# Never surface a raw tool name or JSON to the user -- see `_progress_text` below.
_PROGRESS_PHRASES = {
    "run_audit": "running the capacity audit",
    "list_workspaces": "listing the workspaces",
    "user_activity": "looking into that user's activity",
    "investigate_user": "looking into that user's activity",
    "user_timeline": "looking into that user's activity",
    "user_spike_history": "looking into that user's activity",
    "investigate_capacity_spike": "checking events with unusual spikes",
    "spike_events": "checking events with unusual spikes",
    "capacity_peaks": "finding the biggest usage moments",
    "capacity_overloads": "finding when the capacity went over",
    "raw_events": "pulling the raw event stream",
    "capacity_patterns": "analyzing capacity patterns",
    "capacity_diagnostics": "analyzing capacity patterns",
    "describe_source": "checking what the data source contains",
    "sample_events": "checking what the data source contains",
    "diagnose": "working through the diagnosis",
    "analyze_dax": "reviewing the DAX",
    "whats_changed": "comparing against the last run",
    "run_kql": "running a read-only query",
    "query_library": "checking the query library",
    # Direct Fabric REST tools (Feature 2) — same no-tool-name-leak wording.
    "fabric_list_workspaces": "listing the workspaces",
    "fabric_list_items": "checking what's in that workspace",
    "fabric_list_capacities": "listing the capacities",
    "fabric_dataset_refresh_history": "checking the refresh history",
    "fabric_refresh_schedule": "checking the refresh schedule",
    "fabric_list_datasets": "looking up datasets in that workspace",
}
_PROGRESS_DEFAULT = "working on it…"

# Scope-hint whitelist, in a fixed evaluation order (deterministic when multiple keys are
# present). Any key not listed here is ignored -- never rendered.
_SCOPE_HINT_FORMATS = (
    ("user", " for {}"),
    ("item", " for {}"),
    ("topN", " (top {})"),
    ("days", " (last {}d)"),
)
_SCOPE_HINT_MAX_LEN = 60


def _scope_hint(inp):
    if not isinstance(inp, dict):
        return ""
    for key, fmt in _SCOPE_HINT_FORMATS:
        value = inp.get(key)
        if value is None:
            continue
        value = str(value)
        # FORMAT guard, not a PII control: braces would leak JSON-shaped text into the plain
        # progress line; newlines/over-length values would break the single-line format. An
        # empty/whitespace value is skipped too, so a hint never renders as a dangling "for ".
        if (not value.strip() or "{" in value or "}" in value or "\n" in value
                or len(value) > _SCOPE_HINT_MAX_LEN):
            continue
        return fmt.format(value)
    return ""


def _friendly_failure(exc):
    """A readable end-of-stream message for a failed turn — clean copy for a rate limit (the common
    case: the model serving endpoint is momentarily over its request limit), generic otherwise. Never
    dumps the raw endpoint URL at the user."""
    msg = str(exc)
    if "429" in msg or "Too Many Requests" in msg:
        return ("The model is momentarily rate-limited — too many requests to the serving endpoint "
                "right now. That's a temporary capacity limit, not a problem with your question, and "
                "nothing was modified. Give it a few seconds and ask again.")
    return ("The investigation failed before completing. Nothing was modified (all tools are "
            "read-only) — please retry, or rephrase the question.")


def _progress_text(name, inp):
    # The `user`/`item` hint echoes an identifier straight back to the requester by design --
    # acceptable only while the app viewer == the requester (see the OBO note on
    # get_user_workspace_client above); TODO revisit once OBO / per-user auth lands.
    phrase = _PROGRESS_PHRASES.get(name, _PROGRESS_DEFAULT)
    return f"🔎 {phrase}{_scope_hint(inp)}"


def _progress_line(name, inp):
    # Streamed progress line: an animated-feel "…" working indicator, plus a trailing paragraph break
    # so each check STACKS on its own line. The chat UI merges consecutive text items into one block,
    # so without the break the checks run together on a single line ("🔎 A 🔎 B"); the "\n\n" makes
    # them drop underneath one another as they arrive, like a normal running list.
    return f"{_progress_text(name, inp)} …\n\n"


@stream()
async def stream_handler(request: ResponsesAgentRequest):
    """Emit a progress event per tool call, then the final answer. A multi-step investigation
    was previously silent until the very end — pushing progress keeps the user informed AND
    keeps bytes flowing through the Apps proxy during long runs."""
    queue: asyncio.Queue = asyncio.Queue()

    async def on_tool(name, inp):
        await queue.put((name, inp))

    task = asyncio.create_task(_run(request, on_tool=on_tool))
    idx = 0
    while True:
        getter = asyncio.create_task(queue.get())
        done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            name, inp = getter.result()
            idx += 1
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=create_text_output_item(text=_progress_line(name, inp), id=f"progress_{idx}"),
            )
            continue
        getter.cancel()
        while not queue.empty():   # drain progress that landed with the final result
            name, inp = queue.get_nowait()
            idx += 1
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=create_text_output_item(text=_progress_line(name, inp), id=f"progress_{idx}"),
            )
        try:
            r = task.result()
            # Append any render_chart specs as ```fabric-chart fences so the chat UI renders the
            # real <Chart> (the tool loop consumes results internally, so without this the chart
            # spec never reaches the client and the answer falls back to an ASCII table).
            final_text = append_chart_fences(r["text"], r.get("toolResults"))
        except Exception as exc:
            # A raised exception here would abort the SSE stream mid-flight and the chat UI
            # shows a broken/blank response. End the stream with an honest, readable failure
            # instead (the non-streaming /invocations path still surfaces a proper 500).
            import traceback as _tb
            print(f"[stream] turn failed: {type(exc).__name__}: {exc}\n{_tb.format_exc()}",
                  flush=True)
            final_text = _friendly_failure(exc)
        yield ResponsesAgentStreamEvent(
            type="response.output_item.done",
            item=create_text_output_item(text=final_text, id="msg_1"),
        )
        break
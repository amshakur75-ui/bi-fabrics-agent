# 18 — MCP Protocol Design + Anthropic Claude API

**Research focus:** The **MCP protocol spec** (server primitives, tool annotations, transports, lifecycle, OAuth authorization, errors, pagination, security) and the **Anthropic Claude API** (Messages API tool use, structured outputs, prompt caching, streaming, system prompts, token counting/cost, extended/adaptive thinking, model selection) — and, for each, how it refines the **bi-fabrics-audit-agent**: a READ-ONLY Fabric/Power BI audit agent that **IS** a custom MCP server (exposes a read-only `run_audit` tool over **Streamable HTTP**, hosted in a Databricks App) and uses **Claude** (Anthropic SDK) as its reasoner.

**Out of scope (already researched elsewhere — NOT re-covered here):** hosting a custom MCP on a Databricks App (`mcp-` name prefix, `/mcp` endpoint, streamable-http), Databricks managed MCP servers, Mosaic AI Agent Framework, serving Claude on Databricks (endpoint names, `get_open_ai_client`, the messages→chat.completions shim).

**Spec revision anchor:** MCP **2025-06-18** (current "latest"). Version timeline that matters: **2024-11-05** (first dated revision; HTTP+SSE transport) → **2025-03-26** (Streamable HTTP introduced; tool annotations added) → **2025-06-18** (structured tool output, OAuth Resource Server classification, RFC 8707 mandate, `MCP-Protocol-Version` header required, JSON-RPC batching removed). Note: the Anthropic MCP-connector docs now reference the newer **2025-11-25** authorization revision — the protocol continues to evolve; pin a revision in code.

**Anthropic model anchor:** flagship reasoner is **`claude-opus-4-8`** (Opus 4.8), adaptive-thinking only, 1M context, 128K max output, $5/$25 per MTok.

---

## PART A — MCP PROTOCOL SPEC

### A1. Server primitives — Tools

- **TITLE:** Tools (server primitive, model-controlled)
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- **Summary:** Tools are model-controlled callable functions discovered via `tools/list` (paginated) and invoked via `tools/call`. The server may emit `notifications/tools/list_changed` when its tool set changes. A tool definition carries `name`, optional `title`, `description`, `inputSchema` (JSON Schema), optional `outputSchema` (JSON Schema for structured results), and optional `annotations`.
- **Exact identifiers:** methods `tools/list`, `tools/call` (`params: { "name", "arguments" }`), `notifications/tools/list_changed`; tool fields `name`, `title`, `description`, `inputSchema`, `outputSchema`, `annotations`; server capability `{"capabilities":{"tools":{"listChanged": <bool>}}}`.
- **How it helps the agent:** `run_audit` is exactly one entry in `tools/list`: `name: "run_audit"`, a thorough `description`, a tight `inputSchema` (tenant/capacity/date-range/detector params), and an `outputSchema` for the audit verdict object. Declare `tools.listChanged: false` — the tool set is static, so no need to emit `notifications/tools/list_changed`. Sibling read-only views (`top_users`, `capacity_usage`) can be **separate tools** in the same list, or sub-modes of `run_audit` selected by an `action`/`scope` input param (Anthropic's "consolidate related operations" guidance — see C1).

### A2. Server primitives — Resources

- **TITLE:** Resources (server primitive, application-driven, read-only context)
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/resources
- **Summary:** Resources are URI-addressed read-only context. Listed via `resources/list` (paginated), read via `resources/read` (`params: { "uri" }` → `contents[]` each with `uri`, `mimeType`, and `text` or base64 `blob`). Templates via `resources/templates/list` (RFC 6570 `uriTemplate`). Optional subscriptions: `resources/subscribe`/`unsubscribe` + `notifications/resources/updated` and `notifications/resources/list_changed`.
- **Exact identifiers:** methods `resources/list`, `resources/read`, `resources/templates/list`, `resources/subscribe`, `resources/unsubscribe`; notifications `notifications/resources/list_changed`, `notifications/resources/updated`; resource fields `uri`, `name`, `title`, `description`, `mimeType`, `size`, `annotations` (`audience`, `priority`, `lastModified`); capability `{"resources":{"subscribe":<bool>,"listChanged":<bool>}}`; resource-not-found error `-32002`.
- **How it helps the agent:** Optional but a strong fit. Expose **prior audit snapshots / baselines** as read-only resources (e.g. `audit://snapshots/2026-06-22`, `audit://baselines/capacity/{id}`) so an MCP client can pull historical context via `resources/read` without re-running a scan. If you don't implement resources, simply omit the `resources` capability from `initialize`.

### A3. Server primitives — Prompts

- **TITLE:** Prompts (server primitive, user-controlled templates)
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/prompts
- **Summary:** Prompts are user-controlled templates (often surfaced as slash commands). Listed via `prompts/list` (paginated), fetched via `prompts/get` (`params: { "name", "arguments" }` → `{ "description", "messages": [...] }`). Each `PromptMessage` has `role` (`user`/`assistant`) and `content` (text/image/audio/embedded resource).
- **Exact identifiers:** methods `prompts/list`, `prompts/get`; prompt fields `name`, `title`, `description`, `arguments[]` (each `name`, `description`, `required`); capability `{"prompts":{"listChanged":<bool>}}`; errors `-32602` (bad args), `-32603` (internal).
- **How it helps the agent:** Optional. Ship a `run_audit` prompt template (arguments `capacity_name`, `lookback_days`) so users invoke the audit as a guided slash command from an MCP-aware client. Not required for the tool to function.

### A4. Tool annotations — readOnlyHint / destructiveHint (CRITICAL)

- **TITLE:** ToolAnnotations — behavioral hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, `title`)
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/tools and schema `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-06-18/schema.ts` (`ToolAnnotations` interface)
- **Summary:** The optional `annotations` object describes tool behavior. Verbatim fields **and their defaults**:

  | Field | Meaning (verbatim) | Default |
  |---|---|---|
  | `title` | "A human-readable title for the tool." | — |
  | `readOnlyHint` | "If true, the tool does not modify its environment." | **`false`** |
  | `destructiveHint` | "If true, the tool may perform destructive updates… If false, the tool performs only additive updates. (Meaningful only when `readOnlyHint == false`)" | **`true`** |
  | `idempotentHint` | "If true, calling the tool repeatedly with the same arguments will have no additional effect… (Meaningful only when `readOnlyHint == false`)" | **`false`** |
  | `openWorldHint` | "If true, this tool may interact with an 'open world' of external entities…" | **`true`** |

  Spec note (verbatim, load-bearing): *"all properties in ToolAnnotations are **hints**… Clients should never make tool use decisions based on ToolAnnotations received from untrusted servers."* Interaction rule: when `readOnlyHint: true`, both `destructiveHint` and `idempotentHint` are meaningless/ignored.
- **Exact identifiers:** `annotations.title`, `annotations.readOnlyHint`, `annotations.destructiveHint`, `annotations.idempotentHint`, `annotations.openWorldHint`.
- **How it helps the agent (this is the single most important MCP fact for this server):** Because `readOnlyHint` **defaults to `false`**, you MUST set it explicitly, or clients assume `run_audit` can write. Set:
  ```json
  "annotations": {
    "title": "Read-only Fabric/Power BI capacity audit",
    "readOnlyHint": true,
    "destructiveHint": false,
    "openWorldHint": true
  }
  ```
  `readOnlyHint: true` tells clients the audit never mutates the tenant (skip destructive-action confirmation). `openWorldHint: true` is correct because the audit calls out to Fabric/PBI REST APIs. **Critical caveat:** annotations are *untrusted hints* — a client-side UX/safety convenience, not enforcement. Your read-only guarantee must be enforced in the implementation (only GET/read REST calls, no write scopes), never relied upon via the annotation alone.

### A5. Structured tool output — structuredContent + outputSchema

- **TITLE:** Tool results — content blocks, `structuredContent`, `outputSchema`, `isError`
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- **Summary:** Tool results carry unstructured `content[]` and/or structured `structuredContent`. Content block `type` discriminators: `text`, `image` (base64 `data` + `mimeType`), `audio`, `resource_link` (NEW in 2025-06-18: `uri`, `name`, `description`, `mimeType`, `annotations` — not guaranteed in `resources/list`), and embedded `resource` (`{ "type":"resource", "resource": { "uri","mimeType","text"|"blob" } }`). If `outputSchema` is declared, the server **MUST** return `structuredContent` conforming to it and clients **SHOULD** validate; for backward compat the server **SHOULD** also mirror the JSON as a `text` block. `isError: true` flags tool-execution failures.
- **Exact identifiers:** `content[]`, content types `text`/`image`/`audio`/`resource_link`/`resource`, `structuredContent`, `outputSchema`, `isError`; block annotations `audience`/`priority`/`lastModified`.
- **How it helps the agent:** Define an `outputSchema` for the audit verdict (e.g. `{ capacity_id, cu_utilization_pct, throttling_events, unused_artifacts[], security_findings[], top_users[], verdict }`) and return it in `structuredContent` so the Databricks Data Agent / Mosaic reasoner parses and validates reliably. Mirror the same JSON in a `text` block for older clients. Use `resource_link` blocks to point at a full report artifact rather than inlining megabytes. Reserve `isError: true` for audit *execution* failures (Fabric REST 5xx) — "audit found problems" is normal successful output, **not** an error.

### A6. Transports — Streamable HTTP vs stdio (deprecated HTTP+SSE)

- **TITLE:** Transports — Streamable HTTP (single `/mcp` endpoint), stdio, deprecated HTTP+SSE
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- **Summary:** Two standard transports; all messages JSON-RPC 2.0 over UTF-8.
  - **Streamable HTTP** (introduced 2025-03-26; *"replaces the HTTP+SSE transport from protocol version 2024-11-05"*): a **single MCP endpoint** path serving **both POST and GET**. POST (client→server) **MUST** send `Accept: application/json, text/event-stream`; the server replies either `Content-Type: application/json` (one object) or `text/event-stream` (SSE that eventually contains the JSON-RPC response). A notification/response input → **HTTP 202** no body. GET opens a server→client SSE stream (or **405** if unsupported). Sessions: the server **MAY** assign `Mcp-Session-Id` on the `InitializeResult`; the client **MUST** echo it thereafter (missing → **400**, terminated/expired → **404** then re-initialize; **DELETE** ends a session). Resumability: SSE events get an `id`; client resumes with `Last-Event-ID`, server replays only post-disconnect messages. The client **MUST** send `MCP-Protocol-Version: <negotiated>` on all post-init requests (server defaults to `2025-03-26` if absent; invalid → **400**). Security: servers **MUST** validate `Origin` (DNS-rebinding), **SHOULD** bind localhost-only when local, **SHOULD** authenticate all connections.
  - **stdio:** subprocess, newline-delimited JSON-RPC over stdin/stdout, stderr for logs. No OAuth (env credentials).
- **Exact identifiers:** headers `Accept`, `Content-Type: text/event-stream`, `Mcp-Session-Id`, `Last-Event-ID`, `MCP-Protocol-Version`, `Origin`; status codes 202/400/404/405; SSE event `id`.
- **How it helps the agent:** The Databricks-App-hosted server is exactly the Streamable HTTP case. Expose one `/mcp` endpoint handling POST+GET; validate `Origin`. `run_audit` is slow, so prefer the **SSE response mode** for `tools/call` and emit progress while scanning; implement SSE event `id`s + `Last-Event-ID` replay so a dropped connection mid-audit resumes rather than re-scans. If you need stateful sessions, mint a CSPRNG `Mcp-Session-Id` at `initialize`. Echo/honor `MCP-Protocol-Version` (expect `2025-06-18`).

### A7. Lifecycle — initialize / capabilities / version negotiation

- **TITLE:** Lifecycle — `initialize`, `notifications/initialized`, capability + version negotiation
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
- **Summary:** Three phases: Initialization → Operation → Shutdown. Client sends `initialize` first (`protocolVersion`, `capabilities`, `clientInfo`); server responds with `protocolVersion`, its `capabilities`, `serverInfo`, optional `instructions`; client then sends `notifications/initialized`. Version negotiation: client sends its latest; server echoes if supported else returns its latest (client disconnects if incompatible). Server capabilities: `prompts`, `resources`, `tools`, `logging`, `completions`, `experimental`; sub-capabilities `listChanged` (prompts/resources/tools) and `subscribe` (resources). Per-request timeouts **SHOULD** apply, with a maximum even when progress notifications reset the clock.
- **Exact identifiers:** `initialize` (`params: protocolVersion`, `capabilities`, `clientInfo:{name,title,version}`), result (`protocolVersion`, `capabilities`, `serverInfo:{name,title,version}`, `instructions`), `notifications/initialized`; mismatch error `-32602` "Unsupported protocol version" with `data.supported`/`data.requested`.
- **How it helps the agent:** In the `initialize` response advertise only what you implement — minimally `{"capabilities":{"tools":{"listChanged":false}}}` (add `logging`/`completions`/`resources` only if built). Echo `protocolVersion: "2025-06-18"`; set `serverInfo` to identify the audit server. A long capacity scan should emit progress (resetting client timeouts) but still cap total runtime.

### A8. Authorization — OAuth 2.1 framework (Resource Server)

- **TITLE:** MCP Authorization — OAuth 2.1, Protected Resource Metadata, audience binding, no passthrough
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- **Summary:** Authorization is OPTIONAL; when used over HTTP transports the MCP server is an **OAuth 2.1 Resource Server**, the client is the OAuth client, and a separate (or co-hosted) Authorization Server issues tokens. **stdio SHOULD NOT** use this (env credentials). Standards: OAuth 2.1 (AS **MUST**), **RFC 9728** Protected Resource Metadata (server **MUST** serve; clients **MUST** use for AS discovery; must list `authorization_servers`), **RFC 8414** AS Metadata (**MUST**), **RFC 7591** Dynamic Client Registration (**SHOULD**), **RFC 8707** Resource Indicators (clients **MUST**). Discovery: unauthenticated request → **401** + `WWW-Authenticate` header → client GETs `/.well-known/oauth-protected-resource` → GETs `/.well-known/oauth-authorization-server` → OAuth 2.1 + PKCE (+ optional `POST /register`). The `resource` param (RFC 8707) **MUST** appear in authorization + token requests, identifying the server's **canonical URI**. Tokens: `Authorization: Bearer <token>` on every request (never in query string); the server **MUST** validate token **audience** is itself; **MUST NOT** accept tokens not issued for it; **MUST NOT** pass the client's token through to upstream APIs. Errors: 401 (auth/invalid token), 403 (insufficient scope), 400 (malformed).
- **Exact identifiers:** `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`, `WWW-Authenticate`, `Authorization: Bearer`, `authorization_servers`, `resource` (RFC 8707); status 401/403/400.
- **How it helps the agent:** The Databricks App over Streamable HTTP is squarely "HTTP transport," so this applies. Model the audit server as an OAuth Resource Server: serve `/.well-known/oauth-protected-resource` listing your AS (Microsoft Entra ID), return `401 + WWW-Authenticate` when unauthenticated, and **validate the audience** so only tokens minted for *your* `run_audit` server are accepted. When you then call Fabric/PBI REST, acquire a **separate** Entra token (service principal / OBO) — **do not forward the client's token** (token-passthrough is forbidden — see A11). If the Databricks App fronts auth via Databricks/Entra SSO instead, you can lean on that platform layer, but audience-binding + no-passthrough still hold.

### A9. Error handling — JSON-RPC errors vs tool `isError`

- **TITLE:** Error handling — JSON-RPC error codes vs tool-execution `isError`
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/tools (+ lifecycle/resources/prompts pages)
- **Summary:** Two distinct mechanisms. (1) **Protocol errors** — standard JSON-RPC `error` objects: `-32700` parse, `-32600` invalid request, `-32601` method not found, `-32602` invalid params (used for unknown tool / invalid args / invalid cursor / unsupported protocol version), `-32603` internal, `-32002` resource not found (MCP-specific). (2) **Tool-execution errors** — reported *inside a successful result* with `isError: true` and an explanatory `content` block.
- **Exact identifiers:** error codes `-32700`/`-32600`/`-32601`/`-32602`/`-32603`/`-32002`; result field `isError`.
- **How it helps the agent:** Use protocol errors only for malformed calls (bad tool name → `-32601`/`-32602`; bad audit params → `-32602`). Use `isError: true` for runtime audit failures (Fabric REST 429/5xx, expired upstream token) so the failure stays visible to the LLM reasoner rather than as a hard protocol fault. "Audit succeeded and found violations" returns normally with `isError` absent/false.

### A10. Pagination — cursor-based

- **TITLE:** Pagination — opaque cursor (`cursor` / `nextCursor`)
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/server/utilities/pagination
- **Summary:** Opaque cursor-based pagination (not numbered pages); the server chooses page size. Request carries `params.cursor`; response carries optional `nextCursor`; a **missing `nextCursor` means end of results**. Cursors are opaque — clients **MUST NOT** parse/modify/persist them; invalid cursor → `-32602`. Supported on `resources/list`, `resources/templates/list`, `prompts/list`, `tools/list` — **not** on `tools/call`, `resources/read`, `prompts/get`.
- **Exact identifiers:** `params.cursor`, `nextCursor`.
- **How it helps the agent:** With a single `run_audit` tool, `tools/list` fits one page — omit `nextCursor`. Pagination matters only if you expose audit history as resources (paginate `resources/list`). Note the audit *result set itself* is returned by `tools/call`, which is **not** paginated — so chunk large output via `resource_link` blocks or your own result-paging params, not MCP pagination.

### A11. Security best practices

- **TITLE:** MCP Security Best Practices — token passthrough prohibition, confused deputy, session hijacking, SSRF, scope minimization, human-in-the-loop
- **URL:** https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices
- **Summary:** Major classes + verbatim mitigations:
  - **Token Passthrough (forbidden):** *"MCP servers MUST NOT accept any tokens that were not explicitly issued for the MCP server."* Forces audience validation + a separate upstream token.
  - **Confused Deputy:** affects MCP *proxy* servers (static upstream client ID + DCR + consent cookies). Mitigate with per-client user consent, approved-`client_id` registry, exact-match `redirect_uri`, single-use cryptographically random `state`, `__Host-` cookies (`Secure/HttpOnly/SameSite=Lax`).
  - **Session Hijacking:** servers with authorization **MUST** verify all inbound requests and **MUST NOT** use sessions for auth; **MUST** use secure non-deterministic (random UUID) session IDs; **SHOULD** bind sessions to user identity (`<user_id>:<session_id>`).
  - **SSRF (client-side):** block private/metadata IPs in discovery URLs.
  - **Scope Minimization:** least-privilege scopes; no wildcard/omnibus (`*`, `all`, `full-access`); escalate via `WWW-Authenticate` `scope=`.
  - **Human-in-the-loop / input validation / prompt injection:** there **SHOULD** be a human able to deny tool invocations; servers **MUST** validate all tool inputs, implement access controls, rate-limit, sanitize outputs.
- **Exact identifiers:** "MUST NOT accept any tokens that were not explicitly issued for the MCP server"; `__Host-` cookies; `<user_id>:<session_id>` session binding.
- **How it helps the agent:** Even though `run_audit` is read-only, it is a network-exposed HTTP MCP server, so: (1) enforce **token audience validation** and **never pass the client token through** to Fabric/PBI — use a distinct least-privilege upstream token (read-only scopes only); (2) if using `Mcp-Session-Id`, generate it with a CSPRNG, never authenticate off the session alone, re-validate the bearer on every request, bind to the user; (3) validate every `run_audit` argument (capacity IDs, date ranges) against an allowlist to prevent injection into your REST calls; (4) `readOnlyHint: true` lets clients relax confirmation, but server-side read-only enforcement is what actually guarantees safety. The read-only posture sidesteps destructive-action + confused-deputy proxy risks; the token-passthrough rule is the one to actively engineer around.

---

## PART B — PYTHON MCP SDK / FastMCP

> **Version note:** target the stable **`mcp` v1.x** line (`from mcp.server.fastmcp import FastMCP`). The repo `main` branch is a **v2.0.0-alpha** rename (`mcp.server.fastmcp` → `mcp.server.mcpserver`) that would break the imports below. All citations pinned to tag **v1.28.0**. Confirm the patch you `pip install mcp` resolves.

### B1. FastMCP basics — server, decorators, schema generation, structured output

- **TITLE:** `FastMCP` class + `@tool`/`@resource`/`@prompt` decorators; Pydantic-driven schema generation
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/server.py (+ README, `examples/fastmcp/weather_structured.py`)
- **Summary:** `FastMCP` is the ergonomic façade. `@mcp.tool()` inspects the signature: **type hints + docstring → `inputSchema`**, and the **return-type annotation → `outputSchema` + `structuredContent`** (auto-detected). Internally `Tool.from_function` builds a Pydantic arg-model and calls `arg_model.model_json_schema(by_alias=True)`. Return a Pydantic `BaseModel`/`TypedDict`/`dataclass`/`dict[str,X]` to auto-emit structured output.
- **Exact identifiers / imports:** `from mcp.server.fastmcp import FastMCP, Context`; `mcp = FastMCP("name", instructions=..., host=..., port=...)`; `@mcp.tool(name, title, description, annotations, icons, meta, structured_output)`; `@mcp.resource(uri, *, name, title, description, mime_type, annotations, meta)`; `@mcp.prompt(name, title, description)`; `Tool.from_function`; `fn_metadata.output_schema`; `structured_output` ∈ `{None (auto), True, False}`.
  ```python
  from pydantic import BaseModel, Field
  from mcp.server.fastmcp import FastMCP
  mcp = FastMCP("Fabric Audit")

  class CapacityUsage(BaseModel):
      capacity_id: str
      cu_utilization_pct: float = Field(description="Mean CU% over the window")
      throttling_events: int

  @mcp.tool()
  def run_audit(scope: str = "capacity") -> CapacityUsage:  # return type → outputSchema + structuredContent
      """Run a read-only Fabric/PBI capacity audit and return findings."""
      ...
  ```
- **How it helps the agent:** Define `run_audit` (and optionally `top_users`/`capacity_usage`) returning a Pydantic `AuditReport`; the client gets a validated `outputSchema` + `structuredContent` for free — ideal for the Databricks/Mosaic consumer that wants typed JSON. Use `@mcp.resource("audit://capacity/{capacity_id}")` for read-only snapshot resources.

### B2. Tool annotations in FastMCP — `ToolAnnotations(readOnlyHint=True, …)`

- **TITLE:** `ToolAnnotations` model + `@mcp.tool(annotations=...)`
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/types.py (class `ToolAnnotations`)
- **Summary:** Set annotations by passing a `ToolAnnotations(...)` to `annotations=` on `@mcp.tool(...)`. Fields mirror the spec (A4): `title`, `readOnlyHint` (default false), `destructiveHint` (default true, meaningful only when not read-only), `idempotentHint` (default false), `openWorldHint` (default true).
- **Exact identifiers:** `from mcp.types import ToolAnnotations`; fields `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
  ```python
  from mcp.types import ToolAnnotations

  @mcp.tool(
      title="Run Fabric/PBI Read-Only Audit",
      annotations=ToolAnnotations(
          title="Run Fabric/PBI Read-Only Audit",
          readOnlyHint=True,        # declares no environment mutation
          destructiveHint=False,
          idempotentHint=True,      # same args → same audit, no side effects
          openWorldHint=True,       # talks to external Fabric/PBI REST APIs
      ),
  )
  def run_audit(scope: str = "capacity") -> AuditReport: ...
  ```
  (`title` may appear both at `@mcp.tool(title=...)` and inside `ToolAnnotations(title=...)`; the annotation title is the display title per spec — setting both is the safe pattern.)
- **How it helps the agent:** This is the load-bearing pattern. `readOnlyHint=True` + `destructiveHint=False` is exactly the signal a governance layer / orchestrator uses to call `run_audit` without confirmation; `idempotentHint=True` makes retries safe; `openWorldHint=True` is accurate (external REST). Must be set explicitly because `readOnlyHint` defaults to `false`.

### B3. Serving over Streamable HTTP (→ Databricks App)

- **TITLE:** `transport="streamable-http"`, `streamable_http_app()`, `stateless_http`, `StreamableHTTPSessionManager`, mounting
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/server.py (+ `examples/servers/simple-streamablehttp-stateless/.../server.py`)
- **Summary:** `run(transport: Literal["stdio","sse","streamable-http"]="stdio", mount_path=None)`. `mcp.run(transport="streamable-http")` runs uvicorn via `run_streamable_http_async()` on `settings.host`/`settings.port`. `transport="sse"` is the deprecated legacy HTTP transport. Key `FastMCP.__init__` defaults: `host="127.0.0.1"`, `port=8000`, `streamable_http_path="/mcp"`, `json_response=False` (True → single JSON instead of SSE), `stateless_http=False` (True → new transport per request, no session state). A DNS-rebinding guard auto-enables on localhost binds; binding `0.0.0.0` for a real app disables it. `streamable_http_app()` returns a Starlette app mounting the handler at `/mcp` with `lifespan=lambda app: self.session_manager.run()`.
- **Exact identifiers:** `mcp.run(transport="streamable-http")`, `mcp.streamable_http_app()`, `streamable_http_path="/mcp"`, `stateless_http=True`, `json_response=True`, `StreamableHTTPSessionManager(app=, event_store=, json_response=, stateless=)`, `session_manager.run()`, `@mcp.custom_route("/health", methods=["GET"])`.
  ```python
  import contextlib
  from starlette.applications import Starlette
  from starlette.routing import Mount

  @contextlib.asynccontextmanager
  async def lifespan(app):
      async with mcp.session_manager.run():   # MUST run the session manager
          yield

  app = Starlette(routes=[Mount("/", app=mcp.streamable_http_app())], lifespan=lifespan)
  ```
- **How it helps the agent:** A Databricks App runs a long-lived web server on the platform port (`$DATABRICKS_APP_PORT`) bound to `0.0.0.0`. Two shapes: (1) simplest — `FastMCP("Fabric Audit", host="0.0.0.0", port=int(os.environ["DATABRICKS_APP_PORT"]), stateless_http=True); mcp.run(transport="streamable-http")`; (2) recommended — expose `app = mcp.streamable_http_app()` and let the App's process manager run it via its own uvicorn/gunicorn command. Use `stateless_http=True` so any replica serves any request. Add an **unauthenticated** `@mcp.custom_route("/health")` so the platform probe passes.

### B4. Context object — logging, progress, resource reads

- **TITLE:** `Context` — `ctx.info/debug/warning/error`, `ctx.report_progress`, `ctx.read_resource`, request metadata
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/server.py (class `Context`)
- **Summary:** A tool/resource/prompt param annotated `Context` is auto-injected by type (name is arbitrary). All methods are **async**. Exposes async logging, `report_progress`, `read_resource`, and request metadata.
- **Exact identifiers:** `from mcp.server.fastmcp import Context`; `await ctx.debug/info/warning/error(message, **extra)`, `await ctx.log(level, message, *, logger_name=None)`, `await ctx.report_progress(progress, total=None, message=None)`, `await ctx.read_resource(uri)`; properties `ctx.request_id`, `ctx.client_id`, `ctx.session`, `ctx.request_context` (→ `.lifespan_context`, `.meta`, `.session`).
  ```python
  @mcp.tool()
  async def run_audit(scope: str, ctx: Context) -> AuditReport:
      await ctx.info(f"Starting audit scope={scope}")
      await ctx.report_progress(progress=0.25, total=1.0, message="Collecting capacity metrics")
      ...
  ```
- **How it helps the agent:** A long Fabric REST scan benefits from `await ctx.report_progress(...)` per collector and `await ctx.info(...)` streamed over the Streamable HTTP SSE channel — good UX in a Databricks chat surface. `ctx.request_context.lifespan_context` reaches the shared Fabric client (B5).

### B5. Lifespan / dependency injection (shared Fabric client)

- **TITLE:** `lifespan` async context manager → `ctx.request_context.lifespan_context`
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/README.md (+ `examples/snippets/servers/lowlevel/lifespan.py`)
- **Summary:** Pass an `@asynccontextmanager` to `FastMCP(lifespan=...)`. It runs once at startup, `yield`s a context object (typically a dataclass), tears down at shutdown. Tools read it via `ctx.request_context.lifespan_context`. The idiomatic home for one shared authenticated Fabric/PBI client + caches.
- **Exact identifiers:** `FastMCP("...", lifespan=app_lifespan)`; `@asynccontextmanager async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]`; read via `ctx.request_context.lifespan_context.<field>`. (Low-level: `Server("name", lifespan=...)` yields a dict, read via `server.request_context.lifespan_context["db"]`.)
  ```python
  from contextlib import asynccontextmanager
  from dataclasses import dataclass

  @dataclass
  class AppContext:
      fabric: FabricApiClient

  @asynccontextmanager
  async def app_lifespan(server: FastMCP):
      fabric = await FabricApiClient.connect()   # acquire SP/managed-identity token once
      try:
          yield AppContext(fabric=fabric)
      finally:
          await fabric.aclose()

  mcp = FastMCP("Fabric Audit", lifespan=app_lifespan)
  ```
- **How it helps the agent:** Build the authenticated Fabric/PBI REST client (+ Power BI Admin client, token cache, throttle limiter) once in `app_lifespan`; `run_audit` pulls it from `ctx.request_context.lifespan_context`. Avoids per-call re-auth; natural home for the Databricks service-principal / managed-identity credential acquired at app startup.

### B6. Authentication — `TokenVerifier` + `AuthSettings` (OAuth Resource Server)

- **TITLE:** OAuth Resource-Server auth — `TokenVerifier`, `AccessToken`, `AuthSettings`, `get_access_token()`
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/auth/provider.py (+ `settings.py`, `middleware/auth_context.py`, `examples/servers/simple-auth/...`)
- **Summary:** For HTTP transports, FastMCP can act as an OAuth 2.0 Resource Server: supply a `TokenVerifier` (validates incoming bearer tokens) + `AuthSettings` (issuer URL, this RS's URL, required scopes). FastMCP then wraps `/mcp` in `RequireAuthMiddleware` (401 + `WWW-Authenticate` → protected-resource metadata) and runs `BearerAuthBackend` + `AuthContextMiddleware`; tools read the validated token via `get_access_token()`. Constructor rule: if `auth` is set, supply **exactly one** of `token_verifier` or `auth_server_provider`.
- **Exact identifiers:** `from mcp.server.auth.provider import AccessToken, TokenVerifier`; `class TokenVerifier(Protocol): async def verify_token(self, token: str) -> AccessToken | None`; `AccessToken(token, client_id, scopes, expires_at, resource, subject, claims)`; `from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions`; `AuthSettings(issuer_url, resource_server_url, required_scopes, ...)`; `FastMCP(name=..., token_verifier=..., auth=AuthSettings(...))`; `from mcp.server.auth.middleware.auth_context import get_access_token`. The repo ships `IntrospectionTokenVerifier` (RFC 7662 introspection, checks `active`, validates RFC 8707 `aud`/resource via `check_resource_allowed`, rejects non-HTTPS introspection URLs).
  ```python
  @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
  def run_audit(...) -> AuditReport:
      token = get_access_token()                      # validated bearer for THIS request
      if token is None or "audit:read" not in token.scopes:
          raise ValueError("Missing required scope audit:read")
      ...
  ```
- **How it helps the agent:** Implement a `TokenVerifier` that validates the incoming Entra ID / Databricks token (JWKS or introspection); gate `run_audit` on `required_scopes=["audit:read"]`. `get_access_token().subject` gives the calling principal for audit logging and downstream OBO calls. Note: SDK-native auth is bearer/RS-style — confirm whether the Databricks App presents a standard `Authorization: Bearer` vs injected headers (header path → a `@mcp.custom_route` middleware / `ctx.request_context` read instead).

### B7. Input validation & errors — `ToolError`, `isError`, `Field(...)`

- **TITLE:** Errors map to `isError`; `ToolError`; Pydantic `Field` for param docs/constraints
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/exceptions.py (+ `tools/base.py`, `types.py`, `examples/fastmcp/parameter_descriptions.py`)
- **Summary:** In FastMCP, raise a normal exception from a tool; `Tool.run()` catches it and wraps it as `ToolError(f"Error executing tool {self.name}: {e}")`, which the framework converts into a `CallToolResult(isError=True, content=[...])`. You rarely build `CallToolResult` yourself. `Field(description=...)` flows into the per-property `inputSchema` description; constraints (`ge`, `le`, `max_length`) appear in the schema and are enforced by Pydantic before the function runs.
- **Exact identifiers:** `from mcp.server.fastmcp.exceptions import ToolError` (hierarchy `FastMCPError` → `ValidationError`/`ResourceError`/`ToolError`); `CallToolResult(content, structuredContent=None, isError=False)`; `from pydantic import Field`.
  ```python
  @mcp.tool()
  def run_audit(
      lookback_days: int = Field(description="Days of history to audit", ge=1, le=90, default=30),
      top_n: int = Field(description="Number of top users to return", ge=1, le=100, default=10),
  ) -> AuditReport:
      ...
      # on a Fabric API failure:
      raise ToolError("Capacity metrics API returned 429; retry later")
  ```
- **How it helps the agent:** Constrained, self-documenting `run_audit` params (validated automatically); Fabric REST failures become `isError=True` with an instructive message instead of a crashed transport.

### B8. Low-level `Server` API (when FastMCP is too high-level)

- **TITLE:** `mcp.server.lowlevel.Server`, `@server.list_tools()`, `@server.call_tool(validate_input=...)`
- **URL:** https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/lowlevel/server.py (+ stateless streamable-http example)
- **Summary:** The low-level `Server` gives full protocol control: write `@server.list_tools()` returning `list[types.Tool]` with hand-authored `inputSchema`/`outputSchema`, and `@server.call_tool()` dispatching by name. With `validate_input=True` (default) it runs `jsonschema.validate(arguments, tool.inputSchema)`; if a tool declares `outputSchema`, the handler validates `structuredContent` or errors. A handler may return unstructured content, a `dict` (→ structuredContent), a `(unstructured, structured)` tuple, or a full `types.CallToolResult`. Served over Streamable HTTP via `StreamableHTTPSessionManager(app=server, stateless=True, json_response=...)` mounted at `/mcp`.
- **Exact identifiers:** `from mcp.server.lowlevel import Server, NotificationOptions`; `import mcp.types as types`; `Server("name", version=None, instructions=None, lifespan=...)`; `@server.list_tools()`, `@server.call_tool(validate_input=True)`; `server.request_context`.
- **How it helps the agent:** Drop to the low-level `Server` if you need hand-authored exact `inputSchema`/`outputSchema` for `run_audit` (precise union types for `top_users`/`capacity_usage`), custom error codes, or explicit session-manager lifecycle control — the cleanest fit for a Databricks App managing its own ASGI process. Otherwise FastMCP (B1–B7) is sufficient with far less boilerplate.

---

## PART C — ANTHROPIC CLAUDE API (the reasoner)

### C1. Messages API tool use — definitions, `tool_choice`, the tool_use→tool_result loop

- **TITLE:** Tool use — `tools` array, `tool_choice`, parallel calls, `tool_use`/`tool_result` loop
- **URLs:** https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview.md · .../define-tools.md · .../handle-tool-calls.md
- **Summary:** Each tool in the top-level `tools` array has `name` (regex `^[a-zA-Z0-9_-]{1,64}$`), `description`, `input_schema` (JSON Schema), plus optional `input_examples`, `cache_control`, `strict`, `defer_loading`, `allowed_callers`. `tool_choice`: `{"type":"auto"}` (default with tools), `{"type":"any"}` (must use some tool), `{"type":"tool","name":"run_audit"}` (force one), `{"type":"none"}`. Any choice accepts `disable_parallel_tool_use: true`. **With extended/adaptive thinking, only `auto` and `none` are supported** — `any`/`tool` error. Response: `stop_reason: "tool_use"` + `tool_use` blocks (`id`, `name`, `input`). You reply with a `user` message whose content leads with `tool_result` blocks (`tool_use_id`, optional `content`, optional `is_error: true`). **Hard rules:** `tool_result` must immediately follow the assistant's `tool_use` message and come FIRST in the user content array (text after). Best practice: descriptions of **3-4+ sentences** ("by far the most important factor in tool performance"); consolidate related operations into fewer tools with an `action` param; return only high-signal fields. Treat tool results as untrusted (keep inside `tool_result`, not `system`).
- **Exact identifiers:** `tools[]` (`name`, `description`, `input_schema`, `strict`, `cache_control`); `tool_choice` (`auto`/`any`/`tool`/`none`, `disable_parallel_tool_use`); content blocks `tool_use` (`id`/`name`/`input`), `tool_result` (`tool_use_id`/`content`/`is_error`); `stop_reason: "tool_use"`.
- **How it helps the agent:** This is the literal `run_audit` loop: the Claude reasoner emits `tool_use{name:"run_audit"}`, your code runs the read-only audit, you return findings in a `tool_result`, Claude interprets and coaches. Keep `tool_choice` at `auto` (required because the agent uses adaptive thinking — C7); bias toward calling via prompting, not forced choice. Because audit data comes from Fabric/PBI (partly user-controlled metadata), keep all collected data inside `tool_result` blocks (indirect-prompt-injection containment). Consolidate `top_users`/`capacity_usage` as sub-modes of `run_audit` (or as a small focused tool set with rich descriptions).

### C2. Structured outputs / JSON

- **TITLE:** Structured outputs — `output_config.format` (json_schema), `strict: true` tools, `messages.parse()`
- **URL:** https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md
- **Summary:** Force the **final response** to a JSON Schema via `output_config: {format: {type: "json_schema", schema: {...}}}` (the old top-level `output_format` moved here). `strict: true` on a tool guarantees tool *inputs* match the schema (`additionalProperties` must be `false`). Supported schema features: object/array/string/integer/number/boolean/null, enums (scalars), `const`/`anyOf`/`allOf`, internal `$ref`/`$def`, `default`/`required`/`additionalProperties:false`, string formats (`date-time`/`date`/`email`/`uri`/`uuid`/`ipv4`/`ipv6`/...), `minItems` of 0 or 1, regex (quantifiers/classes/groups). **Unsupported (→400):** recursive schemas, numeric constraints (`minimum`/`maximum`/`multipleOf`), string length (`minLength`/`maxLength`), array constraints beyond `minItems` 0/1, external `$ref`, regex backreferences/lookaround. Limits: ≤20 strict tools, ≤24 optional params across strict schemas, ≤16 union params, 180s compile. First request compiles the grammar (latency); compiled grammar **cached 24h from last use** (changing only `name`/`description` does NOT invalidate). Incompatible with citations; limited prefill support. Changing `output_config.format` invalidates the **prompt** cache. On `stop_reason: "refusal"` or `"max_tokens"`, output may not match the schema — check `stop_reason` before trusting JSON. SDK helper `client.messages.parse(output_format=<PydanticModel>)` → `response.parsed_output`.
- **Exact identifiers:** `output_config.format` (`type: "json_schema"`, `schema`), `strict: true`, `additionalProperties: false`, `messages.parse()` / `response.parsed_output`; caveats `stop_reason: "refusal"`/`"max_tokens"`.
- **How it helps the agent:** The clean way to emit **structured audit findings**. Either set `output_config.format` to a `json_schema` describing the findings array (`severity`, `resource`, `recommendation`, `cu_impact`, `evidence`), or put `strict: true` on `run_audit` so the *call* is schema-perfect. Keep the findings schema **byte-stable across runs** so the 24h grammar cache and the prompt cache both survive (toggling `output_config.format` busts the prompt cache). Always check `stop_reason` before parsing.

### C3. Prompt caching

- **TITLE:** Prompt caching — `cache_control: {type:"ephemeral"}`, breakpoints, prefix-match, TTL, economics
- **URL:** https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md
- **Summary:** Mark a block with `cache_control: {type: "ephemeral"}` (optional `ttl: "1h"`; default 5m). **Max 4 breakpoints.** **Render/cache order is mandatory: `tools` → `system` → `messages`.** Prefix-match invariant: a hit requires a 100% identical prefix up to the breakpoint; lookback ≤20 positions. Usage fields: `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens` (uncached remainder); total = sum. Minimum cacheable prefix per model — **Opus 4.8: 1,024 tokens** (Opus 4.7: 2,048; Opus 4.6 / Haiku 4.5: 4,096; Sonnet 4.6: 1,024; Fable 5: 512). Economics: 5m write **1.25×**, 1h write **2×**, read **0.1×**, output unaffected. Cache system + tools by placing `cache_control` on the **last tool definition** and on the **system block**. Whole-cache invalidators: tool-definition changes, web-search/citations toggle, speed change. Message-only invalidators: `tool_choice` changes, image add/remove, thinking param changes. Pre-warm with `max_tokens: 0`.
- **Exact identifiers:** `cache_control: {"type":"ephemeral"}` / `{"type":"ephemeral","ttl":"1h"}`; usage `cache_creation_input_tokens`/`cache_read_input_tokens`/`input_tokens`; pre-warm `max_tokens: 0`.
- **How it helps the agent:** The agent's audit-policy system prompt + the `run_audit` tool definition are large and stable — cache both (breakpoint on the last tool and on the system block). On Opus 4.8 the system prompt easily exceeds the 1,024-token minimum. Reads at **0.1×** make recurring audits dramatically cheaper. Because audits run hours apart, prefer **`ttl: "1h"`** for the system+tools prefix. Keep tool definitions and `output_config.format` byte-stable across runs so the cache survives. (`tool_choice` changes only bust message cache, so swapping it is cheap; thinking-mode switches also only bust message cache.)

### C4. Streaming

- **TITLE:** Streaming — `stream=True`, SSE event types, `input_json_delta`, `get_final_message()`
- **URL:** https://platform.claude.com/docs/en/build-with-claude/streaming.md
- **Summary:** Set `stream: true` (or SDK `client.messages.stream(...)`). Event flow: `message_start` → per block `content_block_start` → `content_block_delta` (×N) → `content_block_stop` → `message_delta` (top-level + **cumulative `usage`**) → `message_stop`; plus `ping`/`error` (`overloaded_error` ≈ 529). Delta types: `text_delta`, `input_json_delta` (tool inputs stream as **partial JSON strings** in `partial_json`; accumulate then parse on `content_block_stop`), `thinking_delta`, `signature_delta` (encrypted thinking signature, just before `content_block_stop`). Tool-use stream ends with `message_delta {stop_reason:"tool_use"}`. SDKs **require streaming for large `max_tokens`** to avoid HTTP timeouts; use `stream.get_final_message()` (Python) / `.finalMessage()` (TS) to get the full `Message`. `display: "omitted"` suppresses `thinking_delta` (faster time-to-first-text).
- **Exact identifiers:** events `message_start`/`content_block_start`/`content_block_delta`/`content_block_stop`/`message_delta`/`message_stop`/`ping`/`error`; deltas `text_delta`/`input_json_delta`(`partial_json`)/`thinking_delta`/`signature_delta`; `stream.get_final_message()`.
- **How it helps the agent:** When the reasoner runs Opus 4.8 at high/xhigh effort with large `max_tokens` (~64K recommended), **streaming is effectively required** even if you only consume the final message via `get_final_message()`. For the Teams/conversational surface, stream the interpretation text live. Accumulate `input_json_delta` to reconstruct the `run_audit` call.

### C5. System prompts

- **TITLE:** System prompt — string or array-of-text-blocks (cacheable)
- **URL:** https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md (system-as-array form)
- **Summary:** The top-level `system` param accepts a string (`"system": "..."`) or an **array of text blocks** (`[{"type":"text","text":"...","cache_control":{"type":"ephemeral"}}]`). Use the array form to attach `cache_control`. System renders **after tools, before messages** in the cache prefix.
- **Exact identifiers:** `system` (string | `[{type:"text", text, cache_control}]`).
- **How it helps the agent:** Put the full audit-reasoner persona/policy (capacity heuristics, coaching style, output contract) in one cached `system` text block. Combined with cached tool defs, every recurring run reads both at 0.1×. Keep the system prompt frozen (no interpolated dates/IDs at the front) to preserve the prefix.

### C6. Token counting & cost

- **TITLE:** Token counting — `messages.count_tokens`, `usage` fields, cost estimation
- **URL:** https://platform.claude.com/docs/en/build-with-claude/token-counting.md
- **Summary:** `POST /v1/messages/count_tokens` (SDK `client.messages.count_tokens(...)`) accepts the same inputs as message creation (`model`, `messages`, `system`, `tools`, images, PDFs, `thinking`) and returns `{ "input_tokens": N }`. It's a free estimate with separate rate limits and does NOT use caching; you're not billed for system-added tokens. On real responses, `usage` carries `input_tokens`, `output_tokens` (authoritative billing total), `cache_creation_input_tokens`, `cache_read_input_tokens`, and (with thinking) `output_tokens_details.thinking_tokens`. Do **not** use `tiktoken` — it undercounts Claude tokens.
- **Exact identifiers:** `messages.count_tokens` → `input_tokens`; `usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens_details.thinking_tokens}`.
- **How it helps the agent:** Before each scheduled `run_audit` reasoning call, `count_tokens` on the assembled prompt (system + tools + findings) to predict cost and stay within context/rate limits. After each call, log `usage` — especially `cache_read_input_tokens` (confirm caching works) and `thinking_tokens` (reasoning cost) — to track recurring-audit spend.

### C7. Extended / adaptive thinking + effort

- **TITLE:** Adaptive thinking (`thinking: {type:"adaptive", display}`) + effort (`output_config.effort`)
- **URLs:** https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking.md · .../effort.md
- **Summary:** Enable with `thinking: {type: "adaptive"}` (no beta header). On **Opus 4.8 / 4.7, adaptive is the ONLY mode** — `{type:"enabled", budget_tokens:N}` → 400 (`budget_tokens` removed; deprecated-but-functional on Opus 4.6 / Sonnet 4.6). On Opus 4.8, thinking is **off unless you set `{type:"adaptive"}`**. Adaptive auto-enables **interleaved thinking** (between tool calls) — ideal for the agentic loop. `display: "summarized" | "omitted"` — default on Opus 4.8/4.7 is `"omitted"` (empty `thinking` text, `signature` still present); same cost either way. Effort: `output_config: {effort: "low"|"medium"|"high"|"xhigh"|"max"}` (default `high`; `xhigh` on Opus 4.8/4.7; `max` on 4.6+/Sonnet 4.6). Effort affects ALL output (text + tool calls + thinking). Guidance: start `xhigh` for agentic/coding, `high` for intelligence-sensitive work, step down after eval; set large `max_tokens` (~64K) at xhigh/max. **Multi-turn replay:** pass thinking blocks back UNCHANGED between tool calls (server decrypts `signature`; with `omitted`, the `signature` is what matters). **Interaction:** with thinking active, `tool_choice` is restricted to `auto`/`none` (`any`/`tool` error).
- **Exact identifiers:** `thinking: {"type":"adaptive","display":"summarized"|"omitted"}`; `output_config: {"effort": "low"|"medium"|"high"|"xhigh"|"max"}`; restriction: thinking ⇒ `tool_choice` ∈ {`auto`,`none`}.
- **How it helps the agent:** Core reasoner config — run Opus 4.8 with `thinking: {"type":"adaptive"}` + `output_config: {"effort":"xhigh"}` for thorough audit reasoning across the interleaved `run_audit` loop. Drop to `medium`/`low` for a cheaper recurring pass or a severity-tagging subagent. Keep `tool_choice` at `auto`. Round-trip prior thinking blocks unchanged or the API rejects the turn. Use `display:"omitted"` (the default) unless surfacing reasoning to users.

### C8. Model selection

- **TITLE:** Model selection — `claude-opus-4-8` / `claude-sonnet-4-6` / `claude-haiku-4-5`
- **URL:** https://platform.claude.com/docs/en/about-claude/models/overview.md
- **Summary:**

  | Model | ID | In/Out $/MTok | Context | Max out | Adaptive thinking |
  |---|---|---|---|---|---|
  | Claude Opus 4.8 | `claude-opus-4-8` | $5 / $25 | 1M | 128K | Yes |
  | Claude Sonnet 4.6 | `claude-sonnet-4-6` | $3 / $15 | 1M | 128K | Yes |
  | Claude Haiku 4.5 | `claude-haiku-4-5` | $1 / $5 | 200K | 64K | No |
  | Claude Opus 4.7 | `claude-opus-4-7` | $5 / $25 | 1M | 128K | Yes |
  | Claude Fable 5 | `claude-fable-5` | $10 / $50 | 1M | 128K | Yes (always on) |

  Opus 4.8 for the most complex reasoning / ambiguous judgments / complex tool loops; Sonnet 4.6 for the best speed/intelligence balance; Haiku 4.5 for fast/cheap mechanical tasks (no adaptive thinking — may infer missing params).
- **Exact identifiers:** `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-7`, `claude-fable-5`.
- **How it helps the agent:** Use **`claude-opus-4-8`** as the primary audit reasoner — it best handles the ambiguous "is this capacity healthy?" judgment and the `run_audit` loop. For cost-sensitive recurring passes / summarization / severity-tagging subagents, drop to **`claude-sonnet-4-6`** (40% cheaper) or **`claude-haiku-4-5`** (80% cheaper, but no adaptive thinking — reserve for mechanical subtasks).

### C9. MCP connector — Claude consuming a remote MCP server directly

- **TITLE:** MCP connector — `mcp_servers` param (Claude as MCP client over the Messages API)
- **URL:** https://platform.claude.com/docs/en/agents-and-tools/mcp-connector.md
- **Summary:** The Messages API can connect to **remote HTTP MCP servers** directly (no separate MCP client) via `client.beta.messages.create(..., mcp_servers=[...], tools=[{"type":"mcp_toolset",...}], betas=["mcp-client-2025-11-20"])`. Server def: `{"type":"url", "url": "https://...", "name": "...", "authorization_token": "..."}` (URL must be `https://`; supports **Streamable HTTP and SSE** transports; STDIO not supported). The `mcp_toolset` tool (in `tools`) selects/configures tools (`default_config`/`configs` with `enabled`/`defer_loading`; allowlist/denylist patterns; `cache_control`). Validation: each server must be referenced by exactly one toolset. Response adds `mcp_tool_use` (`id`, `name`, `server_name`, `input`) and `mcp_tool_result` (`tool_use_id`, `is_error`, `content`) blocks. Only **tool calls** supported (not resources/prompts). Beta header: **`mcp-client-2025-11-20`** (prior `mcp-client-2025-04-04` deprecated; tool config moved from server def into the `mcp_toolset`). Not ZDR-eligible. SDK client-side helpers exist for local/stdio + prompts/resources (`anthropic.lib.tools.mcp`, `pip install anthropic[mcp]`).
- **Exact identifiers:** `mcp_servers=[{type:"url", url, name, authorization_token}]`; `tools=[{type:"mcp_toolset", mcp_server_name, default_config:{enabled,defer_loading}, configs:{}, cache_control}]`; beta `mcp-client-2025-11-20`; blocks `mcp_tool_use`/`mcp_tool_result`.
- **How it helps the agent:** Two complementary roles. (1) The agent **is** the MCP server (`run_audit`) — Parts A/B. (2) The agent's own Claude reasoner can, via this connector, attach **the very same `run_audit` MCP server URL** and let Claude call it directly over the Messages API — collapsing the manual tool loop into one API call. Use the **denylist pattern** (or allowlist `run_audit`/`top_users`/`capacity_usage`) to expose only read-only tools (the docs explicitly recommend denylisting write/destructive tools "when building read-only assistants"). Note the connector is **HTTP-only** (matches the Databricks-App Streamable-HTTP server) and **not ZDR-eligible** — relevant if the org has data-retention constraints (Fable 5's 30-day-retention requirement is a separate concern if that model is ever used).

---

## Synthesis — recommended shape for the agent

**As an MCP server (Databricks App, Streamable HTTP):**
- FastMCP v1.x: `FastMCP("Fabric Audit", stateless_http=True, json_response=False, host="0.0.0.0", port=<app port>)` serving at `/mcp`; or expose `mcp.streamable_http_app()` to the App's process manager.
- `run_audit` (+ optional `top_users`/`capacity_usage`) as `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))` returning a Pydantic `AuditReport` (→ `outputSchema`/`structuredContent`).
- Shared Fabric/PBI client in `lifespan`; read via `ctx.request_context.lifespan_context`; progress via `ctx.report_progress`.
- Auth: `TokenVerifier` + `AuthSettings(required_scopes=["audit:read"], resource_server_url=...)`; **validate token audience**, **never pass the client token to Fabric/PBI** (separate least-privilege Entra token). Unauthenticated `@mcp.custom_route("/health")`.
- Long scans: SSE response mode + `Last-Event-ID` resumability. Validate all inputs (allowlist capacity IDs/date ranges).

**As a Claude reasoner (Anthropic SDK):**
```python
response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=64000,                                # room for thinking + findings
    thinking={"type": "adaptive"},                   # only mode on 4.8; off unless set
    output_config={"effort": "xhigh"},               # agentic default; step down after eval
    system=[{"type": "text", "text": AUDIT_POLICY,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
    tools=[{"name": "run_audit", "description": "...3-4+ sentences...",
            "input_schema": {...}, "strict": True,
            "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
    tool_choice={"type": "auto"},                    # any/tool unavailable with thinking
    messages=[...],
)
# Loop on stop_reason == "tool_use"; return run_audit findings in a tool_result block,
# passing prior thinking blocks back UNCHANGED. Stream for long/high-max_tokens calls.
# For structured findings, set output_config.format to a stable json_schema, or strict:true on run_audit.
# Log usage.cache_read_input_tokens and usage.output_tokens_details.thinking_tokens each run.
```
Optionally, attach the same `run_audit` MCP server to the reasoner via the **MCP connector** (`mcp_servers` + `mcp_toolset`, beta `mcp-client-2025-11-20`) to let Claude call it directly.

---

## Flat URL list

- https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- https://modelcontextprotocol.io/specification/2025-06-18/server/resources
- https://modelcontextprotocol.io/specification/2025-06-18/server/prompts
- https://modelcontextprotocol.io/specification/2025-06-18/server/utilities/pagination
- https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
- https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices
- https://modelcontextprotocol.io/specification/2025-06-18/changelog
- https://modelcontextprotocol.io/specification/2025-06-18/schema
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-06-18/schema.ts
- https://github.com/modelcontextprotocol/python-sdk
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/README.md
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/server.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/tools/base.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/fastmcp/exceptions.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/types.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/lowlevel/server.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/auth/provider.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/auth/settings.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/src/mcp/server/auth/middleware/auth_context.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/fastmcp/weather_structured.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/fastmcp/parameter_descriptions.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/servers/simple-streamablehttp-stateless/mcp_simple_streamablehttp_stateless/server.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/snippets/servers/lowlevel/lifespan.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/servers/simple-auth/mcp_simple_auth/token_verifier.py
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/examples/servers/simple-auth/mcp_simple_auth/server.py
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview.md
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools.md
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls.md
- https://platform.claude.com/docs/en/agents-and-tools/mcp-connector.md
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md
- https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md
- https://platform.claude.com/docs/en/build-with-claude/streaming.md
- https://platform.claude.com/docs/en/build-with-claude/token-counting.md
- https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking.md
- https://platform.claude.com/docs/en/build-with-claude/effort.md
- https://platform.claude.com/docs/en/about-claude/models/overview.md
- https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python

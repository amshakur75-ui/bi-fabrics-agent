# Blast-Radius Map — Core Agent Files

Read-only survey of the six load-bearing files that gate Phases 2 and 3. For each: purpose,
public API with exact signatures, callers (grepped across both `fabric-audit-agent-app/` and the
sibling `fabric-audit-mcp/`), and the dict keys / return shapes callers depend on. Nothing here
was modified.

**Repo layout note.** The `query/kql_guard.py` and `query/firewall.py` referenced by the task live
at `fabric_audit_agent/query/` inside `fabric-audit-agent-app/`. The MCP pull surface lives in the
sibling repo `fabric-audit-mcp/` (canonical absolute path
`C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent\fabric-audit-mcp\`); it imports from
`fabric_audit_agent` via the installed wheel — dependency flows one way only, MCP → core.

---

## TL;DR (the three facts that gate Phases 2 & 3)

1. **Tool-registration pattern (`tools.py`).** Every tool is a closure `def <name>_handler(_input=None)`
   defined inside `create_tool_definitions(base_dir=None)`, plus one dict appended to the list that
   function returns:
   ```python
   {"name": str, "description": str, "input_schema": {...json-schema...}, "handler": <callable>}
   ```
   A handler takes **one optional positional arg** (`_input`, a dict — may be `None`/absent) and
   returns a **JSON-serializable dict**. To add a tool: write the handler closure, append its dict
   to the returned list. That is the whole contract. Consumers key off `name`/`description`/
   `input_schema`/`handler` (see §1.5).

2. **agent.py vs loop.py divergences.** The two tool loops are structurally identical (same
   force-answer step, same budget-nudge text, same empty-answer retry+fallback, same
   `_blocks_to_dicts`). **Three real divergences** exist and any future edit must preserve/​mirror
   them deliberately (see §4):
   - `toolResults` element shape: loop.py emits `{"tool","result"}`; agent.py emits
     `{"tool","callId","input","result"}`.
   - Dedup cache-hit `note` string differs (`"...skipped; see earlier result"` vs `"...skipped"`).
   - agent.py adds an `on_tool` progress callback + an empty-synthesis `print(...)` log line;
     loop.py has neither. Plus the obvious sync-vs-async mechanics.

3. **`kql_guard.py` public signatures (verbatim — callers must NOT change these):**
   ```python
   def escape_string(value):
   def escape_entity(name):
   def first_statement(text):
   def assert_read_only_kql(kql):
   def assert_kusto_host(cluster_uri):
   ```
   Plus one underscore-"private" helper that is imported across modules and is effectively public:
   ```python
   def _strip_string_literals(text):
   ```

---

## 1. `fabric_audit_agent/tools.py`  (180 KB, ~3277 lines)

### 1.1 Purpose
Tool definitions in Anthropic/MCP format exposing the read-only audit. Each tool carries a
`handler(input)` the host invokes; the audit is **READ-ONLY** — handlers read (mock or live)
telemetry and write findings to local files, never mutating any estate.
`data_agent.build_data_agent_manifest` strips the handler for the published manifest (keeps
name/description/input_schema). Confirmed against the code: the module ends in a single big
`return [ {...}, ... ]` of 26 tool dicts (line 2577–3276).

### 1.2 The one public entry point
```python
def create_tool_definitions(base_dir=None):
```
- `base_dir` defaults to `_BASE` (repo root = two dirs above this file). Threaded into
  `_collector_or_mock` to locate `fixtures/estate.json`.
- Returns a **list of 26 tool dicts** (order below). All handlers are inner closures capturing
  `base` and `os.environ`.

### 1.3 The 26 tools (name → handler closure; all return dicts)
Names in list order (line of `"name":` in the return list):

| # | name | handler closure | notes |
|---|------|-----------------|-------|
| 1 | `run_audit` | `run_audit_handler` | returns `summary/verdict/findings/gates(+optional digest,narrative,roadmap,healthScore,staggerPlan,correlations,forecast)` |
| 2 | `list_workspaces` | `list_workspaces_handler` | `workspaces/topUsers/totalWorkspaces/totalItems/source` (returns empty+`note` when no live source) |
| 3 | `user_activity` | `user_activity_handler` | optional `user` arg |
| 4 | `investigate_user` | `investigate_user_handler` | |
| 5 | `investigate_capacity_spike` | `investigate_spike_handler` | |
| 6 | `user_spike_history` | `user_spike_history_handler` | requires `user` |
| 7 | `spike_events` | `spike_events_handler` | |
| 8 | `capacity_peaks` | `capacity_peaks_handler` | the canonical peaks flow |
| 9 | `capacity_overloads` | `capacity_overloads_handler` | |
| 10 | `raw_events` | `raw_events_handler` | |
| 11 | `capacity_patterns` | `capacity_patterns_handler` | |
| 12 | `describe_source` | `describe_source_handler` | |
| 13 | `sample_events` | `sample_events_handler` | |
| 14 | `capacity_diagnostics` | `capacity_diagnostics_handler` | |
| 15 | `analyze_dax` | `analyze_dax_handler` | static rules; `expression` required |
| 16 | `diagnose` | `diagnose_handler` | |
| 17 | `whats_changed` | `whats_changed_handler` | |
| 18 | `user_timeline` | `user_timeline_handler` | requires `user` |
| 19 | `run_kql` | `run_kql_handler` | **the P4 firewall chokepoint** (see §1.6) |
| 20 | `query_library` | `query_library_handler` | optional `name` |
| 21 | `run_sql` | `run_sql_handler` | uses `sql_guard` |
| 22 | `run_dax` | `run_dax_handler` | uses `dax_guard` |
| 23 | `describe_sql_table` | `describe_sql_table_handler` | `table` required |
| 24 | `describe_semantic_model` | `describe_semantic_model_handler` | |
| 25 | `classify_query_target` | `classify_target_handler` | `question` required |
| 26 | `render_chart` | `render_chart_handler` | validated chart spec; **overridden in prod** (see §2.4) |

Module-level helpers of interest (hoisted out of the closure): `_capacity_kusto_query(env)`
(calls `assert_kusto_host` for anti-SSRF — the ONLY definition, deliberately not duplicated),
`dry_run(query_callable, kql)` → `{"valid": bool, "error": str|None}`, `_queryplan_estimate`,
`_adhoc_audit_log(...)`, `_live_base_cu`, `_run_real_or_mock`, `_build_collector`.

### 1.4 Imports it depends on from the query guards (relevant to Phases 2/3)
```python
from .query.kql_guard import assert_kusto_host as _assert_kusto_host, escape_entity as _escape_entity
from .query.kql_guard import assert_read_only_kql as _assert_read_only_kql   # NOTE: imported, not used elsewhere (dead import)
from .query.sql_guard import assert_read_only_sql as _assert_read_only_sql, escape_sql_identifier ..., _MAX_SQL_ROWS
from .query.dax_guard import assert_read_only_dax as _assert_read_only_dax, escape_dax_reference ..., _MAX_DAX_ROWS
from .query.firewall import validate_adhoc_kql, FirewallRejection   # imported lazily INSIDE run_kql_handler (line 2077)
```
`_escape_entity` used in `describe_source`/`sample_events` KQL builders (lines 1560, 1572, 1614,
1628). `_assert_kusto_host` used in `_capacity_kusto_query` (line 162). **`_assert_read_only_kql`
is imported at line 41 but never referenced again** — a harmless dead import (the firewall calls
`assert_read_only_kql` itself). Flag if a Phase-2/3 edit "cleans up" imports.

### 1.5 Who imports / calls `create_tool_definitions`
Module import path: `from fabric_audit_agent.tools import create_tool_definitions`.
- **`fabric-audit-mcp/fabric_audit_mcp/mcp_server.py`** — `manifest()` (via `build_data_agent_manifest`)
  and `build_mcp_server()` (registers every tool with FastMCP via `_make_tool_fn`). **This is how
  the production async app gets the tools** — the app sources them over MCP, it does NOT import
  `tools.py` directly.
- **`fabric-audit-agent-app/agent_server/investigator.py`** — the sync eval path imports it
  directly (`create_tool_definitions(base_dir)` → `to_anthropic_tools`/`build_dispatch`).
- **`fabric-audit-agent-app/fabric_audit_agent/agent/tools_anthropic.py`** — `to_anthropic_tools`
  and `build_dispatch` consume the returned list (see §1.5-shape).
- Tests: `fabric-audit-agent-app/tests/test_agent_tools_anthropic.py`,
  `fabric-audit-mcp/tests/test_mcp_tools.py` (heavy — pulls handlers out by name in ~80 places),
  `fabric-audit-mcp/tests/test_entrypoints.py`.

Shape consumers depend on (from `tools_anthropic.py`):
```python
def to_anthropic_tools(tool_defs):  # -> [{"name","description","input_schema"}, ...]  (drops handler)
def build_dispatch(tool_defs):      # -> {name: handler}
```
`data_agent.build_data_agent_manifest` reads `t.get("name")`, `t.get("description")`,
`t.get("input_schema")` per tool. `mcp_server._make_tool_fn` reads `input_schema["properties"]` and
`input_schema["required"]` to synthesize a per-tool signature (FastMCP ignores the `input_schema`
dict itself and derives from the function signature — so **`required` and `properties` are
load-bearing**, not cosmetic).

### 1.6 Return-shape keys callers depend on (per-tool, load-bearing)
- Every tool result is a dict; error results use `{"error": str, ...}` and often
  `"source": "live"|"mock"|"none"`.
- `run_audit`: `summary, verdict, findings, gates{throttleClaim,pressureClaim,trueCuPerUser}` +
  optional keys listed above; findings get a `whenDisplay` twin.
- `run_kql` (the firewall path): success →
  `{"rows", "engine", "source":"live", "ungated":True, "ungatedNote":..., ...(+planEstimate, verifyUrl, _provenance via _finish)}`;
  rejection → `{"error": rej.reason, "rejectionStage": rej.stage, "engine", "source":"live"}`
  where `rejectionStage ∈ {length,verbatim-string,multiline-string,comment,multi-statement,control-command,denied-operator,rehearsal,execute,engine-unconfigured}`.
- `render_chart`: `{"chart": {chartType,title,series,axisLabels,sourceScope,isProxy[,proxyCaveat]}}`
  or `{"error": ...}`. `_CHART_TYPES = ("line","bar","grouped-bar","stacked-bar","pie","donut")`.
- Constants a caller might rely on: `_RUN_KQL_HARD_CAP = 1000` (server-side `| take` bound applied
  AFTER validation).

---

## 2. `agent_server/agent.py`  (37.9 KB, 722 lines) — async production tool loop

### 2.1 Purpose
Databricks App agent handler: hosts the read-only audit as an MLflow ResponsesAgent. Owns exactly
three things: (1) request/streaming transport, (2) the async tool loop over MCP-sourced tools,
(3) the Claude client adapter (§B1-alt: OpenAI chat-completions → Anthropic shape). The system
prompt lives in `system_prompt.py` (canonical single home per ADR-001). Tools are sourced over MCP
via `DatabricksMCPClient` (OAuth), not by importing `tools.py`.

### 2.2 Public surface (module-level, framework-registered)
```python
def get_user_workspace_client() -> WorkspaceClient          # cached WorkspaceClient (SP auth; OBO pending)
async def _run_tool_loop(client, *, model, system, messages, tools, dispatch, max_steps=6, on_tool=None)
async def _mcp_tools_and_dispatch(ws)                        # -> (tools, dispatch), TTL-cached (300s)
def _build_claude_client(ws)                                 # -> _Client with .messages.create(...)
def _messages_from_request(request)                          # -> [{"role","content"}, ...]
async def _run(request, on_tool=None)                        # orchestrates one turn
@invoke()  async def invoke_handler(request) -> ResponsesAgentResponse
@stream()  async def stream_handler(request)                 # SSE progress + final
def build_system_prompt()  # (imported from system_prompt)
```
Registered with MLflow by import side-effect in `agent_server/start_server.py`
(`import agent_server.agent` → `@invoke`/`@stream` register the handlers; `AgentServer` serves them).
No other module imports `invoke_handler`/`stream_handler` directly.

### 2.3 Step-budget classifier (pre-call, deterministic)
`_step_budget(question)` → `_INVESTIGATION_BUDGET=12` if any `_INVESTIGATION_HINTS` keyword matches,
else `_LOOKUP_BUDGET=6`. When a lookup exhausts its budget (`stoppedReason=="budget"`) `_run`
appends a disclosure sentence. Progress presentation: `_PROGRESS_PHRASES` (maps ~24 tool names +
6 direct-fabric tools to plain phrases), `_scope_hint`, `_progress_text`, `_progress_line`,
`_plain_trail`. `_conversation_audit_log` emits one `[conversation]` line (scrubbed via
`_scrub_secrets`; never logs tool inputs or full answer).

### 2.4 Tool assembly in `_run` (order matters — later wins on name collision)
1. MCP tools+dispatch (`_mcp_tools_and_dispatch`).
2. `+ direct_tools` from `fabric_direct.direct_tools_and_dispatch(os.environ)` (inert without SP creds).
3. **`render_chart` override**: drop any MCP `render_chart`, add `chart_tool.chart_tool_and_dispatch()`
   (tolerant + donut). Our dispatch wins.
4. `+ ticket_tools` from `ticket_tool.create_ticket_tool_and_dispatch()` — the ONE write surface
   (`create_notification_ticket`).

### 2.5 Return shape of `_run` / `_run_tool_loop`
`_run_tool_loop` returns `{"text","trajectory","toolResults","stoppedReason"}` with
`stoppedReason ∈ {"answer","budget"}`. `_run` adds `"trail"` (plain-language). `invoke_handler`
surfaces `custom_outputs={"trajectory","toolResults","stoppedReason","trail"}` and runs the text
through `append_chart_fences(text, toolResults)`.

`toolResults` element (agent.py): `{"tool": name, "callId": id, "input": input, "result": result}`.
`trajectory` element: `{"tool": name, "input": input}` (both twins identical here).

---

## 3. `agent_server/loop.py`  (4.8 KB, 82 lines) — sync eval twin

### 3.1 Purpose
Sync ReAct tool-loop used by the eval harness (ADR-001). Production runs the async twin in
`agent.py`; this sync version is driven only by `investigator.investigate()` with a
`ScriptedClient`. The docstring states the invariant explicitly: **the two loops must stay
structurally in sync** — dedup of identical read-only calls, budget-exhaustion nudge before the
forced-answer step, and `wrap_untrusted` on every tool result.

### 3.2 Public surface
```python
def run_tool_loop(client, *, model, system, messages, tools, dispatch, max_steps=6):
def _blocks_to_dicts(content):   # identical to agent.py's
```
Returns `{"text","trajectory","toolResults","stoppedReason}` — same keys as the async twin.

### 3.3 Who calls it
- `agent_server/investigator.py::investigate(...)` (the only production caller).
- Tests: `tests/test_agent_loop_sync.py` (direct), `tests/test_agent_investigator.py` (via
  `investigate`). `investigate` in turn is called by `agent_server/eval_score.py` (the golden-case
  scorer) and `fabric-audit-mcp/tests/test_glue.py`.

---

## 4. `agent.py` ↔ `loop.py` SIDE-BY-SIDE (apply future edits to BOTH twins)

Structure is line-for-line parallel. Table maps each structural block; **DIVERGENCE** rows are the
only intentional differences.

| Block | loop.py (sync `run_tool_loop`) | agent.py (async `_run_tool_loop`) |
|-------|--------------------------------|-----------------------------------|
| Signature | `(client,*,model,system,messages,tools,dispatch,max_steps=6)` | `(...,max_steps=6, on_tool=None)` **[extra param]** |
| Init | `messages=list(messages); trajectory,cache,tool_results=[],{},[]` | identical |
| Loop | `for step in range(max_steps):` | identical |
| Force-answer | `use_tools = tools if step<max_steps-1 else []` | identical |
| Budget nudge | append `[SYSTEM] Tool budget exhausted ...` (exact same text) when `not use_tools and tools and step==max_steps-1 and trajectory` | identical text + condition |
| Model call | `resp = client.messages.create(model=,max_tokens=4096,system=,messages=,tools=use_tools)` | `resp = await asyncio.to_thread(client.messages.create, ...)` **[async mechanics]** |
| Non-tool-use branch | join text blocks; if empty → append `[SYSTEM] Your previous reply was empty...` retry (tools=[]) → if still empty → identical fallback string; return `stoppedReason:"answer"` | same, **plus** `print(f"[agent] empty synthesis after ... retrying tool-less", flush=True)` before the retry **[DIVERGENCE: log line only in agent.py]** |
| Empty-retry call | `client.messages.create(...tools=[])` | `await asyncio.to_thread(client.messages.create, ...tools=[])` |
| Append assistant | `messages.append({"role":"assistant","content":_blocks_to_dicts(resp.content)})` | identical |
| Per-tool iterate | `for b in resp.content: if type!="tool_use": continue` | identical, **plus** `if on_tool is not None: await on_tool(b.name, b.input)` fired BEFORE the dedup/cache check **[DIVERGENCE: progress hook; note it fires even for cached dup calls]** |
| Dedup key | `key=(b.name, json.dumps(b.input, sort_keys=True, ensure_ascii=False))` | identical |
| Cache hit | `{"note":"duplicate read-only tool call skipped; see earlier result","cached":cache[key]}` | `{"note":"duplicate read-only tool call skipped","cached":cache[key]}` **[DIVERGENCE: note string]** |
| Handler call | `result = handler(b.input) if handler else {"error":f"unknown tool {b.name}"}` | `result = await handler(b.input) if handler else {...}` **[async]** |
| Record toolResults | `tool_results.append({"tool":b.name,"result":result})` | `tool_results.append({"tool":b.name,"callId":b.id,"input":b.input,"result":result})` **[DIVERGENCE: extra callId+input]** |
| trajectory | `trajectory.append({"tool":b.name,"input":b.input})` | identical |
| Tool result msg | `{"type":"tool_result","tool_use_id":b.id,"content": wrap_untrusted(json.dumps(result, ensure_ascii=False))}` | identical (import aliased `_wrap_untrusted`) |
| Append user turn | `messages.append({"role":"user","content":results})` | identical |
| Budget exit | return `{"text":"Investigation stopped at the step budget without a conclusion.", ..., "stoppedReason":"budget"}` | identical |

### Where new "hooks" would insert (same two positions in both twins)
1. **Pre-tool-execution hook** — at the top of the per-tool loop body. agent.py already has the
   `on_tool(name, input)` seam here; loop.py has none. A new pre-hook (e.g. per-call policy check,
   rate limit, audit) slots in right after `if type != "tool_use": continue`, BEFORE the dedup
   cache lookup (mirror the `on_tool` placement).
2. **Post-tool-execution hook** — immediately after `result = handler(...)` and before
   `wrap_untrusted(...)`. This is the natural seam for result transforms (redaction, gate tagging,
   truncation). The `wrap_untrusted` call itself is the untrusted-telemetry spotlighting seam and
   must remain the last transform on tool-result content in both twins.
   Secondary seams: pre-model-call (before `messages.create`) and the budget-nudge injection point.

**Rule for Phase 2/3:** any change to the loop body must be written twice — once sync, once async —
keeping the 3 intentional divergences intact (toolResults shape, cache-hit note, on_tool/print).
No test currently *enforces* structural equivalence, so drift is silent; the invariant is
convention + the docstrings in both files.

---

## 5. `agent_server/system_prompt.py`  (37.6 KB, 457 lines) — canonical single-sourced prompt

### 5.1 Purpose
The investigator system prompt + untrusted-telemetry spotlighting. Canonical home per ADR-001
(2026-07-29): the chat app owns the prompt, replacing the deleted
`fabric_audit_agent/agent/system_prompt.py` (the old dual-copy drift, gap C2-REOPENED). Kept
static / prompt-cache-friendly.

### 5.2 Public surface
```python
def build_system_prompt():   # -> the module-level _SYSTEM string (no args, no interpolation)
def wrap_untrusted(text):    # -> "[UNTRUSTED TELEMETRY ...]\n```\n{text}\n```"
```
`_SYSTEM` is one big triple-quoted string constant (lines 11–447). `build_system_prompt()` returns
it verbatim — no templating, so a new prompt section is added by editing the string in place.

### 5.3 Section structure of `_SYSTEM` (so later additions slot in cleanly)
In order, each is a header line followed by `-` bullets:
1. Role sentence + **Hard rules** (read-only, ground-every-claim, abstain, honesty, targeted calls,
   tool-results-are-data).
2. **TRUE CU% vs THE MONITORED PROXY** — the foundational distinction.
3. **Error semantics (Fabric-specific)**.
4. **Timestamps** (quote `*Display` verbatim).
5. **Hypothesis discipline** (validated/likely/inconclusive labels).
6. **Final review — before answering**.
7. **Presentation & Voice** (lean default; includes SP6 inline-provenance, SP7 query-transparency).
8. **Investigation Mode (DEFAULT posture)** — the funnel; includes SP3 cadence-vs-causation, N24
   proxy caveat, multi-lens spike rule.
9. **Capacity-peaks — THE CANONICAL FLOW** (STEP 1–3; SP1 overage auto-pull; SP4/SP5 two-column %;
   pctBaseLifetime / pctBaseConverted definitions; capacity-overloads).
10. **Recommendations are ON-REQUEST**.
11. **Conversation continuity**.
12. **Default answer shape**.
13. **Prior findings context (when injected)**.
14. **Recurrence surfacing** (recurringRuns / firstSeenAt tiers).
15. **Monthly baseline comparison**.
16. **Chart usage (render_chart)** — sourceScope/isProxy rules.
17. **Investigation quality** (the mandatory four: cause/recurring/healthy?/what-to-do).
18. **Flagging to the notification center** (`create_notification_ticket` — the one write).
19. **Systemic (cross-workspace) patterns**.
20. **Cross-signal correlation**.
21. **Ticket memory** (alert deep-link = standing ticket).
22. **Failure & blind-spot visibility**.
23. **Structural query analysis**.
24. **Response discipline** (depth-proportional; three tiers; one proxy caveat per response;
    pre-send trim).

A new instruction block should be appended as its own headed section (mirroring the above) so it
does not disturb prompt-cache prefixes above it — append near the end (after §24) unless it must
gate earlier reasoning.

### 5.4 Who imports it
Module import path: `from agent_server.system_prompt import ...` (or relative `.system_prompt`).
- `agent_server/agent.py` — `from .system_prompt import build_system_prompt, wrap_untrusted as _wrap_untrusted`.
- `agent_server/loop.py` — `from .system_prompt import wrap_untrusted`.
- `agent_server/investigator.py` — `from .system_prompt import build_system_prompt`.
No tests import it directly (behavior is exercised via the loops). `wrap_untrusted`'s exact
`[UNTRUSTED TELEMETRY ...]` wrapper string is depended on by both tool loops as the spotlighting
seam.

---

## 6. `fabric_audit_agent/query/kql_guard.py`  (4.9 KB, 144 lines) — read-only KQL gate

### 6.1 Purpose
KQL construction guards for **KQL we build ourselves** (the trusted seam) — adapted from
microsoft/fabric-rti-mcp + microsoft/mcp + Azure-MCP (MIT), pure stdlib. Handles standard
single/double-quoted literals with backslash escaping; KQL `@"verbatim"` strings are explicitly NOT
modeled (acceptable because arbitrary agent-authored KQL goes through the P4 firewall instead).

### 6.2 Public signatures (VERBATIM — callers must not change these)
```python
def escape_string(value):
def escape_entity(name):
def first_statement(text):
def assert_read_only_kql(kql):
def assert_kusto_host(cluster_uri):
```
Plus one cross-module helper (underscore-named but imported elsewhere — treat as public):
```python
def _strip_string_literals(text):
```
Module constants that are semver-load-bearing: `_MAX_KQL_LENGTH = 10_000`, `_KUSTO_HOST_SUFFIXES`
(5-tuple), `_CONTROL_COMMANDS` (11-tuple), `_TAUTOLOGY_RE`.

### 6.3 Return / raise contracts callers depend on
- `escape_string(value)` → `str` (doubles `\`, escapes `"`, strips NUL). Never raises.
- `escape_entity(name)` → `str` of form `['...']`; **raises `ValueError`** on control chars
  (`\n \r \t \x00`).
- `first_statement(text)` → `str` (text up to first top-level `;`, rstripped). Never raises.
- `assert_read_only_kql(kql)` → returns `kql` unchanged if clean; **raises `ValueError`** on
  oversize / control command / boolean tautology.
- `assert_kusto_host(cluster_uri)` → returns normalized uri (trailing `/` stripped); **raises
  `ValueError`** on non-https scheme or host not ending in an allowlisted suffix (anti-SSRF, string
  suffix match not regex — ReDoS-safe).
- `_strip_string_literals(text)` → `str` same length with string-literal *contents* blanked to
  spaces (quote structure preserved).

### 6.4 Who imports / calls it
Import path `fabric_audit_agent.query.kql_guard` (relative `..query.kql_guard` /
`.kql_guard`). Non-test production callers:
- **`query/firewall.py`** — `from .kql_guard import assert_read_only_kql, first_statement, _strip_string_literals`
  (the P4 firewall's delegated stages; see §7).
- **`fabric_audit_agent/tools.py`** — `assert_kusto_host` (as `_assert_kusto_host`, line 162),
  `escape_entity` (as `_escape_entity`, lines 1560/1572/1614/1628), `assert_read_only_kql`
  (imported as `_assert_read_only_kql` line 41 but **unused**).
- **`adapters/collector_capacity_events.py`** — `escape_entity, first_statement`.
- **`adapters/collector_log_analytics.py`** — `escape_string, first_statement`.
- **`adapters/collector_events_la.py`** — `escape_string, first_statement`.
- **`query/mine.py`** — `kql_guard._strip_string_literals` (lines 105, 243).
- Tests (signatures pinned): `tests/test_firewall.py`, `tests/test_collector_events_la.py`,
  `tests/test_capacity_events_collector.py`, `tests/test_dax_guard.py` (imports `_strip_string_literals`
  from the DAX twin, not this one — but the pattern mirrors).

`sql_guard.py` and `dax_guard.py` are sibling guards following the same pattern (each has its own
`_strip_string_literals`, `assert_read_only_*`, `escape_*`), consumed by `run_sql`/`run_dax`
handlers in `tools.py`. Out of scope for this task but noted since a Phase-2/3 signature change to
kql_guard would invite a parallel change there.

---

## 7. `fabric_audit_agent/query/firewall.py`  (10.9 KB, 163 lines) — P4 firewall for agent-authored KQL

### 7.1 Purpose
Read-only **ad-hoc KQL firewall** for AGENT-AUTHORED KQL — stricter than the trusted-seam
kql_guard. Static rejection pass that runs BEFORE the engine's own take-0 binder rehearsal.
Rejects (not truncates) top-level `;`; rejects verbatim strings (`@"..."`), triple-backtick blocks,
and `//` line comments outright (the "quote-parity desync" bypass class, documented at length in the
module docstring); and enforces a dangerous-operator deny-list closing cross-resource / external-read
escapes. Pure: no I/O, deterministic.

### 7.2 Public surface
```python
class FirewallRejection(Exception):
    def __init__(self, reason, stage):   # .reason (human str), .stage (machine tag)
def validate_adhoc_kql(kql):             # -> kql unchanged if clean; else raise FirewallRejection
```
`stage` ∈ `{length, verbatim-string, multiline-string, comment, multi-statement, control-command,
denied-operator}`. (`run_kql_handler` adds `rehearsal`/`execute`/`engine-unconfigured` stages of its
own around this.)

### 7.3 How it calls the guard (the delegation Phase 3 must preserve)
```python
from .kql_guard import assert_read_only_kql, first_statement, _strip_string_literals
```
`validate_adhoc_kql` stages run in order, first failure wins:
1. length (`_MAX_ADHOC_LEN = 10_000`)
2. verbatim-string (`_VERBATIM_MARKER` on RAW text)
3. multiline-string (`_BACKTICK` on RAW text)
4. comment (`_LINE_COMMENT` on RAW text)
5. multi-statement — `if first_statement(s) != s.rstrip(): reject` ← **kql_guard.first_statement**
6. control-command — `assert_read_only_kql(s)` wrapped; `ValueError` → `FirewallRejection(..., "control-command")` ← **kql_guard.assert_read_only_kql**
7. denied-operator — `_strip_string_literals(s)` then `_DENIED_CALL` / `_DENIED_WORD` regex ← **kql_guard._strip_string_literals**

Stages 2–4 run on RAW text *before* any state-machine stage precisely so the quote-parity machines
in kql_guard only ever receive input they model correctly. Any Phase-3 change to `first_statement`,
`assert_read_only_kql`, or `_strip_string_literals` signatures/semantics **directly changes firewall
behavior** — this is the tightest coupling in the six files.

### 7.4 Who imports / calls it
Import path `fabric_audit_agent.query.firewall`.
- **`fabric_audit_agent/tools.py`** — `run_kql_handler` (lazy import at line 2077): calls
  `validate_adhoc_kql(kql)` in a `try/except FirewallRejection` (lines 2101–2105), mapping
  `rej.reason`/`rej.stage` into the error result.
- **`query/mine.py`** — `from .firewall import validate_adhoc_kql, FirewallRejection` (drops a mined
  template if its representative query raises).
- Tests: `tests/test_firewall.py` (the exhaustive stage matrix — depends on `.stage` values and
  `validate_adhoc_kql(kql) == kql` on pass), `fabric-audit-mcp/tests/test_entrypoints.py`,
  `fabric-audit-mcp/tests/test_mcp_tools.py` (firewall-chokepoint tests, e.g. line 2674).

---

## Cross-file dependency chain (Phases 2/3 at a glance)

```
system_prompt.py ──(build_system_prompt, wrap_untrusted)──> agent.py (async, prod)
        └───────────(build_system_prompt, wrap_untrusted)──> loop.py (sync, eval) <── investigator.py <── eval_score.py
tools.py (create_tool_definitions)
        ├──> fabric-audit-mcp/mcp_server.py ──(over MCP/OAuth)──> agent.py prod tool source
        ├──> fabric-audit-mcp/data_agent.py (manifest, handler stripped)
        └──> investigator.py (eval, direct import)
kql_guard.py ──(assert_read_only_kql, first_statement, _strip_string_literals)──> firewall.py ──> tools.run_kql_handler
        └──(assert_kusto_host, escape_entity, escape_string, first_statement)──> tools.py + 3 collector adapters
```

Key invariants a later phase must not break:
- Handler signature `handler(_input=None) -> dict`; tool dict keys `name/description/input_schema/handler`.
- The two tool loops stay structurally synced (3 intentional divergences preserved).
- `wrap_untrusted` remains the last transform on tool-result content in both loops.
- kql_guard's 6 public/quasi-public signatures unchanged (firewall + 3 adapters + tools depend on them).
- `FirewallRejection.stage` tag vocabulary unchanged (tools.py + tests read it).

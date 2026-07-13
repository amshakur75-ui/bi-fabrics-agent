# Fabric Audit Agent — Deploy Status

## Live Services

| Service | URL | Status |
|---------|-----|--------|
| **Agent App** (Phase 2) | `https://fabric-audit-agent-7405609570261849.9.azure.databricksapps.com` | RUNNING — verified end-to-end 2026-07-01 |
| **MCP App** (data tools) | `https://mcp-bi-fabrics-auditor-7405609570261849.9.azure.databricksapps.com/mcp` | RUNNING — **18 tools + query firewall live (v1.8.1, 2026-07-08)** |
| **Claude endpoint** | `databricks-claude-opus-4-7` | READY |

## Phase 4 / 3-B — Firewall + 18 tools deployed (2026-07-08)

The MCP app was on pre-firewall `bddbdb8` (16 tools, no `run_kql`/`query_library`); the earlier
"8 tools" note below was stale. Redeployed `main` → prod now serves **18 tools with the query
firewall**. Verified live via MCP JSON-RPC `tools/list` + `tools/call` with a CLI OAuth token:
`run_kql` rejects a denied `union database(...)` at `rejectionStage: denied-operator` (gate active,
engine never hit); `query_library` returns 21 grounded templates.

Deploy mechanic used (Repo id `1681080764058843`, branch `main`):
`databricks repos update 1681080764058843 --branch main --dangerously-force-discard-all` →
`databricks apps deploy mcp-bi-fabrics-auditor --source-code-path <repo>/fabric-audit-agent-py --mode SNAPSHOT`.

**Two deploy bugs caught + fixed during this activation (both would silently ship stale/empty state):**
1. **No version bump on the firewall merge** — `requirements.txt` hash unchanged ⇒ Databricks skips
   the pip reinstall ⇒ stale pre-firewall code keeps serving. Fixed by bumping `pyproject` version +
   the `# code version:` marker in lockstep (`1.7.0`→`1.8.0`). Remember this on EVERY code change.
2. **`query_library.json` not packaged** — `pyproject` had no `package-data`, so setuptools excluded
   the file from the wheel ⇒ `_load_query_library` found nothing ⇒ `query_library` returned 0
   templates in prod (local tests passed against the source tree). Fixed with
   `[tool.setuptools.package-data] fabric_audit_agent = ["query_library.json"]` + bump `→1.8.1`.
   Verified the built wheel now contains `fabric_audit_agent/query_library.json`.

**Deferred (still owed):** durable `FABRIC_HISTORY_PATH` (App still on ephemeral `/tmp`; the durable
App-reads / Job-writes split is its own follow-up) · **B0 secret rotation** (the exposed
`FABRIC_CLIENT_SECRET`, user's Azure action).

## Verification (2026-07-01)

Tested directly against `/invocations` (bypassing the browser UI) using an OAuth token from
`databricks auth login` + `databricks auth token` via the Databricks CLI — a PAT does **not**
authenticate against `*.databricksapps.com` App URLs, but a CLI-issued OAuth token does.

Both gate questions returned grounded, tool-backed answers with `run_audit` confirmed in
`trajectory`/`toolResults`:
- *"Who is driving capacity on Enterprise A4A - SVT?"* → capacity `1faee871-…` at 177.6% CU
  peak (71.5 min throttled); item "Ent-Reporting-Sales" at ~48.5% CU concentration; verdict
  size-up. Cited an alternative hypothesis it ruled out (single dominant user).
- *"What caused the last capacity spike?"* → same audit data correlated into a spike narrative;
  explicitly stated what it could not confirm (interactive vs. scheduled-refresh trigger) rather
  than guessing.

Five bugs were fixed to reach this state (see commit history on `main` from `e485e0b` through
`ee7b8b7`):
1. **401 calling the MCP app** — a plain SP bearer token doesn't authenticate against another
   Databricks App's URL. Fixed by using `databricks_mcp.DatabricksMCPClient`, which performs the
   correct app-to-app OAuth negotiation via `DatabricksOAuthClientProvider`.
2. **`asyncio.run() cannot be called from a running event loop`** — `DatabricksMCPClient`'s sync
   `list_tools()`/`call_tool()` call `asyncio.run()` internally, which can't nest inside the
   already-running event loop our `@invoke()`/`@stream()` handlers run under. Fixed by using the
   async `alist_tools()`/`acall_tool()` variants throughout.
3. **401 calling the Claude serving endpoint** — `ws.config.token` is a PAT-only attribute; under
   the app's actual SP/OAuth auth it's empty, so the request silently sent
   `Authorization: Bearer None`. Fixed by using `ws.config.authenticate()`, which returns valid
   headers regardless of auth strategy.
4. **`ImportError` on `ResponsesAgent` from `mlflow.types.responses`** — that class lives in
   `mlflow.pyfunc`, not `mlflow.types.responses`, which only exports the standalone
   `create_text_output_item` function. Fixed by importing that function directly.
5. **400 Bad Request calling the Claude serving endpoint** — real Responses-API clients (the
   chat UI) send `content` as a list of blocks that mlflow parses into `ResponseInputTextParam`
   *objects*, not dicts. `_messages_from_request` only checked `isinstance(c, dict)`, silently
   dropping every block and sending Claude an empty message. Fixed by falling back to
   `getattr(c, "text", "")` for non-dict blocks.

## Phase 3 Part B — Live event tools deployed + verified (2026-07-02)

The 3 Phase-3 event tools now run on **live** Log Analytics data on the MCP app (were mock-only):

- **`spike_events`** → real event-level depth: per-event timestamps, users, `cuSeconds`, and actual
  `queryText` (DAX/XMLA) — not averages.
- **`user_spike_history`** → e.g. `edwin.gregary@…` → 208 spikes, per-event item/operation/kind.
- **`capacity_patterns`** → runs live (`source: "live"`); returns `[]` when no ≥4-user surge
  coincides with a ≥70% CU bucket (valid deterministic result, not an error).
- **`run_audit`** and the other 5 tools verified returning live data; `tools/list` shows all 8.

Verified by direct MCP JSON-RPC (`initialize` → `tools/list` → `tools/call`) with a CLI OAuth token.
The previously-unverified `OperationName`/`EventText` LA columns **work on first live call** — no
schema fix needed. All read-only (SELECT-only KQL).

### Deploy mechanics learned the hard way (documented so they don't recur)

1. **`requirements.txt` install cache.** Databricks Apps keys the pip install on this file's content
   hash and logs `Requirements have not changed. Skipping installation.` when unchanged. The app
   installs the local package via `.[mcp]` *by version*, so a `pyproject.toml` version bump alone
   never reinstalls — the app kept serving stale pre-Part-A code (only `run_audit` in `tools/list`).
   **Fix:** bump the `# code version:` marker in `requirements.txt` in lockstep with the pyproject
   version on every code change — this changes the hash and forces the reinstall.

2. **The MCP app is a git-backed Databricks Repo.** Deploy = `databricks repos update <id>
   --branch main --dangerously-force-discard-all` (discards workspace edits, pulls main — atomic,
   no destructive window) → then `apps deploy`. Do **not** hand-upload files into the Repo working
   tree; that dirties it and blocks future pulls.

3. **`app.yaml` is now fully `valueFrom` + pull-safe (the durable fix).** Previously the working
   `app.yaml` was **workspace-only** (inline tenant/client IDs + live-source config) and never in
   git, because the repo is **public**. Any `repos update` wiped it and broke the app. Fixed by
   promoting **all** credentials *and* infrastructure identifiers/endpoints to secret-backed **app
   resources**, so git's `app.yaml` is 100% `valueFrom` (no sensitive values) and complete — a plain
   pull now deploys cleanly.

   App resources on `mcp-bi-fabrics-auditor` (secret scope `fabric-audit`):

   | Resource | Secret key | Injected env |
   |----------|-----------|--------------|
   | `tenant_id` | `FABRIC_TENANT_ID` | `FABRIC_TENANT_ID` |
   | `client_id` | `FABRIC_CLIENT_ID` | `FABRIC_CLIENT_ID` |
   | `client_secret` | `FABRIC_CLIENT_SECRET` | `FABRIC_CLIENT_SECRET` |
   | `la_workspace_id` | `FABRIC_LA_WORKSPACE_ID` | `FABRIC_LA_WORKSPACE_ID` |
   | `capacity_events_cluster` | `FABRIC_CAPACITY_EVENTS_CLUSTER` | `FABRIC_CAPACITY_EVENTS_CLUSTER` |
   | `capacity_events_db` | `FABRIC_CAPACITY_EVENTS_DB` | `FABRIC_CAPACITY_EVENTS_DB` |
   | `kusto_cluster` | `FABRIC_KUSTO_CLUSTER` | `FABRIC_KUSTO_CLUSTER` |
   | `kusto_db` | `FABRIC_KUSTO_DB` | `FABRIC_KUSTO_DB` |

   (`FABRIC_CAPACITY_EVENTS_KQL` / `FABRIC_KUSTO_KQL` stay inline in `app.yaml` — query logic only,
   no secrets. The Databricks CLI *OAuth* profile is required to write secrets; the PAT lacks the
   `secrets` scope.)

### Incident + recovery note (transparency)

The Part B deploy briefly broke the MCP app: the first `repos update` pulled the incomplete git
`app.yaml` (`valueFrom: tenant_id/client_id` referencing resources that didn't exist yet) over the
working workspace-only version, so `run_audit` errored (`estate.json not found` — it had fallen back
to the un-packaged mock because no live source resolved). Recovered by restoring the working config,
then eliminated the root cause with the app-resource fix above. Running apps were never at data risk
(read-only; the estate was never written).

## Known Issues — Phase 3 Backlog

1. **No capacity-name filter on `run_audit`.** `run_audit`'s `input_schema` takes no parameters,
   so it always audits whichever capacity the collector is configured against — it cannot target
   a capacity named in the question (e.g. "Enterprise A4A - SVT"). In a single-capacity estate
   this is invisible; in a multi-capacity estate it means the agent can only ever answer about
   one capacity, honestly abstaining on any other name rather than fabricating data (confirmed
   2026-07-01 — the abstain behavior itself worked correctly). **Real product gap, directly
   relevant to Phase 3**: `run_audit` needs a capacity-id/name parameter, `list_workspaces` (or a
   new tool) needs to resolve a human name to that id, and the collector/pipeline need to accept
   a capacity selector instead of a single fixed target.

2. **Inlined loop drift risk (minor, not urgent).** `agent_server/agent.py` still inlines its own
   copy of the tool loop (`_run_tool_loop`) and system prompt rather than importing the tested
   `fabric_audit_agent.agent.loop`/`system_prompt` package. `tests/test_agent_server.py`'s
   `TestInlinedLoopParity` class guards against behavioral drift between the two copies, but that
   mitigates the risk rather than eliminating it — a change to the real `loop.py` still has to be
   manually mirrored into `agent_server/agent.py`, and nothing fails until the parity tests are
   updated by hand. Worth revisiting once the agent app's packaging story is settled (e.g.
   vendoring `fabric_audit_agent` properly or publishing it as an installable dependency).

## Architecture

```
User → fabric-audit-agent (Databricks App)
           │
           ├── reasoning → databricks-claude-opus-4-7  [OpenAI chat-completions format]
           │                                             [§B1-alt adapter active — see below]
           │
           └── data tools → mcp-bi-fabrics-auditor (Databricks App, /mcp)
                               ├── CapacityEvents Eventhouse  (live CU%)
                               ├── SemanticModelLogs Eventhouse (per-item attribution)
                               └── Log Analytics (tenant-wide per-user activity)
```

## Claude Endpoint Protocol — IMPORTANT

`databricks-claude-opus-4-7` returns **OpenAI chat-completions format**, NOT Anthropic Messages:
- Response shape: `choices[0].message.content`, `choices[0].finish_reason`
- NOT: `content[0].text`, `stop_reason`

A `§B1-alt` adapter in `fabric-audit-agent-app/agent_server/agent.py` (`_build_claude_client`)
translates the request (Anthropic → OpenAI) and response (OpenAI → Anthropic blocks + stop_reason)
so the tool loop sees standard Anthropic format. Confirmed by B1 smoke test 2026-07-01.

If the endpoint is ever replaced with one that speaks native Anthropic Messages, remove the adapter
and replace `_build_claude_client` with a standard `anthropic.Anthropic(...)` client.

## Service Principals

| App | SP Name | SP Client ID |
|-----|---------|-------------|
| `fabric-audit-agent` | `app-1g3673 fabric-audit-agent` | `4bbc5413-2627-4be0-a93c-4a0af36f0dd3` |
| `mcp-bi-fabrics-auditor` | (earlier deploy, same workspace) | — |

The Entra SP used by the MCP server for data collection:
- Client ID: stored in `app.yaml` as `FABRIC_CLIENT_ID` (workspace-only, never committed)
- Tenant: stored in `app.yaml` as `FABRIC_TENANT_ID` (workspace-only, never committed)
- Secret: stored in Databricks secret scope `fabric-audit` key `FABRIC_CLIENT_SECRET`
- **ACTION REQUIRED**: rotate this secret — it was exposed in a prior chat session.

## User Authorization (OBO) — Pending Admin Action

The agent currently runs as its **service principal** (not the requesting user's identity).
To enable per-user identity (OBO):

1. A **workspace admin** must enable: Workspace Settings → Feature Preview → **User authorization for Databricks Apps**
2. After enabling, `get_user_workspace_client()` in `agent_server/agent.py` returns the user's downscoped token
3. The agent then inherits the user's read grants — fully read-only, auditable per-user

Until OBO is enabled, all requests run as the service principal. Data access is still read-only.

## Source Code Locations

| Component | Workspace Path | Repo Path |
|-----------|---------------|-----------|
| MCP server | `…/fabric-audit-agent-py/` | `fabric-audit-agent-py/` |
| Agent app | `…/fabric-audit-agent-app/` | `fabric-audit-agent-app/` |
| Agent app source (`agent.py`) | `…/fabric-audit-agent-app/agent_server/agent.py` | `fabric-audit-agent-app/agent_server/agent.py` |

**Note on inlined code:** `agent_server/agent.py` inlines the tool loop (`loop.py`) and system prompt
(`system_prompt.py`) from `fabric_audit_agent/agent/` to keep the agent app self-contained.
Tests in `fabric-audit-agent-app/tests/test_agent_server.py` cover the adapter and MCP client.
The inlined copies are currently identical to the originals — any changes to `loop.py` or
`system_prompt.py` must be reflected manually in `agent_server/agent.py`.

## Outstanding Admin Asks

1. **URGENT — Rotate Entra SP client secret** (exposed in prior chat session):
   - Azure Portal → App Registrations → find the SP by name (Client ID is in `app.yaml` / workspace secrets) → Certificates & secrets
   - Generate new secret → update Databricks secret scope: `databricks secrets put --scope fabric-audit --key FABRIC_CLIENT_SECRET`
   - Redeploy the MCP app after updating

2. **Enable User Authorization**: Databricks workspace admin → Settings → Feature Preview → User authorization for Databricks Apps

3. **Database shortcut / KQL permissions**: To enable cross-workspace SemanticModelLogs queries via Eventhouse shortcut, grant Contributor on the source KQL database to the service principal.

## MCP App Change Log (this deploy cycle)

**2026-07-01**: Activated `FABRIC_LA_WORKSPACE_ID` in MCP app's `app.yaml` (previously commented out).
- Before: `#- name: FABRIC_LA_WORKSPACE_ID` (commented)
- After: `- name: FABRIC_LA_WORKSPACE_ID / value: "45844fb8-0574-46ca-a4d9-a266b72847da"` (active)
- Effect: adds Log Analytics as a per-user attribution source for `run_audit` / `list_workspaces`
- Impact: additive and fault-tolerant — if LA auth fails, that collector is skipped with a warning
- Read-only: Log Analytics queries are read-only

## Deploy Log

**2026-07-08**: MCP app `mcp-bi-fabrics-auditor` redeployed to `main` (Phase 4 / firewall, v1.8.0) — repo
`repos update 1681080764058843 --branch main` → `apps deploy`. Verified live: `tools/list` = **18 tools**
incl. `run_kql` + `query_library`; firewall confirmed (a denied `union database(...)` rejected at
`denied-operator`). (Known: `query_library` returned 0 templates until v1.8.1 added `package-data` so
`query_library.json` ships in the wheel.)

**2026-07-09**: Agent app `fabric-audit-agent` redeployed to `main` (Phase 5, agent v0.1.2) — deployment
`01f17bca…` SUCCEEDED, app RUNNING. Ships: 5.1 reconciled honesty prompt + humanized progress (no tool
names to users), 5.4a `[conversation]` capture line. **Verified live** via `/invocations` (CLI OAuth
token): HTTP 200, concise senior-analyst answer with **no tool-name leak**; the `[conversation]` audit
line emitted in app logs with the expected shape (`tag/ts/question/toolsCalled/toolCount/abstainedHint/
answerChars`). Real conversations now accumulate (unblocks the deferred 5.4b eval miner).
- **Not yet deployed (Job side):** 5.2 egress chokepoint (gates delivery) + 5.3 `[identity]` line both
  live in `job.py`/`pipeline.py` → they ship with the next **scheduled-Job / bundle** deploy (the egress
  gate is inert until the Job delivers to a broadcast sink, which needs `TEAMS_WEBHOOK_URL`). The MCP app
  does not consume these, so no MCP redeploy was needed for Phase 5.
- **Standing:** `FABRIC_CLIENT_SECRET` rotation (B0) is still a pending user Azure action.

**2026-07-13**: Agent-response quality fixes deployed after a live behavior test flagged an off-looking answer.
Root-caused (via [conversation] logs + the raw run_audit envelope over /invocations) to: (1) the agent blending
per-item vs per-capacity user figures into one contradictory sentence, and (2) the informational top-users
finding (`capacity.user-ranking`) leaking developer placeholders ("Pattern not yet in the knowledge base",
"add a playbook entry"). Data itself confirmed **live and real** (Log Analytics + Capacity Events Eventhouse;
real @newellco.com users; drift across runs is the live "today" window accumulating). Fixes: prompt now
enforces scope separation + a lean/visual default answer (status headline + a few bullets; deep evidence held
until the user asks); added a real KB entry + impact line for `capacity.user-ranking`. **MCP app → v1.8.2**
(carries the KB/reasoner fix), **agent app → v0.1.3** (carries the prompt). Both deployments SUCCEEDED and
**verified live**: the reply is now a scannable ⚠️/✅ headline + bullets, scopes are not blended, and the
top-users finding carries real "no action required" content. Package 1100 tests / agent-app 73 green; prompt
parity held.

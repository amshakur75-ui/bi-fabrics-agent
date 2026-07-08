# MCP server + agent on Databricks

Three connected planes, all in Databricks:

```
Sweep Job   ── scheduled audit → writes report to a Volume                [push]      (job.py / databricks.yml)
MCP server  ── exposes run_audit at https://<app-url>/mcp                  [tool/pull] (mcp_server.py, hosted as a Databricks App)
Mosaic AI   ── your Databricks Claude + the MCP tool → chat "audit it"     [chat]      (AI Playground / Agent Framework)
```

The MCP tool runs the **real** audit when `FABRIC_CSV_PATHS` / live env is set (else an offline mock,
so it always responds). Read-only throughout.

## Tools

The MCP server exposes **16 read-only tools** (`fabric_audit_agent/tools.py::create_tool_definitions`),
not just `run_audit`:

| Purpose | Tools |
|---|---|
| Audit / verdict | `run_audit`, `list_workspaces` |
| User attribution | `user_activity`, `investigate_user`, `investigate_capacity_spike`, `user_spike_history` |
| Event depth + time windows | `spike_events`, `raw_events`, `capacity_patterns` |
| Grounding (schema/sample before querying) | `describe_source`, `sample_events` |
| Capacity diagnostics | `capacity_diagnostics` |
| Deduction | `diagnose`, `analyze_dax` |
| Memory | `whats_changed` |
| Per-user | `user_timeline` |

`user_spike_history`/`spike_events`/`capacity_patterns`/`raw_events` accept `hours` (e.g. "last 6
hours") or `start`+`end` (absolute ISO-8601 window) in addition to `days`. `spike_events`/`raw_events`
support `format:"columnar"` for token-cheaper large pulls. A result envelope carries `queryKql`
(the exact query run) so an answer can quote it rather than paraphrase, and Kusto-backed results
(`describe_source`/`sample_events`/`capacity_diagnostics`) also carry `verifyUrl(s)` — a
click-to-rerun-in-Fabric deeplink.

**Tiered coverage.** Event-backed tools (`spike_events`, `raw_events`, `capacity_patterns`,
`user_spike_history`, `diagnose`, `user_timeline`) run in one of two tiers depending on what's
configured: **Tier-2** (Log Analytics wired — the only source the event tools currently consume;
Workspace Monitoring feeds the aggregate audit's item attribution, not this seam, until wired)
returns real per-query events — exact DAX/query text and `cuSeconds`; **Tier-1** (only Activity Events /
`userAttribution` configured, no per-workspace depth) synthesizes operation-level events from the
tenant-wide audit log — no CU figure, coarser granularity. Every result carries a `tier` field and,
on Tier-1, a `coverageNote` explaining the gap, so an answer can honestly state what it can and
can't see rather than presenting synthesized data as if it were metered cost.

**`user_timeline` deployment note:** this tool reads **admin audit-log data** — per-person
day-tracking is an **org-policy question for the deployer, not a technical gate**. The tool itself
enforces no additional access control beyond the existing read-only Fabric/Entra credentials; whether
and how per-user timelines are exposed to end users (vs. admins only) is a deployment decision.

## 1. Host the MCP server as a Databricks App
The server is the console entry **`fabric-audit-mcp`** (`mcp_server:main`), served over HTTP at
**`/mcp`** on **port 8000** when `MCP_TRANSPORT=streamable-http`. The repo root has the App config:
- **`app.yaml`** — command + env (transport/port; optionally `FABRIC_CSV_PATHS`, `DATABRICKS_CLAUDE_ENDPOINT`).
- **`requirements.txt`** — installs `.[mcp]` + `openai` + `databricks-sdk`.

Deploy (per the [custom-MCP docs](https://learn.microsoft.com/azure/databricks/generative-ai/mcp/custom-mcp)):
- **UI:** Compute → Apps → Create app → point at this folder → Deploy.
- **CLI:** `databricks apps deploy fabric-audit-mcp --source-code-path .`

After deploy, the tool endpoint is **`https://<app-url>/mcp`**.

## 2. Connect a Mosaic AI agent
In the **AI Playground** (or the Agent Framework), per the
[agent-tool docs](https://learn.microsoft.com/azure/databricks/generative-ai/agent-framework/agent-tool):
1. Pick your **Databricks Claude** model as the LLM.
2. **Add tools → MCP server →** the App's `https://<app-url>/mcp`.
3. Chat: *"Audit the Fabric capacity"* → the agent calls `run_audit` → returns the verdict + findings.

The agent is the conversational surface; the MCP App is the tool; the Job is the scheduled sweep.
All three call the same read-only pipeline.

## Read-only posture
`run_audit` only reads telemetry and writes its own report/findings. No estate is modified, by any
plane. The Databricks App + agent inherit that — the tool has no write path.

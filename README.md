# bi-fabrics-audit-agent

A read-only Microsoft Fabric and Power BI capacity audit agent. Detects throttling,
oversized semantic models, and refresh contention. Produces an optimize-vs-size-up
verdict through deterministic STOP gates. Monitors autonomously every 5 minutes and
every hour. Delivers alerts via Teams (Phase 10).

> **Read-only posture is absolute.** It reads telemetry/metadata and *advises*. Its only
> outward actions are writing its own findings and sending notifications — it never edits,
> refreshes, scales, or deletes anything in the estate.

---

## Architecture

Two components, one monorepo:

### fabric-audit-agent-app/ — The Agent (complete, self-contained)

Deployed on Databricks as the **fabric-audit-agent** App. Contains everything:

- **Agent brain** (`agent_server/`) — system prompt, tool-calling loop, NL-to-query
  skill, chart rendering
- **Core logic** (`fabric_audit_agent/`) — investigation pipeline, detectors, autonomous
  sweep jobs, memory (Delta tables), grounding schema, **the read-only tool definitions
  (`tools.py` / `create_tool_definitions`)**, and all analysis
- **Chat UI** (`e2e-chatbot-app-next/`) — React frontend users interact with

The two Databricks Jobs also deploy from here (`databricks.yml`):
- `fabric_audit_sweep` — full LLM-reasoned sweep, every hour
- `fabric_audit_tier2` — deterministic gate check (no LLM), every 5 minutes

### fabric-audit-mcp/ — The MCP Tool Server (thin satellite)

Deployed on Databricks as the **mcp-bi-fabrics-auditor** App. Contains only:
- `mcp_server.py` — the MCP protocol server (FastMCP), serving the tools at `/mcp`
- `data_agent.py` — the Fabric Data Agent / MCP manifest builder

It installs `fabric-audit-agent` as a wheel dependency and imports
`create_tool_definitions` from `fabric_audit_agent.tools`. All business logic — including
the tool handlers themselves — lives in the agent app; the MCP server is a protocol
adapter on top of it.

---

## How they connect

The agent app calls the MCP server over authenticated HTTP using `DatabricksMCPClient`.
The MCP server handles tool calls by importing `create_tool_definitions` from the
`fabric_audit_agent` package (installed from the agent app's wheel) and returning
structured results. The agent brain (`investigator.py`) imports the same
`create_tool_definitions` directly for its offline eval harness — so the tool logic has a
single home in core, imported by both the brain and the MCP server.

```
User
  ↓
fabric-audit-agent (chat app, Databricks App)
  ↓  MCP HTTP + OAuth
mcp-bi-fabrics-auditor (tool server, Databricks App)
  ↓  imports create_tool_definitions from
fabric_audit_agent package (installed from the agent app wheel)
  ↓  reads from (read-only)
Microsoft Fabric — Real-Time Hub, Workspace Monitoring, REST APIs, Azure ARM
```

The dependency flows one way only: **MCP → fabric_audit_agent**. The agent app never
imports from the MCP package.

---

## Data sources (all read-only)

| Source | What it provides | True vs proxy |
|---|---|---|
| Real-Time Hub Capacity Overview Events | Capacity CU% per 30-second window | **True CU** — validated formula |
| Workspace Monitoring Eventhouse | Per-user engine CPU time | Proxy (CpuTimeMs) |
| Log Analytics PowerBIDatasetsWorkspace | Same per-user data, different path | Proxy (CpuTimeMs) |
| Fabric REST API | Workspace, item, capacity metadata | Reference |
| Power BI Activity Events | Per-user operation counts | Reference |
| Azure ARM List Usages | Capacity SKU and state | Reference |
| Capacity Metrics CSV / .vpax export | Offline fallback (no permissions needed) | True CU |

---

## Memory — Delta tables in Unity Catalog

Four tables in `shakur-main.bi-fabrics-audit`, fully isolated from all other workspace data:

| Table | Purpose | Write pattern |
|---|---|---|
| `run_history` | One row per sweep — heartbeat and observability | Append |
| `audit_findings` | Findings per sweep — fed back as prior context (Phase 6) | Append |
| `capacity_reporting` | Capacity-level metrics by date | Upsert |
| `concentration_alerts` | Each concentration alert fired | Append |

90-day retention, no partitioning (liquid clustering).

---

## Deployment

```bash
# Agent app (app + both Databricks jobs). Builds the fabric-audit-agent wheel.
cd fabric-audit-agent-app
python -m build
databricks bundle deploy -t dev
databricks apps deploy

# MCP server (app only, no jobs). Installs the fabric-audit-agent wheel via requirements.
cd fabric-audit-mcp
databricks apps deploy
```

---

## Development

```bash
# Install the core logic locally (needed for MCP-server development and its tests)
cd fabric-audit-agent-app && pip install -e .

# Agent app tests (core logic, brain, moved tool tests)
cd fabric-audit-agent-app && pytest tests/ -q

# MCP protocol tests (import fabric_audit_agent via the installed wheel)
cd fabric-audit-mcp && pytest tests/ -q
```

---

## Key architecture decisions

See `fabric-audit-agent-app/docs/decisions/` for the full ADR trail:

- **ADR-001** — System prompt and tool loop moved to the agent app from the MCP package
- **ADR-002** — Core business logic (pipeline, detectors, automation, memory, **and the
  tool definitions**) moved to the agent app; the MCP package became a thin protocol
  satellite — `mcp_server.py` + `data_agent.py` only. `tools.py` stays in core because it
  is shared business logic imported by both the MCP server and the agent brain; keeping it
  in core preserves the one-way `MCP → fabric_audit_agent` dependency.

---

## What the autonomous monitoring does

**Tier 2 (every 5 min, no AI):** pulls live Capacity Events, runs four deterministic
gate checks (throttle, concentration, pressure, overage). Zero LLM cost on the common
case. Fires immediately if any threshold is crossed.

**Tier 1 (every hour, full sweep):** pulls from all configured sources, runs the full
investigation pipeline (detectors → gates → verdict → narrative), compares to previous
run, alerts only on material change.

Both jobs are defined in `fabric-audit-agent-app/databricks.yml`.

---

## Entra identity (Phase 10)

Currently uses a service principal. Phase 10 will migrate to Entra Agent ID — Microsoft's
purpose-built identity type for AI agent workloads. Requires M365 E5 + Microsoft Agent
365 add-on licensing at the tenant level. All delivery (Teams alerts) is wired in Phase 10.

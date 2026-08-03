# ADR-002: Restructure — Core Logic Moves to Agent App; MCP Becomes a Thin Satellite

## Status
Accepted — 2026-07-30

## Supersedes
ADR-001 (which fixed system-prompt ownership; this fixes the deeper logic ownership)

---

## Context

The original build placed all business logic (`pipeline.py`, `job.py`, `automation/`,
`detectors/`, `investigation/`, `adapters/`, `kb/`, `query/`) inside
`fabric-audit-agent-py/` alongside the MCP server (`mcp_server.py`, `tools.py`).
This happened because the investigation logic was built first, then an MCP layer was
added on top of it in the same package.

The result: `fabric-audit-agent-py/fabric_audit_agent/` does two completely different
jobs — it is simultaneously a business logic library AND an MCP protocol server.

ADR-001 already moved the agent brain (system prompt, tool loop) into the chat app.
But the deeper logic (pipeline, detectors, automation, memory) remained in the MCP
package, making the "MCP server" the true brain of the system in practice — not just
a tool provider.

This is structurally backwards. The agent app should own everything that makes the
agent what it is. The MCP server should be a thin satellite that exposes tools and
nothing else.

Direct evidence: `tools.py` imports from 20+ internal modules across `investigation/`,
`adapters/`, `query/`, `pipeline.py` — confirming the business logic is inseparable
from the MCP server in the current structure.

---

## Decision

**The core business logic moves into the agent app. The MCP server becomes a thin satellite.**

### Target structure

```
bi-fabrics-agent/                    monorepo (unchanged)
│
├── fabric-audit-agent-app/          THE AGENT — complete, self-contained
│   ├── fabric_audit_agent/          core package (moved FROM MCP repo)
│   │   ├── pipeline.py, job.py, config.py, finding.py, verdict.py
│   │   ├── health_score.py, narrative.py, anomaly.py, forecast.py
│   │   ├── correlate.py, coaching.py, accountability.py, audience.py
│   │   ├── egress.py, outbound.py, validate.py, context_findings.py
│   │   ├── identity.py, sources.py, timefmt.py, key_utils.py
│   │   ├── dax.py, staleness.py, report_md.py, reasoner_stub.py
│   │   ├── automation/   detectors/   investigation/
│   │   ├── adapters/     kb/          query/         importers/
│   │   └── eval/
│   ├── agent_server/                agent brain (UNCHANGED)
│   ├── e2e-chatbot-app-next/        React UI (UNCHANGED)
│   ├── tests/                       ALL tests (moved from MCP repo)
│   ├── scripts/                     moved from MCP repo
│   ├── docs/                        moved from MCP repo (including ADRs)
│   ├── databricks.yml               app + BOTH Databricks jobs (jobs move here)
│   ├── pyproject.toml               builds fabric-audit-agent wheel
│   └── app.yaml
│
└── fabric-audit-mcp/                MCP SERVER ONLY — thin satellite
    ├── fabric_audit_mcp/
    │   ├── __init__.py
    │   ├── mcp_server.py            moved from fabric_audit_agent/
    │   └── tools.py                 moved, imports updated to absolute paths
    ├── tests/                       MCP protocol tests only
    ├── databricks.yml               MCP app only — NO jobs
    ├── pyproject.toml               depends on fabric-audit-agent wheel
    └── app.yaml
```

### How the MCP server accesses the business logic

The agent app's `pyproject.toml` builds a `fabric-audit-agent` wheel that includes
`fabric_audit_agent/` as an installable Python package. The MCP server's
`pyproject.toml` declares `fabric-audit-agent` as a dependency.

`tools.py` import paths change from relative to absolute:

```python
# BEFORE (relative — breaks once tools.py moves to a different package):
from .pipeline import run_audit
from .investigation.diagnose import run_diagnosis

# AFTER (absolute — works because fabric_audit_agent is an installed wheel):
from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.investigation.diagnose import run_diagnosis
```

Every `from .` in `tools.py` becomes `from fabric_audit_agent.` — no logic changes,
only import prefixes.

### What moves where

| File/Folder | From | To |
|---|---|---|
| `fabric_audit_agent/` (entire core) | `fabric-audit-agent-py/` | `fabric-audit-agent-app/` |
| `mcp_server.py` | `fabric_audit_agent/` | `fabric-audit-mcp/fabric_audit_mcp/` |
| `tools.py` | `fabric_audit_agent/` | `fabric-audit-mcp/fabric_audit_mcp/` |
| `tests/` (1,500+ tests) | `fabric-audit-agent-py/` | `fabric-audit-agent-app/` |
| Databricks Jobs config | `fabric-audit-agent-py/databricks.yml` | `fabric-audit-agent-app/databricks.yml` |
| `scripts/` | `fabric-audit-agent-py/` | `fabric-audit-agent-app/` |
| `docs/` | `fabric-audit-agent-py/` | `fabric-audit-agent-app/` |

### What is renamed

| Old | New | Notes |
|---|---|---|
| `fabric-audit-agent-py/` folder | `fabric-audit-mcp/` | Repo folder rename |
| `fabric_audit_agent` package | `fabric_audit_agent` | Unchanged — minimises import disruption |

---

## Consequences

- The agent app is fully self-contained — brain, investigation logic, autonomous jobs,
  and memory all live together
- The MCP server is genuinely thin — `mcp_server.py` + `tools.py` + tests only
- Databricks Jobs move to the agent app's `databricks.yml` where they belong
- All 1,500+ tests live in the agent app repo
- Any future MCP-compatible client installs the `fabric-audit-agent` wheel and gets
  the full logic — the MCP server is just the protocol adapter on top
- `tools.py` import paths change from relative to absolute; no logic changes anywhere

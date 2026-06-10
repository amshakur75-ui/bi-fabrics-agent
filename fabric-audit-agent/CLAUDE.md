# Fabric Audit Agent

## What It Is

Read-only Microsoft Fabric / Power BI capacity & performance auditor. Detects issues,
explains root cause, prioritizes fixes for the Power BI team, coaches report authors, and
gives an evidence-backed capacity verdict. It never edits, modifies, or actuates anything —
the only outward action is delivering findings.

**Read-only posture is absolute.** No writes, no refreshes, no scale actions.

## Entry Points

| Command | File | What it does |
|---|---|---|
| `npm run audit` | `audit.js` | Full pipeline with mock adapters → `runs/latest.json` + `runs/report.md` |
| `npm run eval` | `eval.js` | Evaluates reasoner quality against golden fixtures |
| `npm run whatif` | `whatif.js` | What-if capacity scenario modelling |
| `npm run triggers` | `triggers.js` | Trigger condition explorer |
| `npm run lifecycle` | `lifecycle.js` | Finding lifecycle / suppression inspection |
| `npm run dax` | `dax.js` | DAX pattern analysis |
| `npm test` | `**/*.test.js` | Full test suite (no env required) |

## Architecture

**Functional core** (`core/`) + **swappable ports** (`adapters/`).

The core is pure — it takes injected ports and returns data. All I/O is dependency-injected
so the same logic runs offline (mock adapters) or in production (real adapters). Nothing in
`core/` knows about HTTP, files, or external services.

**Mock adapters** (ship with this package, work offline):
- `adapters/collector.mock.js` — reads `fixtures/estate.json`
- `adapters/reasoner.stub.js` — deterministic stub (no API key needed)
- `adapters/delivery.file.js` — writes to `runs/`
- `adapters/store.local.js` — JSON file store
- `adapters/lifecycle.store.js` — JSON lifecycle state

**Swap at deployment** (implement the same port interface):
- `adapters/collector.rest.js` — real Microsoft Fabric / Power BI Admin REST collector
- `adapters/reasoner.claude.js` — Claude reasoner (requires `ANTHROPIC_API_KEY`)
- `adapters/delivery.teams.js` — Teams card push via incoming webhook
- `adapters/ticketing.js` — Jira / Azure DevOps / ServiceNow ticket creation

## Conversational Deployment

The data-agent manifest (`core/data-agent.js`) exposes the auditor as a `run_audit` tool
callable from Copilot Studio, MCP hosts, or Copilot in Power BI / M365 Copilot. Wire it
into the host at deployment — see `DEPLOYMENT.md`.

`mcp.config.json` holds the MCP server configuration for the pull surface.

## Self-Contained

No dependencies beyond Node built-ins and `@anthropic-ai/sdk` (used only by
`adapters/reasoner.claude.js`, which is opt-in at deployment). Nothing here references any
external build system. Requires Node >= 20.

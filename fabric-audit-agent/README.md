# fabric-audit-agent

Read-only Microsoft Fabric / Power BI capacity & performance audit agent. Sweeps the estate,
detects issues (capacity throttling, oversized semantic models, slow reports, failing pipelines),
explains root cause, prioritizes fixes, coaches report authors, and gives an evidence-backed
verdict on whether to optimize further or size up the capacity.

**Self-contained — no dependencies beyond Node built-ins and `@anthropic-ai/sdk`. Nothing here
references any external build system.**

Requires Node >= 20.

---

## Quick Start

```bash
npm install          # installs @anthropic-ai/sdk (only needed for the Claude reasoner)
npm test             # run the full test suite — no API key required
npm run audit        # run a full audit with mock data → runs/latest.json + runs/report.md
```

---

## Entry Points

| Script | File | Description |
|---|---|---|
| `npm run audit` | `audit.js` | Full audit pipeline — mock collectors in, findings + report out |
| `npm run eval` | `eval.js` | Evaluates reasoner output quality against golden fixtures |
| `npm run whatif` | `whatif.js` | What-if capacity scenario modelling (e.g. "what if we halve CU?") |
| `npm run triggers` | `triggers.js` | Explore which trigger conditions fire on the current estate |
| `npm run lifecycle` | `lifecycle.js` | Inspect finding lifecycle states and suppression logic |
| `npm run dax` | `dax.js` | DAX pattern analysis across semantic models |

---

## Architecture

```
core/           Pure functional logic — detectors, pipeline, verdict, roadmap, etc.
adapters/       Swappable I/O ports — mock (ship) vs real (deploy)
fixtures/       Static test data (estate.json, golden/)
runs/           Output directory — latest.json, history.json, report.md
```

**Functional core in `core/`** — all logic is pure functions. No HTTP, no file I/O, no
external calls. Takes injected port objects, returns data.

**Swappable ports in `adapters/`** — mock adapters ship with the package and work fully
offline. At deployment, swap in real implementations:

- **Collector** — `adapters/collector.rest.js` → real Microsoft Fabric / Power BI Admin REST API
- **Reasoner** — `adapters/reasoner.claude.js` → Claude (requires `ANTHROPIC_API_KEY`)
- **Delivery** — `adapters/delivery.teams.js` → Microsoft Teams incoming webhook
- **Ticketing** — `adapters/ticketing.js` → Jira / Azure DevOps / ServiceNow
- **Store** — swap `adapters/store.local.js` for a database-backed store in production

Each adapter implements a simple port interface (see the mock for the contract). The core
never changes.

---

## Conversational Deployment

`core/data-agent.js` generates the MCP/Fabric Data Agent manifest so the auditor is callable
conversationally from Copilot Studio, Copilot in Power BI, M365 Copilot, or any MCP host.
`mcp.config.json` holds the server configuration for the pull surface.

See `DEPLOYMENT.md` for permissions, environment variables, Entra app registration, and
step-by-step wiring instructions.

---

## Running with the Real Claude Reasoner

Set `FABRIC_AUDIT_REASONER=claude` and provide `ANTHROPIC_API_KEY`, then run:

```bash
FABRIC_AUDIT_REASONER=claude ANTHROPIC_API_KEY=sk-ant-... npm run audit
```

All other entry points (`eval`, `whatif`, `triggers`, `lifecycle`, `dax`) use the stub
reasoner by default and require no API key.

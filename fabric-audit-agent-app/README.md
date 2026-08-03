# fabric-audit-agent (Python)

All-Python rebuild of the read-only Microsoft Fabric / Power BI capacity & performance
audit agent, targeting **Databricks** (a Python-wheel Job for the scheduled sweep + a Python
MCP server for the conversational pull surface).

It was built as a **test-guided port** of the original Node implementation (kept at
`../fabric-audit-agent/` as the reference spec). Every Node module has a Python counterpart
(only `core/zip.js` is dropped — replaced by the stdlib `zipfile` in `importers/vpax.py`),
and behaviour was pinned by `pytest` + adversarial parity-review passes. **No Node remains in
the runtime path.**

> **Posture: read-only is absolute.** The agent reads telemetry/metadata and *advises*. Its
> only outward actions are writing its own findings to its own store and sending
> notifications. It never edits, refreshes, scales, or deletes anything in the estate.

## What it does
Detects capacity throttling, oversized models, refresh contention, DirectQuery/visual
bloat, blast-radius, security/access and cost/unused issues; explains root cause; prioritises
fixes for the Power BI team; coaches report authors; and gives an evidence-backed capacity
verdict (**optimize vs. size-up**). It also runs the **30% concentration alert**: when one
item/workload consumes ≥30% of capacity CU it names the **User → Item → Owner** driving it and
can converse two-way in Teams.

## CLI (offline, mock adapters — 100% local, no network/key)
```
python -m fabric_audit_agent <command>      # or: python run.py <command>
```
| Command | What it does |
|---|---|
| `audit` | full pipeline → `runs/latest.json` + `runs/report.md` + console summary |
| `eval` | score the reasoner against the golden suite |
| `whatif <kind> <sizeGB> <at>` | capacity what-if (e.g. `whatif model 5 06:00`) |
| `triggers` | evaluate immediate trigger conditions |
| `lifecycle <action> <key> [...]` | set a finding's lifecycle state (`snoozed` needs an ISO date) |
| `dax "<measure>"` | DAX anti-pattern analysis |
| `import <file> [...]` | import Capacity-Metrics CSV / `.vpax` exports + diagnose |
| `inspect <file.csv>` | safe per-column stats (no sensitive values) |
| `mytest` | re-diagnose the gitignored `my-estate.json` |

## Architecture
**Functional core** (`fabric_audit_agent/`, pure) + **swappable ports** (`fabric_audit_agent/adapters/`).
All I/O is dependency-injected as dict-style ports — `{"collect": fn}` / `{"reason": fn}` /
`{"deliver": fn}` — so the same core runs offline (mock adapters) or in production (real
adapters). `pipeline.run_audit(...)` is the orchestrator.

- **Offline adapters** (zero deps): `collector_mock`, `reasoner_stub`, `delivery_file`,
  `store_local`, `lifecycle_store`.
- **Production adapters** (inject an HTTP/LLM client; real SDKs at deploy): `collector_rest`
  (Fabric/Power BI Admin REST), `collector_activity` (Activity Events / Log Analytics user
  attribution), `reasoner_claude` (Anthropic / Databricks-hosted Claude, KB fallback on
  error), `delivery_teams`, `ticketing`. Concrete client builders live in `adapters/clients.py`
  (`EntraHttp`, `build_entra_token_provider`, `build_anthropic_client`, `PlainJsonHttp`).
- **Surfaces:** `job.py` (Databricks wheel-task production sweep), `mcp_server.py` (MCP pull
  surface exposing `run_audit`), `tools.py` / `data_agent.py` (tool manifest), `conversation.py`
  (Teams two-way: concentration alerts + inbound Q&A).

## Install & test
```
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]     # Windows  (Linux/Databricks: .venv/bin/python)
.venv/Scripts/python -m pytest -q                 # core suite — no network or API key needed
```
Optional extras: `.[prod]` (`requests`, `msal`, `anthropic`) for the live adapters; `.[mcp]`
(`mcp`) for the pull-surface server.

## Conventions
- **Data dict keys stay camelCase** (`peakCuPct`, `sharePct`, `topUsers`) to mirror the source
  JSON and Microsoft API shapes — JSON round-trips stay identical to the Node version.
- **Python identifiers are snake_case** (functions, params, locals).

## Deploy
See **[DEPLOYMENT.md](DEPLOYMENT.md)** for the Databricks wiring (wheel Job, secret scopes,
Entra service-principal identity, Unity Catalog/Delta store, the 30% feature, and the Teams
two-way bot endpoint) and the read-only permission matrix. Project guide for contributors:
**[CLAUDE.md](CLAUDE.md)**.

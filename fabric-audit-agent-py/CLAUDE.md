# Fabric Audit Agent (Python)

## What It Is

Read-only Microsoft Fabric / Power BI capacity & performance auditor — the **all-Python
rebuild** of the Node agent (`../fabric-audit-agent/`), targeting **Databricks**. Detects
issues, explains root cause, prioritizes fixes for the Power BI team, coaches report authors,
gives an evidence-backed capacity verdict, and runs the **30% concentration alert** (names the
**User → Item → Owner** driving a hot item, two-way in Teams).

**Read-only posture is absolute.** No writes, no refreshes, no scale actions. The only outward
actions are delivering findings and sending notifications.

## Entry Points

`python -m fabric_audit_agent <command>` (or `python run.py <command>`, or the `fabric-audit`
console script after `pip install -e .`):

| Command | What it does |
|---|---|
| `audit` | full pipeline (mock adapters) → `runs/latest.json` + `runs/report.md` |
| `eval` | score the reasoner against the golden suite |
| `whatif <kind> <sizeGB> <at>` | what-if capacity scenario modelling |
| `triggers` | trigger-condition explorer |
| `lifecycle <action> <key> [...]` | finding lifecycle / suppression |
| `dax "<measure>"` | DAX anti-pattern analysis |
| `import` / `inspect` / `mytest` | CSV/`.vpax` import + diagnosis (local) |

Production surfaces: `fabric_audit_agent/job.py` (`main` / `run_job` — the Databricks wheel-task
sweep) and `fabric_audit_agent/mcp_server.py` (`main` / `build_mcp_server` — the MCP pull
surface). `python -m pytest -q` runs the full suite (no env required).

## Architecture

**Functional core** (`fabric_audit_agent/`) + **swappable ports** (`fabric_audit_agent/adapters/`).

The core is pure — it takes injected ports and returns data. All I/O is dependency-injected as
**dict-style ports** (`{"collect": fn}` / `{"reason": fn}` / `{"deliver": fn}` / `{"history","append"}`
/ `{"load","save"}`), so the same logic runs offline (mock adapters) or in production (real
adapters). Nothing in the core knows about HTTP, files, or external services.
`pipeline.run_audit(collector, reasoner, delivery, store=, lifecycle_store=, ...)` orchestrates.

**Mock adapters** (zero deps, ship for offline + tests):
- `adapters/collector_mock.py` — reads `fixtures/estate.json`
- `adapters/reasoner_stub.py` — deterministic stub (no API key)
- `adapters/delivery_file.py` — writes `runs/`
- `adapters/store_local.py` / `adapters/lifecycle_store.py` — JSON file stores

**Production adapters** (inject an HTTP/LLM client; same port interface):
- `adapters/collector_rest.py` — Fabric / Power BI Admin REST collector
- `adapters/collector_activity.py` — Activity Events / Log Analytics **user attribution** (the
  WHO behind the 30% alert; enriches `facts["items"]` via `attribution.enrich_items`)
- `adapters/reasoner_claude.py` — Claude (Anthropic SDK / Databricks-hosted); **KB fallback on
  any API/parse error**; sanitizes (no names) before any call
- `adapters/delivery_teams.py` — Teams card push; `adapters/ticketing.py` — Jira/ADO/ServiceNow
- `adapters/clients.py` — concrete client builders: `EntraHttp` (SP bearer auth),
  `build_entra_token_provider` (MSAL client-credentials), `build_anthropic_client`, `PlainJsonHttp`

## Conversational / Pull Surface

`tools.py` (`create_tool_definitions`) exposes the auditor as a read-only `run_audit` tool;
`data_agent.py` (`build_data_agent_manifest`) produces the Fabric Data Agent / MCP manifest
(handler stripped, `readOnly: true`); `mcp_server.py` serves it. `conversation.py` handles the
Teams two-way surface (`build_concentration_alert`, `answer_question`). See `DEPLOYMENT.md`.

## Conventions (port fidelity)

- **Data dict keys stay camelCase** (`peakCuPct`, `sharePct`, `topUsers`) — mirrors source JSON
  + Microsoft API shapes, so JSON round-trips match the Node version byte-for-byte.
- **Python identifiers are snake_case.**
- **JS→Python traps that are deliberately handled:** nullish `??` → `x if x is not None else d`
  (NOT falsy `or`); `Math.round` half-up → `math.floor(x+0.5)`; `Number.isFinite` → reject
  bool/NaN/Inf; `JSON.stringify` Unicode-literal → `ensure_ascii=False`; compact JSON →
  `separators=(",",":")`; `String(undefined)` → `"undefined"` at the Teams-card boundary; JS
  `${number}` whole-float → drop `.0`. CLI entry reconfigures stdout to UTF-8 for Windows.

## Self-Contained

The core + offline adapters need only the **Python standard library** (`zipfile` replaces the
Node hand-rolled `zip.js` for `.vpax`). Production extras are opt-in: `.[prod]` (`requests`,
`msal`, `anthropic`), `.[mcp]` (`mcp`). Requires Python ≥ 3.10.

# Corporate

Private home for agents built to deploy inside the company's own infrastructure
(Microsoft Fabric / Power BI / Azure / Databricks). Each agent is built and tested
standalone — no external build-system dependencies.

## Agents

### `fabric-audit-agent-py/` — Fabric / Power BI capacity audit agent (Python · **primary**)

Read-only Microsoft **Fabric / Power BI capacity & performance audit agent** — the all-Python
build that deploys to **Databricks** (a Python-wheel Job for the scheduled sweep + an MCP server
for the conversational pull surface). Detects issues across 7 domains (capacity, semantic models,
reports, pipelines, lineage, security, cost), explains root cause, prioritizes fixes for the
Power BI team, coaches report authors, gives an evidence-backed **optimize-vs-size-up** capacity
verdict, and runs the **30% concentration alert** — naming the **User → Item → Owner** driving a
hot item, two-way in Teams.

> **Read-only posture is absolute.** It reads telemetry/metadata and *advises*. Its only outward
> actions are writing its own findings and sending notifications — it never edits, refreshes,
> scales, or deletes anything in the estate.

**Status:** code-complete and verified — **1187 tests pass** across the MCP tools package.
The companion chat app (`fabric-audit-agent-app/`) adds 114 tests. Both packages deploy as
Databricks Apps (MCP server + chat agent). Runs fully offline on mock adapters today; live
deployment needs environment wiring (Entra service-principal credentials, confirmed API endpoints,
Delta/UC store) per `DEPLOYMENT.md`.

**Quick start**
```
cd fabric-audit-agent-py
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]   # Windows  (Linux/Databricks: .venv/bin/python)
.venv/Scripts/python -m pytest -q               # 1187 tests, no env or API key required
.venv/Scripts/python run.py audit               # sample run on mock data
```

- Overview: `fabric-audit-agent-py/README.md`
- Permissions + deployment: `fabric-audit-agent-py/DEPLOYMENT.md`

**Validate on real data (local — nothing leaves the machine):**
```
cd fabric-audit-agent-py
python run.py inspect data.csv                 # safe column stats first (no sensitive values)
python run.py import "Capacity Metrics export.csv"   # also reads .vpax
python run.py import data.csv Items.csv        # merge the two Capacity Metrics exports
```
It auto-maps your columns (printing exactly which column fed which field), writes the gitignored
`my-estate.json`, then prints the diagnosis. Excel? **File → Save As → CSV** first. `my-estate.json`
is gitignored, so real company numbers are **never** pushed — only the blank
`my-estate.example.json` template is tracked. Tweak the JSON and re-run `python run.py mytest`.

### `fabric-audit-agent-app/` — Chat agent (Databricks App)

The conversational chat interface deployed as a Databricks App (`fabric-audit-agent`). Implements
the Responses Agent protocol, calls the MCP server's tools over HTTP/OAuth, and owns the system
prompt, investigation loop, and agent-case eval suite per ADR-001.

### Node reference (removed)

The original Node.js implementation (`fabric-audit-agent/`) was the porting reference and has
been deleted now that the Python build is complete and independently verified.

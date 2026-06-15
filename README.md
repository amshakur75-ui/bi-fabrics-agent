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

**Status:** code-complete and verified — **246 tests pass** (1 skipped: the optional `mcp` SDK),
behaviour pinned to the Node reference by adversarial parity review, audit output byte-identical
to Node. Runs fully offline on mock adapters today; live deployment needs environment wiring
(Entra service-principal credentials, confirmed API endpoints, Delta/UC store) per `DEPLOYMENT.md`.

**Quick start**
```
cd fabric-audit-agent-py
python -m venv .venv
.venv/Scripts/python -m pip install -e .[dev]   # Windows  (Linux/Databricks: .venv/bin/python)
.venv/Scripts/python -m pytest -q               # 246 tests, no env or API key required
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

### `fabric-audit-agent/` — Node reference (origin)

The original self-contained Node implementation (557 tests) that the Python build was ported
from. Kept as the reference spec / answer key — **not** the deployment target.
```
cd fabric-audit-agent
npm install && npm test     # 557 tests, no env required
npm run audit               # sample run on mock data
```
- Overview: `fabric-audit-agent/README.md` · Deployment notes: `fabric-audit-agent/DEPLOYMENT.md`

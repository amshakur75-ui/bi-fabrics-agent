# Restructure Plan — bi-fabrics-audit-agent
# Created: 2026-07-30
# See: fabric-audit-agent-app/docs/decisions/ADR-002-restructure-core-into-agent-app.md
# for the full architectural decision record.
#
# This file is the step-by-step execution guide for Claude Code.
# Execute every step in order. Do not skip steps. Do not start the restructure
# until this file is committed and pushed to GitHub.

---

## What this restructure does

Moves all business logic (pipeline, automation, detectors, investigation, adapters, kb,
query) from the MCP server repo (fabric-audit-agent-py) into the agent app
(fabric-audit-agent-app). The MCP server becomes a thin satellite containing only
mcp_server.py and tools.py. The agent app becomes fully self-contained.

See ADR-002 for the full reasoning.

---

## Pre-flight checks

- [ ] Pull latest from GitHub: `git pull origin main` in the monorepo root
- [ ] Confirm both test suites pass BEFORE touching anything:
      `cd fabric-audit-agent-py && pytest tests/ -q`
      `cd fabric-audit-agent-app && pytest tests/ -q`
- [ ] Confirm `git status` is clean (no uncommitted changes) in both repos
- [ ] Note current test count so you can verify it's preserved after the move

---

## Critical logic changes required alongside the file moves

These are not optional. Every one of these will cause a crash or silent failure
if not done as part of the restructure.

### C1: agent app pyproject.toml — switch build backend + include fabric_audit_agent

Current agent app uses `hatchling` and only packages `agent_server/`. After the move
it must also package `fabric_audit_agent/` and expose the job entry points.

Replace `fabric-audit-agent-app/pyproject.toml` with:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "fabric-audit-agent"
version = "0.2.15"
requires-python = ">=3.11"
description = "Read-only Fabric/Power BI audit agent — complete agent + core logic"
dependencies = [
    "fastapi>=0.129.0",
    "uvicorn>=0.41.0",
    "mlflow>=2.16",
    "databricks-agents>=1.9.3",
    "databricks-sdk>=0.28",
    "databricks-mcp>=0.1",
    "mcp>=1.13,<2",
    "requests>=2",
    "msal>=1.24",
    "azure-kusto-data>=4.0",
    "python-dotenv>=1.2.1",
]

[tool.setuptools.packages.find]
include = ["fabric_audit_agent*", "agent_server*"]

# CRITICAL: without this, query_library.json is excluded from the wheel and
# run_kql returns 0 templates in production (works locally, fails in prod).
[tool.setuptools.package-data]
fabric_audit_agent = ["query_library.json"]

[project.scripts]
fabric-audit-job   = "fabric_audit_agent.job:job_main"
fabric-audit-tier2 = "fabric_audit_agent.job:tier2_main"
start-app          = "scripts.start_app:main"
start-server       = "agent_server.start_server:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

### C2: MCP server pyproject.toml — depend on agent wheel, declare new package

Replace `fabric-audit-mcp/pyproject.toml` with:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "fabric-audit-mcp"
version = "1.9.17"
requires-python = ">=3.10"
description = "Thin MCP tool server — exposes fabric_audit_agent tools via MCP protocol"
dependencies = [
    "fabric-audit-agent>=0.2.15",   # installs fabric_audit_agent package
    "mcp>=1.2,<2",
    "requests>=2",
    "msal>=1.24",
    "azure-kusto-data>=4.0",
    "databricks-sdk>=0.28",
    "databricks-mcp>=0.1",
]

[tool.setuptools.packages.find]
include = ["fabric_audit_mcp*"]

[project.scripts]
fabric-audit-mcp = "fabric_audit_mcp.mcp_server:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

### C3: tools.py — all relative imports become absolute

Every `from .` in tools.py becomes `from fabric_audit_agent.`. Examples:
```python
# BEFORE:
from .pipeline import run_audit
from .investigation.diagnose import run_diagnosis
from .adapters.collector_capacity_events import capacity_series
from .query.kql_guard import assert_read_only_kql
from .kb import METRIC_DEFINITIONS, MetricValue

# AFTER:
from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.investigation.diagnose import run_diagnosis
from fabric_audit_agent.adapters.collector_capacity_events import capacity_series
from fabric_audit_agent.query.kql_guard import assert_read_only_kql
from fabric_audit_agent.kb import METRIC_DEFINITIONS, MetricValue
```
Do the same for mcp_server.py if it has any relative imports into fabric_audit_agent.
Verification: `grep -n "from \." fabric-audit-mcp/fabric_audit_mcp/tools.py` must
return zero hits.

### C4: databricks.yml — both jobs update their artifacts source

When the two job stanzas move to `fabric-audit-agent-app/databricks.yml`, the
`artifacts` block must be updated. The wheel now comes from the agent app build:
```yaml
artifacts:
  default:
    type: whl
    build: python -m build
    path: .    # this resolves to fabric-audit-agent-app/ after the move
```
The `package_name: fabric_audit_agent` in both job task specs stays the same.

### C5: FABRIC_BASE_CU in app.yaml — investigate before carrying over

The current MCP `app.yaml` has:
```yaml
- name: FABRIC_BASE_CU
  value: "1024"
```
The comment explains this exists because the capacity's SKU name resolves as a
non-standard string (FTL64) and the live read was failing. This is the suspected
cause of the GAP-1 / N26 SKU mismatch (if the real capacity is F512, this value
is doubling every percentage figure the agent produces).

Do NOT silently carry this value over. Before copying it into the new MCP app.yaml:
1. Run the GAP-1 KQL query to confirm the live `baseCapacityUnits` from the event stream
2. If the stream returns 512: change this value to "512" and document it
3. If the stream returns 1024: keep it as "1024" and close N26 Explanation A
4. Either way: add `_resolve_base_cu()` logic to prefer the live stream value and
   only fall back to this env var when the stream is unavailable, logging a warning
   when the fallback fires (silent fallback to a wrong value is exactly N26's bug)

### C6: mcp_server.py import path

After the move, mcp_server.py lives in `fabric_audit_mcp/` not `fabric_audit_agent/`.
If it does `from .tools import ...` that must become `from fabric_audit_mcp.tools import ...`.
If it does `from .adapters import ...` that must become `from fabric_audit_agent.adapters import ...`.
Read the file and update accordingly.

### C7: Test imports

Every test file that imports from `fabric_audit_agent` using absolute paths is fine —
that path is unchanged. Check for any tests using relative imports (`from ..xxx import`)
and convert them to absolute. Also confirm no test imports from `fabric_audit_mcp`
using a path that changes.

### C8: data_agent.py moves WITH mcp_server.py (it is MCP-only code)

`data_agent.py` lives in `fabric_audit_agent/` today but it only does one thing:
build the Fabric Data Agent manifest that `mcp_server.py` uses to register itself.
Nothing in the core pipeline uses it. It belongs in `fabric_audit_mcp/` alongside
`mcp_server.py`. Move it there and update `mcp_server.py`'s import:

```python
# BEFORE (in mcp_server.py, after it moves):
from fabric_audit_agent.data_agent import build_data_agent_manifest   # WRONG

# AFTER:
from fabric_audit_mcp.data_agent import build_data_agent_manifest     # CORRECT
```

Also update `mcp_server.py`'s import of tools:
```python
# AFTER move:
from fabric_audit_mcp.tools import create_tool_definitions
```

### C9: watch_run.py imports private functions from tools.py — MUST FIX or circular dependency

This is the most important logic fix in the entire restructure.

`watch_run.py` currently does:
```python
from .tools import _live_base_cu        # private helper
from .tools import _capacity_kusto_query  # private helper
```

After the restructure, `tools.py` is in `fabric_audit_mcp/` but `watch_run.py` is
in `fabric_audit_agent/` (agent app). If left unchanged, `watch_run.py` would need
to import from the MCP package — which depends on `fabric_audit_agent`. That's a
circular dependency that will crash on import.

**Fix:** Extract `_live_base_cu` and `_capacity_kusto_query` OUT of `tools.py` and
INTO `fabric_audit_agent/job.py` (where the other collector-building helpers already
live — `_default_collector`, `_build_tier2_collector`, `build_collector_from_env`, etc.).

Then:
- `watch_run.py` imports them from `fabric_audit_agent.job`:
  ```python
  from fabric_audit_agent.job import _live_base_cu, _capacity_kusto_query
  ```
- `tools.py` (now in MCP) also imports them from `fabric_audit_agent.job`:
  ```python
  from fabric_audit_agent.job import _live_base_cu as _live_base_cu
  from fabric_audit_agent.job import _capacity_kusto_query as _capacity_kusto_query
  ```

Verification: `grep -rn "from fabric_audit_mcp" fabric-audit-agent-app/` must return
zero hits — the agent app must never import from the MCP package.

### C10: watch_run.py Databricks entry point moves to agent app

Current `pyproject.toml` (MCP package) declares:
```toml
fabric-audit-watch = "fabric_audit_agent.watch_run:main"
```

After the move `watch_run.py` is in `fabric_audit_agent/` inside the agent app.
The script entry point in the agent app's `pyproject.toml` must include it:
```toml
[project.scripts]
fabric-audit-job   = "fabric_audit_agent.job:job_main"
fabric-audit-tier2 = "fabric_audit_agent.job:tier2_main"
fabric-audit-watch = "fabric_audit_agent.watch_run:main"
start-app          = "scripts.start_app:main"
start-server       = "agent_server.start_server:main"
```

### C11: __main__.py entry point

If `fabric_audit_agent/__main__.py` exists (for `python -m fabric_audit_agent`),
it moves with the package to the agent app and needs no import changes.
Confirm it works after the move by running `python -m fabric_audit_agent --help`
from the agent app directory.

---

## Step 1: Rename the MCP repo folder

```bash
cd C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent
git mv fabric-audit-agent-py fabric-audit-mcp
```

From this point: the old fabric-audit-agent-py/ is now fabric-audit-mcp/.

---

## Step 2: Move the entire core package to the agent app

```bash
# Move the core package
git mv fabric-audit-mcp/fabric_audit_agent fabric-audit-agent-app/fabric_audit_agent

# Move tests (all 1,500+ of them)
git mv fabric-audit-mcp/tests fabric-audit-agent-app/tests

# Move scripts
git mv fabric-audit-mcp/scripts fabric-audit-agent-app/scripts

# Move docs (ADRs, specs, fix plans)
# Note: fabric-audit-agent-app/docs already exists (we created it for ADR-002)
# Move contents, not the folder itself
git mv fabric-audit-mcp/docs/decisions/ADR-001* \
       fabric-audit-agent-app/docs/decisions/
git mv fabric-audit-mcp/docs/superpowers \
       fabric-audit-agent-app/docs/superpowers
# Any other docs subdirectories — move them across too

# Move GAPS-AND-ISSUES.md and task files
git mv fabric-audit-mcp/GAPS-AND-ISSUES.md fabric-audit-agent-app/GAPS-AND-ISSUES.md
git mv fabric-audit-mcp/UNVALIDATED-FORMULAS-AND-MEASURES.md \
       fabric-audit-agent-app/UNVALIDATED-FORMULAS-AND-MEASURES.md
git mv fabric-audit-mcp/MEASURE-CATALOG-RAW.md \
       fabric-audit-agent-app/MEASURE-CATALOG-RAW.md
git mv fabric-audit-mcp/tasks fabric-audit-agent-app/tasks
```

---

## Step 3: Create the thin MCP package structure

```bash
mkdir -p fabric-audit-mcp/fabric_audit_mcp
mkdir -p fabric-audit-mcp/tests
```

Create `fabric-audit-mcp/fabric_audit_mcp/__init__.py` (empty file).

```bash
# Move only mcp_server.py and tools.py into the new thin MCP package
git mv fabric-audit-agent-app/fabric_audit_agent/mcp_server.py \
       fabric-audit-mcp/fabric_audit_mcp/mcp_server.py
git mv fabric-audit-agent-app/fabric_audit_agent/tools.py \
       fabric-audit-mcp/fabric_audit_mcp/tools.py
```

After this step:
- `fabric-audit-agent-app/fabric_audit_agent/` has everything EXCEPT mcp_server.py and tools.py
- `fabric-audit-mcp/fabric_audit_mcp/` has ONLY mcp_server.py and tools.py

---

## Step 4: Update all import paths in tools.py

Every relative import in tools.py must become an absolute import.

**Find and replace pattern:**
```
from .              →  from fabric_audit_agent.
from . import       →  from fabric_audit_agent import
```

Example transformations:
```python
# BEFORE:
from .pipeline import run_audit
from .investigation.diagnose import run_diagnosis
from .adapters.collector_capacity_events import capacity_series
from .query.kql_guard import assert_read_only_kql
from .kb import METRIC_DEFINITIONS, MetricValue

# AFTER:
from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.investigation.diagnose import run_diagnosis
from fabric_audit_agent.adapters.collector_capacity_events import capacity_series
from fabric_audit_agent.query.kql_guard import assert_read_only_kql
from fabric_audit_agent.kb import METRIC_DEFINITIONS, MetricValue
```

Do the same for mcp_server.py if it has any relative imports into fabric_audit_agent.

**Verification:** `grep -n "from \." fabric-audit-mcp/fabric_audit_mcp/tools.py`
must return zero hits after this step.

---

## Step 5: Update pyproject.toml files

### fabric-audit-agent-app/pyproject.toml
Add package discovery so the wheel includes fabric_audit_agent:
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["fabric_audit_agent*", "agent_server*"]

[project]
name = "fabric-audit-agent"
version = "0.2.14"
# ... preserve all existing dependencies
```

### fabric-audit-mcp/pyproject.toml
Update to depend on the agent wheel and point to the new package:
```toml
[project]
name = "fabric-audit-mcp"
version = "1.9.15"
dependencies = [
    "fabric-audit-agent>=0.2.14",  # installs fabric_audit_agent package
    # ... preserve all other existing dependencies
]

[tool.setuptools.packages.find]
where = ["."]
include = ["fabric_audit_mcp*"]
```

---

## Step 6: Move Databricks Jobs to agent app databricks.yml

Open `fabric-audit-mcp/databricks.yml` and `fabric-audit-agent-app/databricks.yml`.

Move the `fabric_audit_sweep` and `fabric_audit_tier2` job stanzas from the MCP YAML
into the agent app YAML.

After:
- `fabric-audit-agent-app/databricks.yml` contains: the fabric-audit-agent App + both jobs
- `fabric-audit-mcp/databricks.yml` contains: the mcp-bi-fabrics-auditor App ONLY

Update the wheel task references in the job stanzas — they previously pointed to the
MCP package wheel. They now point to the agent app wheel (`fabric-audit-agent`).

---

## Step 7: Run both test suites — no logic changed, only paths

```bash
cd fabric-audit-agent-app
pytest tests/ -q
# Must pass: all 1,500+ tests

cd ../fabric-audit-mcp
pytest tests/ -q
# Must pass: MCP protocol tests
```

If any test fails, it is an import path issue. Fix the specific import — do not change
any logic. Common failure patterns:
- `ModuleNotFoundError: No module named 'fabric_audit_agent'` → wheel not installed;
  run `pip install -e ../fabric-audit-agent-app` in the MCP venv
- `ImportError: cannot import name 'X' from 'fabric_audit_mcp'` → a test is importing
  from the old path; update the test's import

---

## Step 8: Rewrite the top-level README.md

Replace `C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent\README.md` with:

```markdown
# bi-fabrics-audit-agent

A read-only Microsoft Fabric and Power BI capacity audit agent. Detects throttling,
oversized semantic models, and refresh contention. Produces an optimize-vs-size-up
verdict through deterministic STOP gates. Monitors autonomously every 5 minutes and
every hour. Delivers alerts via Teams (Phase 10).

---

## Architecture

Two components, one monorepo:

### fabric-audit-agent-app/ — The Agent (complete, self-contained)

Deployed on Databricks as the **fabric-audit-agent** App. Contains everything:

- **Agent brain** (`agent_server/`) — system prompt, tool-calling loop, NL-to-query
  skill, chart rendering
- **Core logic** (`fabric_audit_agent/`) — investigation pipeline, detectors, autonomous
  sweep jobs, memory (Delta tables), grounding schema, all analysis
- **Chat UI** (`e2e-chatbot-app-next/`) — React frontend users interact with

The two Databricks Jobs also deploy from here:
- `fabric_audit_sweep` — full LLM-reasoned sweep, every hour
- `fabric_audit_tier2` — deterministic gate check (no LLM), every 5 minutes

### fabric-audit-mcp/ — The MCP Tool Server (thin satellite)

Deployed on Databricks as the **mcp-bi-fabrics-auditor** App. Contains only:
- `tools.py` — 19+ read-only tool handlers
- `mcp_server.py` — MCP protocol server

Installs `fabric-audit-agent` as a wheel dependency. All business logic lives in the
agent app — the MCP server is a protocol adapter on top of it.

---

## How they connect

The agent app calls the MCP server over authenticated HTTP using `DatabricksMCPClient`.
The MCP server handles tool calls by importing from the `fabric_audit_agent` package
(installed from the agent app's wheel) and returning structured results.

```
User
  ↓
fabric-audit-agent (chat app, Databricks App)
  ↓  MCP HTTP + OAuth
mcp-bi-fabrics-auditor (tool server, Databricks App)
  ↓  imports fabric_audit_agent wheel
fabric_audit_agent package (installed from agent app)
  ↓  reads from (read-only)
Microsoft Fabric — Real-Time Hub, Workspace Monitoring, REST APIs, Azure ARM
```

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
# Agent app (app + both Databricks jobs)
cd fabric-audit-agent-app
python -m build
databricks bundle deploy -t dev
databricks apps deploy

# MCP server (app only, no jobs)
cd fabric-audit-mcp
databricks apps deploy
```

---

## Development

```bash
# Install core logic locally (needed for MCP server development)
cd fabric-audit-agent-app && pip install -e .

# Run all tests
cd fabric-audit-agent-app && pytest tests/ -q   # 1,500+ tests

# Run MCP protocol tests
cd fabric-audit-mcp && pytest tests/ -q
```

---

## Key architecture decisions

See `fabric-audit-agent-app/docs/decisions/` for the full ADR trail:

- **ADR-001** — System prompt and tool loop moved to agent app from MCP package
- **ADR-002** — Core business logic moved to agent app; MCP became a thin satellite

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
```

---

## Step 9: Update GAPS-AND-ISSUES.md header

Add to the top of the session notes in GAPS-AND-ISSUES.md:

```
**Restructure completed (2026-07-30):** fabric-audit-agent-py/ renamed to
fabric-audit-mcp/. fabric_audit_agent/ core package moved into fabric-audit-agent-app/.
Databricks Jobs config moved to agent app databricks.yml. ADR-002 written. All 1,500+
tests now live in the agent app repo. MCP server stripped to mcp_server.py + tools.py.
See ADR-002 for full detail.
```

Also update all file path references in GAPS-AND-ISSUES.md and tasks/ files that
previously pointed to `fabric-audit-agent-py/` — they now point to
`fabric-audit-agent-app/`.

---

## Step 10: Final commit and push

```bash
cd C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent
git add -A
git commit -m "refactor: restructure — core logic moves to agent app, MCP becomes thin satellite (ADR-002)"
git push origin main
```

---

## Acceptance criteria

- [ ] `fabric-audit-agent-py/` no longer exists
- [ ] `fabric-audit-mcp/` contains ONLY:
      `fabric_audit_mcp/` (3 files: __init__.py, mcp_server.py, tools.py),
      `tests/`, `databricks.yml`, `pyproject.toml`, `app.yaml`
- [ ] `fabric-audit-agent-app/fabric_audit_agent/` exists with all business logic
- [ ] `grep -rn "from \." fabric-audit-mcp/fabric_audit_mcp/tools.py`
      returns zero hits
- [ ] `cd fabric-audit-agent-app && pytest tests/ -q` — all tests pass
- [ ] `cd fabric-audit-mcp && pytest tests/ -q` — protocol tests pass
- [ ] Databricks Jobs in `fabric-audit-agent-app/databricks.yml` only
- [ ] `fabric-audit-mcp/databricks.yml` has no job stanzas
- [ ] ADR-002 at `fabric-audit-agent-app/docs/decisions/ADR-002-*.md`
- [ ] README.md at repo root reflects new architecture
- [ ] Both apps deploy successfully after restructure
- [ ] Push to GitHub with the commit message above

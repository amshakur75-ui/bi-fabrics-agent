# Phase 3 — Databricks deployment (scheduled read-only sweep)

**Prereqs:** Phase 2 passed in **`--auth sp`** (the service principal can read your pilot workspace —
see `PHASE2-SP-TEST.md`). This phase packages that same read into a scheduled Databricks Job that
posts findings to Teams. Still scoped to the pilot workspace(s); estate-wide is Phase 4.

> Read-only throughout: a GET-only collector + a Teams notification. No writes to the estate.

## Identity (recap from Phase 2)
One Entra SP — `FABRIC_TENANT_ID` / `FABRIC_CLIENT_ID` / `FABRIC_CLIENT_SECRET` — in
`sg-fabric-audit-readonly`, with "Service principals can use Power BI APIs" enabled for that group,
and **Viewer** on the pilot workspace(s).

## Step 1 — Secret scope (Databricks CLI)
```bash
databricks secrets create-scope fabric-audit
databricks secrets put-secret fabric-audit FABRIC_TENANT_ID
databricks secrets put-secret fabric-audit FABRIC_CLIENT_ID
databricks secrets put-secret fabric-audit FABRIC_CLIENT_SECRET
databricks secrets put-secret fabric-audit ANTHROPIC_API_KEY     # optional (omit -> KB-only reasoning)
databricks secrets put-secret fabric-audit TEAMS_WEBHOOK_URL     # optional (omit -> no Teams push)
```

## Step 2 — Build & upload the wheel
From `fabric-audit-agent-py/`:
```bash
python -m pip install build
python -m build        # -> dist/fabric_audit_agent-1.0.0-py3-none-any.whl
```
Upload the `.whl` to a Unity Catalog Volume (Databricks → Catalog → your volume → Upload), e.g.
`/Volumes/main/default/wheels/`.

## Step 3 — Launcher notebook
The CLI `audit` uses **mock** adapters; the real sweep is `fabric_audit_agent.job:main`, which reads
the env vars. A small notebook bridges secrets → env, then calls it:
```python
# Cell 1 — install the wheel + prod extras (requests, msal, anthropic)
%pip install "/Volumes/main/default/wheels/fabric_audit_agent-1.0.0-py3-none-any.whl[prod]"
dbutils.library.restartPython()
```
```python
# Cell 2 — secrets -> env, set endpoints, run one read-only sweep
import os
from fabric_audit_agent.job import main

for k in ["FABRIC_TENANT_ID","FABRIC_CLIENT_ID","FABRIC_CLIENT_SECRET","ANTHROPIC_API_KEY","TEAMS_WEBHOOK_URL"]:
    try: os.environ[k] = dbutils.secrets.get("fabric-audit", k)
    except Exception: pass

# Per-domain REST endpoints (confirm exact paths on Microsoft Learn; unset -> that domain is skipped).
# Scoped start uses groups/<workspaceId>/... ; estate-wide (Phase 4) uses the /admin/... endpoints.
os.environ["FABRIC_DATASETS_URL"] = "https://api.powerbi.com/v1.0/myorg/groups/<workspaceId>/datasets"
os.environ["FABRIC_REPORTS_URL"]  = "https://api.powerbi.com/v1.0/myorg/groups/<workspaceId>/reports"

main()   # prints the summary; posts the Teams card; appends run history
```

## Step 4 — Job + schedule
Databricks → **Workflows → Create job** → task type **Notebook** → select the launcher → compute =
serverless or a small single-node cluster → add a **schedule** (e.g., daily 06:00).
*(Wheel-task alternative: it needs a console entry point — add `fabric-audit-job =
"fabric_audit_agent.job:main"` under `[project.scripts]` in `pyproject.toml`, rebuild, then use a
Python-wheel task with entry point `fabric-audit-job`.)*

## Step 5 — Network egress
Allow the workspace outbound to: `login.microsoftonline.com`, `api.powerbi.com`,
`api.fabric.microsoft.com`, and `api.anthropic.com` (if using the key).

## Step 6 — First run + validate
**Run now**, then confirm: no auth errors, the **Teams card posted**, and the diagnoses **match your
Phase 1 CSV read** for that workspace. If they line up with a known incident, the live path is proven.
Then let the schedule take over and widen scope (Phase 4).

## Env vars `job.py` reads
| Var | Purpose |
|---|---|
| `FABRIC_TENANT_ID` / `FABRIC_CLIENT_ID` / `FABRIC_CLIENT_SECRET` | SP client-credentials |
| `FABRIC_CAPACITY_URL` / `_REFRESHES_URL` / `_DATASETS_URL` / `_REPORTS_URL` / `_PIPELINES_URL` / `_LINEAGE_URL` / `_ACCESS_URL` / `_USAGE_URL` | per-domain REST endpoints (unset → skipped) |
| `ANTHROPIC_API_KEY` | Claude reasoner (omit → KB-only) |
| `TEAMS_WEBHOOK_URL` | Teams push |
| `AUDIT_HISTORY_PATH` | run-history JSON path (swap for a Delta store) |
| `FABRIC_AUDIT_CONFIG` | optional JSON merged over detection thresholds |

## Known follow-ups
- **Delta/UC store:** `job.py` uses a local JSON history (`/tmp`, ephemeral). Implement the
  `{history, append}` port against a Delta table so run history persists across runs (enables
  trend/escalation/forecast across runs).
- **Capacity CU:** still via CSV import (metrics model not SPN-queryable) — keep a periodic manual
  import, or fold the CSV path into a notebook step.

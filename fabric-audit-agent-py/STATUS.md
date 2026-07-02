# STATUS — where this project is & what to do next

**Read this first if you're a new session or new to the repo.** It's the single source of truth for
*current state + the rollout plan*. The code guide is `CLAUDE.md`; the deploy reference is
`DEPLOYMENT.md`; per-phase step-by-step lives in the `PHASE*.md` runbooks.

## What this is
Read-only Microsoft Fabric / Power BI capacity & performance audit agent (all-Python, Databricks-
ready). Detects capacity/throttling/model/refresh/report/security/cost issues, explains root cause,
prioritises fixes, gives an **optimize-vs-size-up** verdict, and runs the **30% concentration alert**
(names **User → Item → Owner** driving a hot item, two-way in Teams). **Read-only is absolute** — it
reads + advises + notifies; it never edits/refreshes/scales/deletes.

## Current state
- **Build: complete & verified.** All-Python port of the Node reference (`../fabric-audit-agent/`).
  `python -m pytest -q` → **460 passed, 1 skipped** (skip = optional `mcp` SDK). Audit/eval output is
  byte-identical to Node; zero Node in the runtime path.
- **Rollout: Phase 1 done · Phase 2 built · Phase 3 (Databricks deploy) in progress** — the read-only
  agent App + MCP app are **deployed and verified end-to-end** on Databricks (see [`docs/DEPLOY-STATUS.md`](docs/DEPLOY-STATUS.md));
  the scheduled Job / Teams surface is still pending.
- **Two phase tracks — don't conflate.** The *Phase N* labels here are the **deployment-rollout**
  track (CSV → connectivity → Databricks → estate-wide). The **agent-capability** build phases
  (investigation core → agent brain → event depth → deeper permissions) are a separate track,
  tracked in `docs/superpowers/plans/` + `docs/DEPLOY-STATUS.md`.

## Rollout phases
| Phase | What | Status | How |
|---|---|---|---|
| **1 — local engine test** | run the engine on a real Capacity Metrics CSV export; no cloud, no SP | ✅ done (on `data.csv`) | `python run.py import data.csv [Items.csv]` / `inspect` |
| **2 — single-workspace connectivity test** | prove auth + one-workspace read, locally | ✅ built | `python -m fabric_audit_agent.connectivity <wsId> [--auth user\|sp]` → `PHASE2-SP-TEST.md` |
| **3 — Databricks deployment** | scheduled read-only sweep (secret scope, wheel, Job, Teams) | 🔄 in progress — App + MCP deployed & verified (`docs/DEPLOY-STATUS.md`); Job/Teams pending | `PHASE3-DATABRICKS.md` |
| **4 — widen + interactive** | estate-wide admin APIs, Log Analytics attribution, MCP/Copilot pull, Bot Service two-way, ITSM | ⬜ later | `DEPLOYMENT.md` §§2, 4 |

## Phase 1 — what we found (on `data.csv`, 3,119 timepoints)
- The importer's safeguard fired: `Total CU Usage %` carries **raw pre-smoothing spikes** (peak
  **23,070%**) → flagged + sanitized so it can't drive a bogus verdict.
- Trustworthy signal (computed `Total CU(s) ÷ 100% in CU(s)`): **median ~33%**, p90 85%, **p95 ~105%**
  (over limit), **187 / 3,119 timepoints > 100%**, **9 `Overloaded` state-changes**. Spikes are
  **~99% background-driven** (max background 7.07M CU-s vs interactive 42K).
- Baseline `100% in CU(s)` is **15,360 for most rows but 30,720 for 690** → a **resize/autoscale**
  mid-window (or two grains overlaid) — confirm.
- **Preliminary verdict:** *optimize the background refresh spikes (stagger the schedule) before
  sizing up* — typical load is low; the problem is intermittent background contention.
- **Open inputs to finalise:** (1) **`Items.csv`** (per-item CU table → names the optimize targets +
  the 30% user attribution) — not yet provided; (2) the **capacity SKU** (F-size).

## Key decisions & facts (so you don't re-derive them)
- **There is no "workspace-scoped service principal" in Power BI.** An SP is a *tenant* identity; you
  scope it by adding it as **Viewer to one workspace**. Letting SPs use the API at all is a tenant
  setting — scope it to a **1-SP security group**. The estate-wide grant is a *separate* setting
  ("Service principals can access read-only admin APIs"), deferred to Phase 4.
- **Capacity CU telemetry comes via CSV export** — the Capacity Metrics semantic model is **not**
  SPN-queryable. The REST collector covers surrounding metadata (workspaces/datasets/reports/
  refreshes); CU% stays CSV.
- **Phase 2 has two auth modes:** `--auth user` (your device-code login — no SP, no admin, validate
  now) and `--auth sp` (client-credentials — the identity the Phase-3 job uses).
- **Real data is NOT in git.** Only `my-estate.json` is gitignored (the importer writes it); the raw
  CSVs were never pushed and must not be committed.
- **A Managed Identity cannot call Power BI/Fabric APIs** — only an Entra SP (in the allowed group) can.

## Where things live
- Engine/core: `fabric_audit_agent/` (pure); orchestrator `pipeline.py:run_audit`.
- Ports/adapters: `fabric_audit_agent/adapters/` (mock + real); clients in `adapters/clients.py`
  (`EntraHttp`, `build_entra_token_provider`, `build_user_token_provider`, `build_anthropic_client`,
  `PlainJsonHttp`).
- Live entry points: `connectivity.py` (Phase 2 test), `job.py:main` (Phase 3 sweep),
  `mcp_server.py:main` (pull surface).
- CLI (offline/mock + CSV import): `run.py` / `python -m fabric_audit_agent`.
- Docs: `README.md`, `CLAUDE.md`, `DEPLOYMENT.md`, `PHASE2-SP-TEST.md`, `PHASE3-DATABRICKS.md`, this file.

## Immediate next actions
1. Run **Phase 2**: `--auth user` now to validate your workspace (no admin); `--auth sp` once an admin
   enables the tenant toggle + grants Viewer.
2. Get **`Items.csv`** + the **SKU** to finalise the Phase 1 verdict (named optimize targets).
3. When Phase 2 passes in `--auth sp`, follow **`PHASE3-DATABRICKS.md`**.

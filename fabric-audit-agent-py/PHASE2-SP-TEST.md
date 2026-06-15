# Phase 2 — Single-workspace connectivity test

**Goal:** prove you can authenticate **and** read **one** Power BI workspace over the REST API —
locally, read-only, **before** any Databricks wiring. This isolates the *identity/permissions*
question from the *deployment* question.

**You do NOT need Databricks, a secret scope, a wheel, or Teams for this phase.** The only network
call is a `GET` of the workspace's datasets — nothing is written or changed.

## Two ways to run it (`--auth`)
| Mode | Identity | Needs | Use it for |
|---|---|---|---|
| `--auth user` | **your own login** (device-code sign-in) | `FABRIC_TENANT_ID` + `FABRIC_CLIENT_ID` (a public-client app); **no secret, no SP API tenant setting**; you must be a member of the workspace | Validate your workspace **now**, zero admin |
| `--auth sp` *(default)* | **one service principal** (client-credentials) | `FABRIC_TENANT_ID` + `FABRIC_CLIENT_ID` + `FABRIC_CLIENT_SECRET`; the "SPs can use Power BI APIs" tenant setting for your group; SP as Viewer | Prove the **exact** identity the scheduled Databricks job (Phase 3) uses |

> A service principal is always a **tenant** identity — Power BI has no "workspace-scoped SP." You
> scope it by adding the one SP as **Viewer to one workspace**; `--auth user` sidesteps the SP
> entirely by signing in as you.

## Prereqs (both modes)
```bash
cd fabric-audit-agent-py
python -m pip install -e ".[prod]"      # requests + msal
```
Get your workspace **GUID** from its Power BI URL (`.../groups/<workspaceId>/...`).

---

## Option A — your own login (no admin)
**WHERE — Entra admin center (https://entra.microsoft.com → App registrations):** one app
registration; under **Authentication**, set **"Allow public client flows" = Yes**. Copy its
**Application (client) ID** + your **Directory (tenant) ID**. No secret, no API permissions, no
Power BI tenant setting.

```bash
export FABRIC_TENANT_ID=...  FABRIC_CLIENT_ID=...
python -m fabric_audit_agent.connectivity <workspaceId> --auth user
```
```powershell
$env:FABRIC_TENANT_ID="..."; $env:FABRIC_CLIENT_ID="..."
python -m fabric_audit_agent.connectivity <workspaceId> --auth user
```
It prints a device-code message ("open https://microsoft.com/devicelogin and enter CODE"); sign in
as yourself. It then reads the workspace you're a member of. No SP, no admin toggle.

---

## Option B — one service principal → one workspace
1. **Entra → App registrations → New** — name `Fabric-PowerBI-Audit-Agent`; copy client + tenant ID;
   **Certificates & secrets → New client secret** → copy the value.
2. **Entra → Groups → New group** — `sg-fabric-audit-readonly`; add the app as a member.
3. **Power BI Admin portal** (https://app.powerbi.com → Admin portal → Tenant settings → Developer
   settings) — enable **"Service principals can use Power BI APIs"** for that group; then open your
   one workspace → **Manage access** → add the app as **Viewer**.
```bash
export FABRIC_TENANT_ID=...  FABRIC_CLIENT_ID=...  FABRIC_CLIENT_SECRET=...
python -m fabric_audit_agent.connectivity <workspaceId>          # --auth sp is the default
```
```powershell
$env:FABRIC_TENANT_ID="..."; $env:FABRIC_CLIENT_ID="..."; $env:FABRIC_CLIENT_SECRET="..."
python -m fabric_audit_agent.connectivity <workspaceId>
```

---

## Reading the result (either mode)
| Output | Meaning | Fix |
|---|---|---|
| `[OK] token` + `[OK] workspace_read` | **PASS** | (sp mode) proceed to **Phase 3**, `DEPLOYMENT.md` |
| `[XX] token` | sign-in / credentials failed | user: complete the device-code prompt; sp: re-check client / tenant / secret |
| `[OK] token` + `[XX] workspace_read` (401/403) | authorized to call, not to see this workspace | user: confirm you're a member of the workspace; sp: enable the tenant setting for your group + add the SP as Viewer |

On **PASS in `--auth sp`**, the live identity for the scheduled job is proven end-to-end, and
Phase 3 is just packaging + scheduling the same read on Databricks.

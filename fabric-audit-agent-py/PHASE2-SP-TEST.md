# Phase 2 — Single-workspace service-principal test

**Goal:** prove the Entra service principal can authenticate **and** read **one** Power BI
workspace over the REST API — locally, read-only, **before** any Databricks wiring. This isolates
the *identity/permissions* question from the *deployment* question, so if something is wrong you
know exactly which.

**You do NOT need Databricks, a secret scope, a wheel, or Teams for this phase.** The only network
call is a `GET` of the workspace's datasets — nothing is written or changed.

---

## Prereqs
- Python 3.10+ with the prod extras. From `fabric-audit-agent-py/`:
  ```bash
  python -m pip install -e ".[prod]"      # installs requests + msal
  ```
- You (or an admin) can register an Entra app and change one Power BI tenant setting.

## Steps

### 1. Register the SP — Microsoft Entra admin center → https://entra.microsoft.com
*Identity → Applications → App registrations → New registration.* Name `Fabric-PowerBI-Audit-Agent`,
single tenant. Copy **Application (client) ID** + **Directory (tenant) ID**. Then *Certificates &
secrets → New client secret* → copy the **Value** (shown once). No Graph API permissions needed.

### 2. Security group — Entra → Groups → New group
Security group `sg-fabric-audit-readonly`; add the app as a **member**.

### 3. Authorize + scope — Power BI Admin portal → https://app.powerbi.com → Admin portal → Tenant settings
*(Needs Fabric / Power BI Admin.)*
- **Developer settings → "Service principals can use Power BI APIs"** → Enabled → **apply to
  `sg-fabric-audit-readonly`** (not the whole organization).
- Open your **one pilot workspace → Manage access → Add** the app as **Viewer**.

> This is the minimum and the maximum for this phase: the SP can read exactly one workspace.
> It does **not** grant tenant-wide data — that's the separate "read-only admin APIs" setting,
> which stays off until Phase 4.

### 4. Get the workspace ID
Open the workspace in Power BI; its URL contains `/groups/<workspaceId>/...`. Copy that GUID.

### 5. Run the test (local, read-only)
```bash
cd fabric-audit-agent-py
python -m pip install -e ".[prod]"
```
```bash
# bash / zsh / Codespace
export FABRIC_TENANT_ID=...  FABRIC_CLIENT_ID=...  FABRIC_CLIENT_SECRET=...
python -m fabric_audit_agent.connectivity <workspaceId>
```
```powershell
# PowerShell
$env:FABRIC_TENANT_ID="..."; $env:FABRIC_CLIENT_ID="..."; $env:FABRIC_CLIENT_SECRET="..."
python -m fabric_audit_agent.connectivity <workspaceId>
```

### 6. Read the result
| Output | Meaning | Fix |
|---|---|---|
| `[OK] token` + `[OK] workspace_read` | **PASS** — identity + tenant gate + workspace scope all work | Proceed to **Phase 3** (`DEPLOYMENT.md`) |
| `[XX] token` | SP client id / tenant id / secret is wrong (Entra). API never called. | Re-check Step 1 values |
| `[OK] token` + `[XX] workspace_read` (401/403) | Power BI authorization | Step 3: enable the tenant setting for your group, confirm the SP is in the group, and add it as Viewer on this workspace |

On **PASS**, the live identity is proven end-to-end and Phase 3 is just packaging + scheduling the
same read against Databricks.

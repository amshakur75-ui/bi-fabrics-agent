# Microsoft Permissions — Fabric Audit Agent

Microsoft-side permissions only (Entra, Power BI, Fabric, Azure, Graph).
Read-only throughout — the agent only reads and advises.

---

## Group 0 — Identity (required first)

The agent needs an **Entra App Registration (service principal)** so Fabric/Power BI
recognizes it as a caller. There is **no other way** — a Managed Identity *cannot* call the
Power BI/Fabric APIs; only an Entra app registration can.

- Entra App Registration — the agent's identity (how Fabric knows who is calling)
- Client Secret — the app's password, used to get an access token
- Security group membership — tenant settings grant access to groups, so the app must be in one
- Admin consent — activates the API scopes tenant-wide (without it they stay inactive)

---

## Group 1 — Capacity Monitoring (30% concentration, CU usage, capacities, spikes)

**Tenant settings (Admin portal)**
- Service principals can use Power BI APIs — master switch; without it every call is blocked (401)
- Service principals can use Fabric APIs — same switch for Fabric items (Lakehouses, pipelines, notebooks)

**API scopes (on the app)**
- Capacity.Read.All — read capacity state, SKU, and CU info
- Workspace.Read.All — list workspaces and what's in them
- Dataset.Read.All — read semantic models + refresh history

**Fabric features**
- Workspace Monitoring enabled — collects per-user query/CPU logs (the data behind the 30% "who")
- Viewer on the Monitoring Eventhouse — lets the agent query those monitoring logs
- Real-Time Hub Capacity Events → Eventhouse — live CU% + throttle spikes every 30 seconds

---

## Group 2 — Estate-Wide Reads (all workspaces)

**Tenant setting**
- SPs can access read-only admin APIs — see ALL workspaces at once + read Activity Events

**Workspace access**
- Viewer on each workspace — read that workspace's content (if not using admin APIs)

**API scopes**
- Tenant.Read.All — tenant-wide metadata across all workspaces
- Report.Read.All — read reports (detect orphaned/stale ones)
- Dashboard.Read.All — read dashboards
- Dataflow.Read.All — read dataflows + their refresh history
- Item.Read.All — read Fabric items (Lakehouses, notebooks, pipelines)

---

## Group 3 — User Names & Attribution

**Microsoft Graph scopes**
- User.ReadBasic.All — turn user IDs into real names/emails
- Group.Read.All — resolve group ownership of workspaces
- Team.ReadBasic.All — find the Teams channel to route alerts to

**Azure RBAC (for Log Analytics attribution)**
- Reader — look up Azure resources tied to a capacity
- Monitoring Reader — read Azure Monitor infra metrics (CPU/throttle)
- Log Analytics Reader — query the per-user CPU logs (cost-weighted attribution)

**Azure prerequisite**
- PBI diagnostics routed to Log Analytics — the pipe that puts Power BI logs where the agent can read them

---

## Group 4 — OneLake / Lakehouse Data

- Storage Blob Data Reader — read data stored in Fabric Lakehouses (OneLake)

---

## Group 5 — Notifications

- Teams Incoming Webhook — push alerts/digests into a Teams channel
- Azure Bot Service / Copilot Studio — two-way chat so users can ask the agent questions in Teams

---

## What the Levels Mean

- **Tenant setting** — org-wide on/off switch in the Admin portal; off by default; applies to a security group.
- **API scope** (e.g. Capacity.Read.All) — a specific permission declared on the app; needs admin consent.
- **Workspace role** (Viewer) — read-only access inside one workspace.
- **Azure RBAC role** (Reader, etc.) — access to Azure resources (subscriptions, Log Analytics, storage).
- **Client Secret** — the service principal's password.

---

## Who Grants Each Group

- **Group 0** — Global Admin (Entra)
- **Group 1–2** — Power BI / Fabric Admin (tenant settings, Workspace Monitoring) + Global Admin (API scopes)
- **Group 3–4** — Azure Admin (RBAC) + Global Admin (Graph scopes)
- **Group 5** — Teams Admin / you

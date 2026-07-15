# Fabric Audit Agent — Azure Roles, Integration & Validation Brief

**For:** Sowmya
**From:** Abdishakur
**Purpose:** Give a shared understanding of (1) what data the agent sees today, (2) what
additional Azure roles would close the validation gaps we saw when comparing the agent's
output to the Fabric Capacity Metrics report, and (3) how we'd verify parity once the
grants are in place.

---

## 1. What the agent sees today (baseline)

The agent is running read-only against three telemetry surfaces:

| Source | What it gives us | Known limits |
|---|---|---|
| **Fabric Admin REST API** | Capacities, workspaces, items, refresh schedules | Rate-limited; does not expose per-operation billing data |
| **Log Analytics** (via Fabric diagnostic settings) | Per-user query attribution, query text, durations | ~5-minute ingestion lag; drops low-volume users under some sampling conditions |
| **Capacity Events Eventhouse** (Kusto) | Per-event CU-seconds, throttle windows, user timelines | Same source as the Metrics app for events, but not for the *billed* CU column |

**What this means in practice:** the agent can find and explain what's happening
(who ran what, when, and what the query looked like), but the CU figures it reports are
**monitored-CU** — a CPU-time proxy computed from query telemetry — not the *billed*
CU that appears on your Microsoft invoice.

## 2. The validation gap we saw in the demo

When we compared the agent's per-user CU ranking against the Fabric Capacity Metrics
app for the same time window:

- The agent's **top consumers matched** the Metrics app for the biggest users (Daniel,
  Matthew, Kristyn, etc.)
- **Some smaller users** who appear in the Metrics app (e.g. Olivia) were **missing**
  from the agent's list
- **Percentages differed slightly** because the agent uses monitored-CU proxy and the
  Metrics app uses billed CU

The gap has two causes:
1. **Coverage gap** — Log Analytics has ingestion sampling that can drop low-volume users
2. **Unit gap** — monitored CU (proxy) vs billed CU (authoritative) will never match to
   two decimals, only directionally

## 3. Two Azure roles that close part of the gap

**Where these live:** Azure Portal → the Fabric capacity's Azure resource (each Fabric
capacity is registered as an Azure resource of type `Microsoft.Fabric/capacities`)

**How to grant:** IAM → Add role assignment → assign to the agent's service principal

### 3a. **Reader** role
- **What it grants:** view the capacity resource's metadata (SKU, region, current state)
- **Why we want it:** confirm the capacity's shape at any point in time; needed as a
  prerequisite for Monitoring Reader
- **Blast radius:** view-only, on this one resource. Cannot modify, scale, pause,
  or delete anything.

### 3b. **Monitoring Reader** role
- **What it grants:** read Azure Monitor's raw infrastructure metrics for this capacity
  — CPU%, throttle events, memory pressure
- **Why we want it:** Azure Monitor is the *authoritative* source for these signals
  (same data Microsoft support uses when triaging your tickets). It closes the throttle-
  detection accuracy gap and is a strong second data point for validating our Log
  Analytics readings.
- **Blast radius:** read-only metrics for this one capacity. Cannot post metrics,
  change alert rules, or see any other resource in the subscription.

### What these roles do **not** solve
Neither role gives the agent billed CU per user. That data lives in the Fabric Capacity
Metrics app (Power BI plane), not the Azure plane — so it needs a **separate Fabric-side
grant**, not an Azure RBAC grant. See §5.

## 4. Splitting the permission story by plane (important framing)

Sowmya's meeting comment — *"Fabric doesn't live inside Azure"* — is right, and it
affects how we ask for each permission:

| Permission | Plane | Where to grant it | Who grants it |
|---|---|---|---|
| Reader / Monitoring Reader on the capacity | **Azure** | Azure Portal → capacity resource → IAM | Azure subscription Owner |
| Storage Blob Data Reader on OneLake | **Azure** | Azure Portal → OneLake storage account → IAM | Azure subscription Owner |
| Workspace Viewer / model Read | **Fabric** | Fabric portal → workspace or model → Manage permissions | Workspace admin |
| Tenant.Read.All, Report.Read.All, etc. | **Fabric admin API** | Fabric admin portal → Tenant settings | Fabric admin |
| Entra Agent Identity provisioning | **Entra (Azure AD)** | Entra portal → Enterprise apps | Tenant admin |

The Azure grants (§3) are the smallest, lowest-risk step and close the throttle-
detection validation gap. Everything else is optional next-tier work.

## 5. What closes the full validation gap: FUAM

FUAM = **Fabric Unified Admin Monitoring** — Microsoft's open-source admin toolkit that
ingests the Capacity Metrics app data (plus tenant-wide activity events) into a
Lakehouse the agent can query.

Standing up FUAM would give the agent access to:
- **Billed CU per user** (authoritative, matches the invoice)
- **Long-term history** (>30 days; today the agent's history resets on redeploy)
- **Estate-wide activity events** (workspace usage, refresh history, ownership over time)

**Grants needed to activate FUAM for the agent:**
- **Storage Blob Data Reader** on the OneLake storage account (Azure grant)
- **Viewer** on the FUAM workspace (Fabric grant)

Once FUAM is in place, we can do a proper A/B validation: agent output next to the
Metrics app output, same time window, per-user percentages should match within
rounding error.

## 6. Validation methodology (how we'd prove parity)

Once the grants land:

1. Pick 5 recent throttle events (last 30 days) from the Metrics app
2. For each event, ask the agent to produce its top-5 consumer ranking for that window
3. Compare against the Metrics app's top-5 for the same window
4. Score: user match (do the same users appear?), rank match (in the same order?),
   percentage match (within tolerance?)
5. Any mismatch → root-cause it (sampling gap, timing gap, unit conversion) and log
6. Repeat monthly to catch drift

## 7. What I need from you

**Right now (to unblock the Azure-plane validation gap):**
- Approval to submit an IAM request for **Reader + Monitoring Reader** on the Fabric
  capacity resource, granted to the agent's existing service principal
- The subscription ID + capacity resource name (I have these but want to confirm with you
  before submitting)

**Next-tier (to unblock full authoritative-CU validation):**
- Alignment on standing up FUAM in a dedicated admin workspace (this is a bigger ask —
  worth its own conversation once the Azure grants are in and validated)

## 8. Nothing on this list changes the invariants

For the record:
- Agent stays **read-only on data and capacity** — no writes, no refreshes, no scale actions
- Every grant here is **read-only** at the RBAC layer, bounded to specific resources
- No new outbound (Teams, email, ADO tickets) is activated by these grants — those are
  separate P7 items with their own admin gates

---

*Questions or edits before I send this to IAM? Happy to walk through anything in detail.*

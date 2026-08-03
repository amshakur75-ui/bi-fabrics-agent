# Fabric Audit Agent — Azure Roles, Integration & Validation Brief

**For:** Sowmya
**From:** Abdishakur
**Purpose:** Give a shared understanding of (1) what data the agent sees today, (2) what
additional Azure roles would close the validation gaps we saw when comparing the agent's
output to the [Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app),
and (3) how we'd verify parity once the grants are in place.

> **Reference index:** every doc link cited here is consolidated in
> [`REFERENCES-INDEX.md`](./REFERENCES-INDEX.md) for easy sharing with your manager
> or the IAM approvers.

---

## 1. What the agent sees today (baseline)

The agent is running read-only against three telemetry surfaces:

| Source | What it gives us | Known limits | Reference |
|---|---|---|---|
| **[Fabric Admin REST API](https://learn.microsoft.com/en-us/rest/api/fabric/)** | Capacities, workspaces, items, refresh schedules | Rate-limited; does not expose per-operation billing data | [API rate limits](https://learn.microsoft.com/en-us/rest/api/fabric/articles/throttling) |
| **[Log Analytics for Power BI](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview)** (via Fabric diagnostic settings) | Per-user query attribution, query text, durations | ~5-min ingestion lag; sampling can drop low-volume users | [Diagnostic settings](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings) |
| **[Capacity Events Eventhouse](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse)** (Kusto / [KQL](https://learn.microsoft.com/en-us/kusto/query/)) | Per-event CU-seconds, throttle windows, user timelines | Same source as the Metrics app for events, but not for the *billed* CU column | [Kusto query language](https://learn.microsoft.com/en-us/kusto/query/) |

**What this means in practice:** the agent can find and explain what's happening
(who ran what, when, and what the query looked like), but the CU figures it reports are
**monitored-CU** — a CPU-time proxy computed from query telemetry — not the *billed*
CU that appears on your Microsoft invoice. For details on how Fabric bills capacity,
see [Fabric capacity concepts](https://learn.microsoft.com/en-us/fabric/enterprise/licenses)
and [Understand your Fabric capacity](https://learn.microsoft.com/en-us/fabric/enterprise/plan-capacity).

## 2. The validation gap we saw in the demo

When we compared the agent's per-user CU ranking against the [Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
for the same time window:

- The agent's **top consumers matched** the Metrics app for the biggest users (Daniel,
  Matthew, Kristyn, etc.)
- **Some smaller users** who appear in the Metrics app (e.g. Olivia) were **missing**
  from the agent's list
- **Percentages differed slightly** because the agent uses monitored-CU proxy and the
  Metrics app uses billed CU (see [smoothing and throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling))

The gap has two causes:
1. **Coverage gap** — Log Analytics has [ingestion sampling](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-collection-rule-overview)
   that can drop low-volume users
2. **Unit gap** — monitored CU (proxy) vs billed CU (authoritative — see
   [CU consumption](https://learn.microsoft.com/en-us/fabric/enterprise/fabric-capacity-tier-availability))
   will never match to two decimals, only directionally

## 3. Two Azure roles that close part of the gap

**Where these live:** [Azure Portal](https://portal.azure.com) → the Fabric capacity's
Azure resource (each Fabric capacity is registered as an Azure resource of type
[`Microsoft.Fabric/capacities`](https://learn.microsoft.com/en-us/azure/templates/microsoft.fabric/capacities))

**How to grant:** IAM → Add role assignment → assign to the agent's service principal.
Step-by-step: [Assign Azure roles using the Azure portal](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal).

For background: [What is Azure RBAC](https://learn.microsoft.com/en-us/azure/role-based-access-control/overview)
· [Azure built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles).

### 3a. **Reader** role
- **Definition:** [Reader (built-in)](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general#reader)
- **What it grants:** view the capacity resource's metadata (SKU, region, current state)
- **Why we want it:** confirm the capacity's shape at any point in time; needed as a
  prerequisite for Monitoring Reader
- **Blast radius:** view-only, on this one resource. Cannot modify, scale, pause,
  or delete anything.
- **Fabric capacity pause/resume via API** (what this role does NOT allow):
  [Manage Fabric capacity](https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity)

### 3b. **Monitoring Reader** role
- **Definition:** [Monitoring Reader (built-in)](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/monitor#monitoring-reader)
- **What it grants:** read Azure Monitor's raw infrastructure metrics for this capacity
  — CPU%, throttle events, memory pressure. See [Azure Monitor for Fabric](https://learn.microsoft.com/en-us/fabric/enterprise/monitoring-hub)
  and [Monitor metrics with Azure Monitor](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/data-platform-metrics).
- **Why we want it:** Azure Monitor is the *authoritative* source for these signals
  (same data Microsoft support uses when triaging your tickets). It closes the throttle-
  detection accuracy gap and is a strong second data point for validating our Log
  Analytics readings.
- **Blast radius:** read-only metrics for this one capacity. Cannot post metrics,
  change alert rules, or see any other resource in the subscription.
- **Metrics available:** [Fabric platform metrics reference](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-fabric-capacities-metrics)

### What these roles do **not** solve
Neither role gives the agent billed CU per user. That data lives in the [Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
(Power BI plane), not the Azure plane — so it needs a **separate Fabric-side grant**,
not an Azure RBAC grant. See §5.

## 4. Splitting the permission story by plane (important framing)

Sowmya's meeting comment — *"Fabric doesn't live inside Azure"* — is right, and it
affects how we ask for each permission. Fabric's own [security architecture doc](https://learn.microsoft.com/en-us/fabric/security/security-overview)
explains the split:

| Permission | Plane | Where to grant it | Who grants it | Docs |
|---|---|---|---|---|
| Reader / Monitoring Reader on the capacity | **Azure** | [Azure Portal](https://portal.azure.com) → capacity resource → IAM | Azure subscription Owner | [Role assignments](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal) |
| Storage Blob Data Reader on OneLake | **Azure** | Azure Portal → OneLake storage account → IAM | Azure subscription Owner | [Storage Blob Data Reader](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage#storage-blob-data-reader) · [OneLake security](https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security) |
| Workspace Viewer / model Read | **Fabric** | [Fabric portal](https://app.fabric.microsoft.com) → workspace or model → Manage permissions | Workspace admin | [Workspace roles](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces) |
| Tenant.Read.All, Report.Read.All, etc. | **Fabric admin API** | [Fabric admin portal](https://learn.microsoft.com/en-us/fabric/admin/admin-center) → Tenant settings | Fabric admin | [Admin API scopes](https://learn.microsoft.com/en-us/rest/api/power-bi/admin) |
| Entra Agent Identity provisioning | **Entra (Azure AD)** | [Entra portal](https://entra.microsoft.com) → Enterprise apps | Tenant admin | [Service principals](https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals) · [Entra Agent ID](https://learn.microsoft.com/en-us/entra/architecture/agent-identity-management-architecture) |

The Azure grants (§3) are the smallest, lowest-risk step and close the throttle-
detection validation gap. Everything else is optional next-tier work.

## 5. What closes the full validation gap: FUAM

**FUAM = [Fabric Unified Admin Monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)**
— Microsoft's open-source admin toolkit that ingests the Capacity Metrics app data
(plus tenant-wide activity events) into a Lakehouse the agent can query.

Related Microsoft resources:
- [FUAM repository (source of truth)](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)
- [Fabric admin monitoring workspace](https://learn.microsoft.com/en-us/fabric/admin/monitoring-workspace)
- [Monitor Fabric activity](https://learn.microsoft.com/en-us/fabric/admin/track-user-activities)
- [Fabric activity events API](https://learn.microsoft.com/en-us/rest/api/fabric/admin/workspaces/list-workspaces)

Standing up FUAM would give the agent access to:
- **Billed CU per user** (authoritative, matches the invoice)
- **Long-term history** (>30 days; today the agent's history resets on redeploy)
- **Estate-wide activity events** (workspace usage, refresh history, ownership over time)

**Grants needed to activate FUAM for the agent:**
- **[Storage Blob Data Reader](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage#storage-blob-data-reader)**
  on the [OneLake](https://learn.microsoft.com/en-us/fabric/onelake/onelake-overview) storage account (Azure grant)
- **[Viewer](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces)** on the FUAM workspace (Fabric grant)

Once FUAM is in place, we can do a proper A/B validation: agent output next to the
Metrics app output, same time window, per-user percentages should match within
rounding error.

**Alternative to FUAM (lighter):** the [Metrics app itself supports data export](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-troubleshoot)
if we want a narrower first step — same billed-CU data, but without the long-term
history and admin-event coverage FUAM adds.

## 6. Validation methodology (how we'd prove parity)

Once the grants land:

1. Pick 5 recent throttle events (last 30 days) from the [Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
2. For each event, ask the agent to produce its top-5 consumer ranking for that window
3. Compare against the Metrics app's top-5 for the same window
4. Score: user match (do the same users appear?), rank match (in the same order?),
   percentage match (within tolerance?)
5. Any mismatch → root-cause it (sampling gap, timing gap, unit conversion) and log
6. Repeat monthly to catch drift

**Understanding CU math** (useful for interpreting mismatches):
- [How Fabric bills capacity](https://learn.microsoft.com/en-us/fabric/enterprise/fabric-capacity-tier-availability)
- [Smoothing and throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)
- [Interactive vs Background operations](https://learn.microsoft.com/en-us/fabric/enterprise/throttling#future-smoothing-for-background-operations)
- [Understanding the CU calculation](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)

## 7. What I need from you

**Right now (to unblock the Azure-plane validation gap):**
- Approval to submit an IAM request for **Reader + Monitoring Reader** on the Fabric
  capacity resource, granted to the agent's existing service principal
- The subscription ID + capacity resource name (I have these but want to confirm with you
  before submitting)

**Next-tier (to unblock full authoritative-CU validation):**
- Alignment on standing up [FUAM](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)
  in a dedicated admin workspace (this is a bigger ask — worth its own conversation
  once the Azure grants are in and validated)

## 8. Nothing on this list changes the invariants

For the record:
- Agent stays **read-only on data and capacity** — no writes, no refreshes, no scale actions
- Every grant here is **read-only** at the RBAC layer, bounded to specific resources
- No new outbound (Teams, email, ADO tickets) is activated by these grants — those are
  separate P7 items with their own admin gates

For the internal policy alignment on read-only agents, see the [security invariants
overview](https://learn.microsoft.com/en-us/fabric/security/security-overview) and
[least-privilege guidance](https://learn.microsoft.com/en-us/security/zero-trust/deploy/data).

---

## References — quick index

**Fabric / Power BI**
- [Fabric documentation home](https://learn.microsoft.com/en-us/fabric/)
- [Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
- [Fabric throttling & smoothing](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)
- [Fabric admin center](https://learn.microsoft.com/en-us/fabric/admin/admin-center)
- [Fabric workspace roles](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces)
- [Fabric admin monitoring workspace](https://learn.microsoft.com/en-us/fabric/admin/monitoring-workspace)
- [OneLake overview](https://learn.microsoft.com/en-us/fabric/onelake/onelake-overview)
- [OneLake security](https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security)
- [Fabric REST API](https://learn.microsoft.com/en-us/rest/api/fabric/)
- [Power BI Admin REST API](https://learn.microsoft.com/en-us/rest/api/power-bi/admin)
- [Log Analytics for Power BI](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview)

**Azure RBAC & Monitor**
- [Azure RBAC overview](https://learn.microsoft.com/en-us/azure/role-based-access-control/overview)
- [Azure built-in roles (full catalog)](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles)
- [Reader role](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general#reader)
- [Monitoring Reader role](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/monitor#monitoring-reader)
- [Storage Blob Data Reader](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage#storage-blob-data-reader)
- [Assign roles in Azure portal](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal)
- [Azure Monitor overview](https://learn.microsoft.com/en-us/azure/azure-monitor/overview)
- [Fabric platform metrics reference](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-fabric-capacities-metrics)
- [Diagnostic settings](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings)

**FUAM**
- [FUAM repo (GitHub)](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)
- [fabric-toolbox root](https://github.com/microsoft/fabric-toolbox)

**Identity**
- [Service principals in Entra](https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals)
- [Entra Agent ID architecture](https://learn.microsoft.com/en-us/entra/architecture/agent-identity-management-architecture)

**Databricks (agent hosting)**
- [Databricks Apps overview](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/index.html)
- [Model Serving](https://docs.databricks.com/aws/en/machine-learning/model-serving/index.html)

*See [`REFERENCES-INDEX.md`](./REFERENCES-INDEX.md) for the consolidated catalog.*

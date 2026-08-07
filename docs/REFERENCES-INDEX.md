# Fabric Audit Agent — References Index

**Purpose:** A single searchable catalog of every doc, portal, and repo the Fabric
Audit Agent depends on or that its permission asks reference. Use this when preparing
for stakeholder conversations (the IAM approvers / capacity admins), or when researching a
specific concept.

Organized by topic. Where possible, links point to Microsoft's official docs, GitHub
source, or the vendor's own reference page.

---

## 🎯 Portals (where you actually click)

| Portal | Purpose | URL |
|---|---|---|
| Azure Portal | Grant Azure RBAC roles, manage Fabric capacity resource | https://portal.azure.com |
| Entra (Azure AD) admin center | Service principals, Entra Agent Identity | https://entra.microsoft.com |
| Fabric portal | Workspaces, items, admin portal | https://app.fabric.microsoft.com |
| Fabric admin center | Tenant settings, admin API scopes | https://app.fabric.microsoft.com/admin-portal |
| Power BI service | Legacy Power BI admin surface (still active) | https://app.powerbi.com |
| Databricks workspace | Agent hosting | https://adb-7405609570261849.9.azuredatabricks.net |
| Azure DevOps | Ticketing (P7 outbound) | https://dev.azure.com |

## 📘 Microsoft Fabric — core concepts

- [Fabric documentation home](https://learn.microsoft.com/en-us/fabric/)
- [What is Fabric?](https://learn.microsoft.com/en-us/fabric/fundamentals/microsoft-fabric-overview)
- [Fabric capacity licensing](https://learn.microsoft.com/en-us/fabric/enterprise/licenses)
- [Fabric capacity tiers](https://learn.microsoft.com/en-us/fabric/enterprise/fabric-capacity-tier-availability)
- [Plan Fabric capacity](https://learn.microsoft.com/en-us/fabric/enterprise/plan-capacity)
- [Scale Fabric capacity](https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity)
- [Fabric security overview](https://learn.microsoft.com/en-us/fabric/security/security-overview)
- [Fabric workspace roles](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces)

## 📊 Fabric Capacity Metrics app (the authoritative CU source)

- [Metrics app overview](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
- [Install the Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-install)
- [Compute page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)
- [Storage page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-storage-page)
- [Timepoint detail page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-detail-page)
- [Overages page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-overages)
- [Troubleshoot & export data](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-troubleshoot)

## ⚙️ Throttling, smoothing & CU math

- [Fabric throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)
- [Smoothing behavior](https://learn.microsoft.com/en-us/fabric/enterprise/throttling#smoothing)
- [Interactive vs Background operations](https://learn.microsoft.com/en-us/fabric/enterprise/throttling#future-smoothing-for-background-operations)
- [Understanding CU consumption](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)

## 🔐 Azure RBAC (the roles the agent needs)

- [Azure RBAC overview](https://learn.microsoft.com/en-us/azure/role-based-access-control/overview)
- [Built-in roles (full catalog)](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles)
- [Reader role](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general#reader)
- [Monitoring Reader role](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/monitor#monitoring-reader)
- [Storage Blob Data Reader](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage#storage-blob-data-reader)
- [How to assign roles in the portal](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal)
- [Assign roles via CLI](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-cli)
- [Custom roles (if needed)](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles)

## 🛰️ Azure Monitor (throttle-detection ground truth)

- [Azure Monitor overview](https://learn.microsoft.com/en-us/azure/azure-monitor/overview)
- [Platform metrics for Fabric capacities](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-fabric-capacities-metrics)
- [Metrics data platform](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/data-platform-metrics)
- [Diagnostic settings](https://learn.microsoft.com/en-us/azure/azure-monitor/essentials/diagnostic-settings)
- [Log Analytics data collection rules](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-collection-rule-overview)

## 🗄️ OneLake & Fabric data lake

- [OneLake overview](https://learn.microsoft.com/en-us/fabric/onelake/onelake-overview)
- [OneLake security](https://learn.microsoft.com/en-us/fabric/onelake/security/get-started-security)
- [OneLake file explorer](https://learn.microsoft.com/en-us/fabric/onelake/onelake-file-explorer)

## 🧾 Admin telemetry & FUAM

- **[FUAM repo (GitHub)](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)** ← authoritative source
- [Microsoft fabric-toolbox root](https://github.com/microsoft/fabric-toolbox)
- [Fabric admin monitoring workspace](https://learn.microsoft.com/en-us/fabric/admin/monitoring-workspace)
- [Track user activities](https://learn.microsoft.com/en-us/fabric/admin/track-user-activities)
- [Admin center](https://learn.microsoft.com/en-us/fabric/admin/admin-center)

## 🔎 Log Analytics (current attribution source)

- [Log Analytics for Power BI](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview)
- [Connect a workspace to Log Analytics](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure)
- [KQL query language](https://learn.microsoft.com/en-us/kusto/query/)
- [KQL cheat sheet](https://learn.microsoft.com/en-us/azure/data-explorer/kql-quick-reference)

## 🌊 Eventhouse / Real-Time Intelligence

- [Eventhouse overview](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse)
- [KQL databases in Fabric](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/create-database)

## 🌐 REST APIs

- [Fabric REST API root](https://learn.microsoft.com/en-us/rest/api/fabric/)
- [Fabric REST — throttling / rate limits](https://learn.microsoft.com/en-us/rest/api/fabric/articles/throttling)
- [Fabric REST — using Fabric APIs](https://learn.microsoft.com/en-us/rest/api/fabric/articles/using-fabric-apis)
- [Power BI Admin REST API](https://learn.microsoft.com/en-us/rest/api/power-bi/admin)
- [Power BI REST API](https://learn.microsoft.com/en-us/rest/api/power-bi/)

## 🧬 Semantic models & DAX

- [DAX guidance index](https://learn.microsoft.com/en-us/dax/guidance/)
- [Optimize DAX](https://learn.microsoft.com/en-us/power-bi/guidance/dax-optimize-model)
- [Import modeling data reduction](https://learn.microsoft.com/en-us/power-bi/guidance/import-modeling-data-reduction)
- [Semantic Link overview (Python access to models)](https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-overview)
- [Semantic Link Labs (GitHub)](https://github.com/microsoft/semantic-link-labs)
- [Refresh types](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-data)

## 🧊 Analysis Services & MDX (for the Ent-Reporting-Sales case)

- [Azure Analysis Services overview](https://learn.microsoft.com/en-us/azure/analysis-services/analysis-services-overview)
- [MDX query fundamentals](https://learn.microsoft.com/en-us/sql/mdx/mdx-query-fundamentals-analysis-services)
- [CrossJoin (MDX)](https://learn.microsoft.com/en-us/sql/mdx/crossjoin-mdx)
- [DrilldownMember (MDX)](https://learn.microsoft.com/en-us/sql/mdx/drilldownmember-mdx)
- [Aggregations in AAS Multidimensional](https://learn.microsoft.com/en-us/analysis-services/multidimensional-models/aggregations-and-aggregation-designs)
- [Performance tuning of tabular models](https://learn.microsoft.com/en-us/analysis-services/tabular-models/performance-tuning-of-tabular-models)
- [Live connect to Analysis Services from Power BI](https://learn.microsoft.com/en-us/power-bi/connect-data/desktop-analysis-services-tabular-data)
- [Live connect to SSAS Multidimensional](https://learn.microsoft.com/en-us/power-bi/connect-data/desktop-connect-to-ssas-multidimensional-model-live)

## 📄 Paginated reports (a suggested optimization)

- [Paginated reports overview](https://learn.microsoft.com/en-us/power-bi/paginated-reports/paginated-reports-report-builder-power-bi)
- [When to use paginated vs interactive](https://learn.microsoft.com/en-us/power-bi/paginated-reports/paginated-reports-vs-power-bi-reports)

## 🆔 Identity — Entra & Service Principals

- [Entra Identity Platform](https://learn.microsoft.com/en-us/entra/identity-platform/)
- [App registrations & service principals](https://learn.microsoft.com/en-us/entra/identity-platform/app-objects-and-service-principals)
- [Create a service principal](https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal)
- [Entra Agent ID architecture](https://learn.microsoft.com/en-us/entra/architecture/agent-identity-management-architecture)
- [OAuth 2.0 client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [OAuth 2.0 on-behalf-of flow (OBO)](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)

## 🏗️ Databricks — agent hosting

- [Databricks Apps overview](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/index.html)
- [Databricks Apps configuration](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/configuration.html)
- [Databricks Apps resource bindings](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-resources.html)
- [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/index.html)
- [Databricks Model Serving](https://docs.databricks.com/aws/en/machine-learning/model-serving/index.html)
- [Managed MCP on Databricks](https://docs.databricks.com/aws/en/generative-ai/mcp/)
- [Databricks Secrets](https://docs.databricks.com/aws/en/security/secrets/index.html)
- [Databricks Jobs (for the scheduled sweep)](https://docs.databricks.com/aws/en/jobs/index.html)
- [Serverless compute for Jobs](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs.html)

## 🔌 MLflow (the agent framework)

- [MLflow docs home](https://mlflow.org/docs/latest/)
- [MLflow tracing](https://mlflow.org/docs/latest/tracing/)
- [MLflow Responses Agent](https://mlflow.org/docs/latest/genai/mlops-runbook/)
- [Databricks MLflow](https://docs.databricks.com/aws/en/mlflow/index.html)

## 📩 Azure DevOps (P7 outbound — ticket filing + change correlation)

- [Azure DevOps documentation](https://learn.microsoft.com/en-us/azure/devops/)
- [Personal Access Tokens](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate)
- [OAuth apps in Azure DevOps](https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/oauth)
- [Work Items REST API](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/)
- [Repos REST API](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/)

## 🔔 Fabric Activator / Real-Time triggers (P7 outbound alternative)

- [Activator (Reflex) overview](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/data-activator-introduction)
- [Trigger actions](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/data-activator-trigger-actions)

## ✉️ Microsoft Teams outbound

- [Incoming webhooks for Teams](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook)
- [Adaptive Cards (message format)](https://learn.microsoft.com/en-us/adaptive-cards/)
- [Adaptive Cards designer](https://adaptivecards.io/designer/)
- [Microsoft Graph — send Teams messages (long-term path)](https://learn.microsoft.com/en-us/graph/api/chatmessage-post)

## 🧠 Anthropic (agent LLM)

- [Claude on Databricks](https://docs.databricks.com/aws/en/machine-learning/model-serving/foundation-models.html)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
- [Tool use with Claude](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
- [Prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)

## 🔒 Security / Zero-Trust guidance (for the invariants pitch)

- [Zero Trust deployment guide (data)](https://learn.microsoft.com/en-us/security/zero-trust/deploy/data)
- [Least-privilege best practices](https://learn.microsoft.com/en-us/azure/role-based-access-control/best-practices)
- [Fabric security overview](https://learn.microsoft.com/en-us/fabric/security/security-overview)

---

## 📎 This project's internal docs

- [`DEPLOY-STATUS.md`](./DEPLOY-STATUS.md) — current deploy state

---

*Keep this file as a living index — add links as new questions come up. Every URL
here points to a Microsoft-official, vendor-official, or open-source authoritative
source. Nothing is inferred.*

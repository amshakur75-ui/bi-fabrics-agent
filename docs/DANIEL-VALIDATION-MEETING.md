# Daniel Validation Meeting — Agenda & Use Cases

**Attendees:** Daniel, Abdishakur (+ Sowmya if available)
**Target:** Thu/Fri after 12pm
**Goal:** Have Daniel — as the Fabric/model owner — validate three specific agent
findings against his authoritative source ([Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)),
and confirm whether the root-cause explanations match what he'd conclude as a human
expert.

**Time budget:** 45 min. 5 min context, 30 min the three cases, 10 min discussion.

> **Bring:** the [Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
> open in a browser tab, in the timepoint-detail view. The comparison is more useful
> side-by-side.

---

## Context (5 min)

The agent is a read-only Fabric capacity investigator. It reads
[Log Analytics for Power BI](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview),
[Fabric admin APIs](https://learn.microsoft.com/en-us/rest/api/fabric/), and the
Capacity Events [Eventhouse](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse)
via [KQL](https://learn.microsoft.com/en-us/kusto/query/); it does not have direct
model access yet ([semantic link labs](https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-overview)
is the future path). Sowmya has seen the demo. This meeting is specifically to have
you (Daniel) verify that the findings *match reality* — because you know these models
and users better than anyone else.

**What I want from you today:**
1. For each of the three cases below — does the agent's root cause match what you'd
   conclude?
2. Any factual errors or missing context?
3. What would make the findings *actionable* for you (i.e. what's the format /
   granularity you'd want if the agent filed a ticket)?

---

## Case 1 — The Ent-Reporting-Sales / MDX / AAS discovery

**What the agent said:**
Ent-Reporting-Sales isn't a native Fabric semantic model — it's a
[**Live Connection**](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-data#semantic-models-and-data-sources-that-dont-support-refresh)
to an external Analysis Services cube (likely the
[Azure Analysis Services](https://learn.microsoft.com/en-us/azure/analysis-services/analysis-services-overview)
environment). Every visual round-trips
[MDX](https://learn.microsoft.com/en-us/sql/mdx/mdx-query-fundamentals-analysis-services)
to that cube; the Power BI capacity pays the CU for the connector traffic.

**How it deduced this:**
Pulled the query text for the top interactive queries in the throttle window. Every
one was pure MDX (`CrossJoin`, `NON EMPTY`, `DIMENSION PROPERTIES`, `[Measures].[…]`)
— which is
[Analysis Services syntax](https://learn.microsoft.com/en-us/analysis-services/multidimensional-models/mdx/mdx-query-fundamentals-analysis-services),
not [DAX](https://learn.microsoft.com/en-us/dax/). Ran a DAX analyzer against the
queries and got no anti-pattern flags because the analyzer is a DAX tool and this
isn't DAX.

**Consequences the agent called out:**
- Refresh-schedule investigations are dead ends here — there's no local model to refresh
- The tuning surface is not Fabric — [cube aggregations](https://learn.microsoft.com/en-us/analysis-services/multidimensional-models/aggregations-and-aggregation-designs)
  owned by the SSAS/AAS team are the real lever
- Signature cost drivers:
  - **[CrossJoin](https://learn.microsoft.com/en-us/sql/mdx/crossjoin-mdx) blowups**
    with `NON EMPTY` across many hierarchies
  - **Wide measure lists in single visuals** (10+ measures → multiple sub-cubes)
  - **[DrilldownMember](https://learn.microsoft.com/en-us/sql/mdx/drilldownmember-mdx) chains**
    that expand hierarchies live

**Reference reading:**
- [Live connect to Analysis Services in Power BI](https://learn.microsoft.com/en-us/power-bi/connect-data/desktop-analysis-services-tabular-data)
- [Aggregations in Analysis Services (multidimensional)](https://learn.microsoft.com/en-us/analysis-services/multidimensional-models/aggregations-and-aggregation-designs)
- [Performance tuning of tabular models](https://learn.microsoft.com/en-us/analysis-services/tabular-models/performance-tuning-of-tabular-models)

**Question for Daniel:**
1. Is Ent-Reporting-Sales in fact a Live Connection to your AAS environment? If not,
   what is it?
2. If yes — is the AAS team a separate team, or does that come back to you?
3. Is "fix cube aggregations" the right call, or is there a different first move
   (isolate to its own capacity, migrate off AAS, etc.)?

---

## Case 2 — The 176.8% throttle event at 04:07 UTC on 2026-07-14

**What the agent said:**
Peak CU **176.8%** with **30.5 minutes throttled**. Throttle gate confirmed passed
(30.5 min of actual throttling, not just [smoothing](https://learn.microsoft.com/en-us/fabric/enterprise/throttling#smoothing)).
Peak was **interactive, not refresh** — in the ±30-min window, interactive load was
13,304 CU-sec vs 1.8 CU-sec refresh (7,000:1 split).

Top interactive offenders in the window:
- **hemal.patel** — 1,785.5 CU-sec, 177s query
- **kristyn.rooney** — 1,215.6 + 1,171.6 + 1,030.4 CU-sec (three queries)
- **scott.fossett** — 639.1 CU-sec, 52s query
- **michael.balsdon** — 443.8 CU-sec, **414s query**

**Reference reading:**
- [Understanding Fabric throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)
- [Interactive vs Background operations](https://learn.microsoft.com/en-us/fabric/enterprise/throttling#future-smoothing-for-background-operations)
- [Fabric Capacity Metrics — Compute page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)
- [Fabric Capacity Metrics — Timepoint detail](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-detail-page)

**Question for Daniel:**
1. **Open your Metrics app for the same window (04:07 UTC ±30 min) and compare** —
   do these five users appear in your top consumers for that time?
2. Are the CU-sec ranks the same, or does the Metrics app show a different ordering?
3. Any users the agent missed that appear in your Metrics app view?
4. Michael Balsdon's 414-second single query — do you have visibility into what
   report/visual generated it? That's the biggest outlier.

*Why this case matters:* this is where the agent's monitored-CU proxy meets the
authoritative billed CU from the Metrics app. If we're off by one or two users in the
top 5, we have a real coverage gap to fix (probably via
[FUAM](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)).
If we're within one user, the current approach is good enough for triage even without
FUAM.

---

## Case 3 — Weekend CU anomaly: Fri Jul 3 = 9.65M CU-sec, Sat Jul 4 = 8.17M CU-sec

**What the agent said (from the 14-day daily pattern query):**
Weekday baseline on Ent-Reporting-Sales runs ~2–3M CU-sec on 300–400 users.

But:
- **Fri Jul 3 — 9.65M CU-sec, 161 users** (fewer users, ~4x normal CU)
- **Sat Jul 4 — 8.17M CU-sec, 53 users** (weekend, tiny audience, huge CU)
- **Fri Jul 10 — 8.76M CU-sec, 277 users** (same Friday spike shape)

That's a "few users, huge CU" signature — classic sign of one or two very expensive
sessions dominating the day.

**Reference reading (patterns worth checking):**
- [Fabric Capacity Metrics — Overages](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-overages)
- [Optimize refresh scheduling](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-data#configure-scheduled-refresh)
- [Paginated reports (a lower-CU alternative for detail-heavy views)](https://learn.microsoft.com/en-us/power-bi/paginated-reports/paginated-reports-report-builder-power-bi)

**Question for Daniel:**
1. Is there a known scheduled workload that fires on Fridays / weekends? (Batch report
   distribution, month-end close, executive dashboard prep, etc.)
2. If not — is this behavior a red flag you'd want the agent to auto-alert on?
3. What would the ideal alert threshold be — CU % of baseline, absolute CU, or
   "user count dropped below X while CU stayed above Y"?

---

## The validation gap we already know about (5 min)

**Olivia's case** — from the meeting with Sowmya, when we compared the agent's
per-user list against the Metrics app, Olivia appeared in the Metrics app but not in
the agent's top consumers. This is expected — the agent reads Log Analytics, which
has [sampling and ingestion behavior](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-collection-rule-overview);
the Metrics app reads Microsoft's internal billing telemetry which sees every user.

**Our fix path:**
[FUAM](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)
(Fabric Unified Admin Monitoring). Stand up FUAM in an admin workspace, and the agent
reads directly from the same source the Metrics app uses.

Related:
- [FUAM repo](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)
- [Fabric admin monitoring workspace](https://learn.microsoft.com/en-us/fabric/admin/monitoring-workspace)
- [Fabric activity events tracking](https://learn.microsoft.com/en-us/fabric/admin/track-user-activities)

**Question for Daniel:**
Is FUAM something you'd champion internally, or is there a lighter-weight step
(e.g. the [Metrics app's own export feature](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-troubleshoot))
you'd prefer first?

---

## Actions coming out of the meeting

*Will fill this in with Daniel's answers during the call.*

- [ ] Case 1 validated: Y / N — notes:
- [ ] Case 2 top-5 parity with Metrics app: matched / partial / missed — notes:
- [ ] Case 3 root cause: known workload / red flag / needs investigation — notes:
- [ ] FUAM: green light / defer / alternative recommended — notes:
- [ ] Format for actionable output (Teams? ADO ticket? Email?) — notes:
- [ ] Next follow-up: date/scope

---

## References — quick index

**Fabric Capacity Metrics + Throttling**
- [Metrics app overview](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app)
- [Compute page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)
- [Timepoint detail page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-timepoint-detail-page)
- [Overages page](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-overages)
- [Throttling & smoothing](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)
- [Capacity tier availability](https://learn.microsoft.com/en-us/fabric/enterprise/fabric-capacity-tier-availability)

**MDX / AAS**
- [MDX fundamentals](https://learn.microsoft.com/en-us/sql/mdx/mdx-query-fundamentals-analysis-services)
- [CrossJoin (MDX)](https://learn.microsoft.com/en-us/sql/mdx/crossjoin-mdx)
- [DrilldownMember (MDX)](https://learn.microsoft.com/en-us/sql/mdx/drilldownmember-mdx)
- [Azure Analysis Services overview](https://learn.microsoft.com/en-us/azure/analysis-services/analysis-services-overview)
- [Aggregations in AAS Multidimensional](https://learn.microsoft.com/en-us/analysis-services/multidimensional-models/aggregations-and-aggregation-designs)

**Power BI performance & anti-patterns**
- [DAX guidance index](https://learn.microsoft.com/en-us/dax/guidance/)
- [Optimizing DAX](https://learn.microsoft.com/en-us/power-bi/guidance/dax-optimize-model)
- [Reduce data model size](https://learn.microsoft.com/en-us/power-bi/guidance/import-modeling-data-reduction)
- [Paginated reports](https://learn.microsoft.com/en-us/power-bi/paginated-reports/paginated-reports-report-builder-power-bi)

**Admin telemetry & monitoring**
- [FUAM (Fabric Unified Admin Monitoring)](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)
- [Fabric monitoring workspace](https://learn.microsoft.com/en-us/fabric/admin/monitoring-workspace)
- [Track user activities](https://learn.microsoft.com/en-us/fabric/admin/track-user-activities)
- [Log Analytics for Power BI](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-overview)
- [KQL query language](https://learn.microsoft.com/en-us/kusto/query/)

*See [`REFERENCES-INDEX.md`](./REFERENCES-INDEX.md) for the consolidated catalog.*

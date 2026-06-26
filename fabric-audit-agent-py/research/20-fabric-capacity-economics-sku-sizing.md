# Fabric / Power BI Capacity Economics & SKU Sizing — Research for the "Optimize vs Size-Up" Verdict

> Scope: the **dollars-and-units** layer the bi-fabrics-audit-agent needs to put a credible $ figure on
> "cost to size up one SKU" vs "cost of optimizing." Covers the F-SKU ladder + CU/v-core/memory,
> pay-as-you-go vs reservation pricing, the Azure Retail Prices API (programmatic price lookup),
> autoscale billing, pause/resume economics, the F64 licensing cliff, capacity-overage (3x) economics,
> CU-seconds math, PPU-vs-capacity, and cost-management/budgets.
> Throttling/smoothing/surge *mechanics* are covered elsewhere — this file uses them only where they carry $ impact.
> Snapshot date: 2026-06-23. All hard prices are **US East (USD)** unless noted; **prices vary by region**.

---

## 0. The single most load-bearing number: the per-CU rate

**Pay-as-you-go base rate = $0.18 per CU per hour (US East), confirmed live from the Azure Retail Prices API.**
Every F-SKU PAYG price is just `CU × $0.18 × hours`. This is the anchor for all size-up deltas.

- $/CU/hour (PAYG) = **$0.18**
- $/CU/month (PAYG, 730 h) = **$131.40**
- $/CU/month (1-yr reserved, 40.5% off) ≈ **$78.18** (API block price below confirms)

Derived per-SKU table (US East, computed `CU × $0.18`; cross-checked against public 2026 tables — F2=$0.36/hr, F64=$11.52/hr match):

| SKU | CU | PAYG $/hr | PAYG $/mo (730h) | 1-yr Reserved $/mo (~40.5% off) |
|-----|----|-----------|------------------|----------------------------------|
| F2    | 2    | 0.36   | 262.80      | ~156    |
| F4    | 4    | 0.72   | 525.60      | ~313    |
| F8    | 8    | 1.44   | 1,051.20    | ~625    |
| F16   | 16   | 2.88   | 2,102.40    | ~1,251  |
| F32   | 32   | 5.76   | 4,204.80    | ~2,502  |
| F64   | 64   | 11.52  | 8,409.60    | ~5,004  |
| F128  | 128  | 23.04  | 16,819.20   | ~10,007 |
| F256  | 256  | 46.08  | 33,638.40   | ~20,014 |
| F512  | 512  | 92.16  | 67,276.80   | ~40,028 |
| F1024 | 1024 | 184.32 | 134,553.60  | ~80,056 |
| F2048 | 2048 | 368.64 | 269,107.20  | ~160,113 |

> **Verdict use:** the "size-up delta" between adjacent SKUs is exactly the *next* SKU's CU count minus the current one, times the rate. Because the ladder doubles, **going up one rung roughly doubles compute spend** — e.g., F64→F128 = +64 CU = **+$11.52/hr / +$8,410/mo PAYG** (or **+$5,004/mo reserved**). That doubling is precisely why an optimization that claws back, say, 25% of CU is often cheaper than the next rung.

---

## 1. F-SKU ladder: CU, Power BI P-SKU equivalence, and v-cores

**TITLE:** Understand Microsoft Fabric Licenses (capacity SKU table)
**URL:** https://learn.microsoft.com/en-us/fabric/enterprise/licenses
**Summary:** Authoritative SKU ladder. CUs measure compute power per SKU; the table also maps each F-SKU to its legacy Power BI Premium P-SKU and v-cores. Note Microsoft is **retiring the P-SKUs** and steering all customers to F-SKUs.

**Exact table (verbatim):**

| SKU | Capacity Units (CUs) | Power BI SKU | Power BI v-cores |
|-----|----|----------|------|
| F2    | 2    | –        | 0.25 |
| F4    | 4    | –        | 0.5  |
| F8    | 8    | EM/A1    | 1    |
| F16   | 16   | EM2/A2   | 2    |
| F32   | 32   | EM3/A3   | 4    |
| F64   | 64   | P1/A4    | 8    |
| Trial | 64   | –        | 8    |
| F128  | 128  | P2/A5    | 16   |
| F256  | 256  | P3/A6    | 32   |
| F512  | 512  | P4/A7    | 64   |
| F1024 | 1024 | P5/A8    | 128  |
| F2048 | 2048 | –        | 256  |

**Numbers/relationships:** CU = the F-number. v-cores = CU/8 (0.25 v-core per 2 CU). Power BI **memory limit per semantic model** historically tracks the P-SKU (P1/F64 = 25 GB, P2/F128 = 50 GB, P3/F256 = 100 GB) — relevant when a model OOMs ("Cannot load model due to reaching capacity limits") and the fix is size-up not optimization. The doubling structure means there are **no intermediate rungs** between, e.g., F64 and F128 — a key constraint for the verdict (you can't buy "F96").
**How it helps the verdict:** the agent maps an observed capacity to its CU budget, identifies the *only* next rung available, and quantifies the jump. Also lets it flag memory-bound (size-up unavoidable) vs CU-bound (optimizable) overloads.

---

## 2. Pay-as-you-go vs 1-year/3-year reservation — the discount, mechanics, and exact block price

**TITLE:** Save costs with Microsoft Fabric Capacity reservations
**URL:** https://learn.microsoft.com/en-us/azure/cost-management-billing/reservations/fabric-capacity
**Summary:** You commit to a quantity of **CUs** (not a named SKU) in a region for **1 or 3 years**; matching PAYG usage stops being charged at PAYG rates. Purchases are in **1-CU increments**. Reservation covers **only Fabric capacity compute** — **not storage or networking** (those stay PAYG). Discount applied **hourly**; **unused reserved CU-hours do NOT carry forward** (use-it-or-lose-it per hour).

**Exact numbers / mechanics (verbatim-sourced):**
- "Purchases are made in one CU increments." Example: to fully cover an F64, buy **64 CUs** of reservation.
- **Discount examples** (critical for sizing logic):
  - Reservation = capacity (64 CU res, F64 deployed): pay only reservation price.
  - Reservation > used (64 CU res, F32 deployed): F32 covered; the **other 32 CU-hours are lost each hour** they're unmatched (no carry-forward).
  - Reservation < used (64 CU res, F128 deployed): 64 CU covered at reserved rate, **remaining 64 CU billed at PAYG**.
  - Two F32s under one 64-CU reservation: fully covered (reservations float across capacities in scope).
- **Smoothing + reservation:** "purchase for **average** workload rather than the peak" — a 2-CU reservation can absorb a 4-CU spike because smoothing spreads it over 24 h.
- Refund cap: canceled reservation commitment can't exceed **USD 50,000 in a rolling 12-month window**. Can exchange Azure Synapse Dedicated SQL pool reservations into Fabric reservations.
- Scopes: single resource group / single subscription / **shared** (whole EA enrollment or MCA billing profile) / management group.
- In cost data the reservation purchase shows as meter **"Dataflows Standard Compute Capacity Usage CU"**, charge type **Purchase**, tier type **Fabric Cap**.

**Discount magnitude (live API, US East):** reservation `Fabric Capacity CU` block:
- **1 Year: retailPrice 938.0** (unitOfMeasure "1 Hour", productName "Fabric Capacity Reservation")
- **3 Years: retailPrice 2814.0**
This normalizes to **~$78.18 per CU per month reserved vs $131.40 PAYG = ~40.5% savings** (industry-cited "up to 40.5% / ~41%"). Confirmed: F64 reserved ≈ **$5,004/mo** (≈ the widely cited $5,002.67), F2 reserved ≈ **$156/mo**.

**Break-even rule the agent can state:** a 1-year reservation beats PAYG when the capacity runs **more than ~59% of the time** (1 − 0.405). Below that, PAYG's pause-ability wins. (Public guidance commonly rounds to "running >~60% of the time → reserve.")
**How it helps the verdict:** lets the agent price both the *current* and *sized-up* SKU under PAYG **and** reserved, and compute the marginal cost of one rung either way. Critically, it can warn that **reserving then optimizing strands reserved CU-hours** (use-it-or-lose-it) — so optimization recommendations should precede, or be netted against, reservation decisions.

---

## 3. Azure Retail Prices API — programmatic price lookup (the agent's pricing oracle)

**TITLE:** Azure Retail Prices REST API overview
**URL:** https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices
**Summary:** **Unauthenticated** REST API returning retail (list) prices for all Azure services incl. Microsoft Fabric, per region, for Consumption and Reservation price types. This is how the agent should fetch *live, region-correct* prices instead of hard-coding.

**Endpoint:** `https://prices.azure.com/api/retail/prices`
Preview (adds savings-plan + full meter set): `https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview`

**Working Fabric queries (verified live this session):**
```
# All Fabric consumption meters in a region:
GET https://prices.azure.com/api/retail/prices?$filter=serviceName eq 'Microsoft Fabric' and armRegionName eq 'eastus'

# Reservation prices only:
GET https://prices.azure.com/api/retail/prices?$filter=serviceName eq 'Microsoft Fabric' and armRegionName eq 'eastus' and priceType eq 'Reservation'

# Currency override:
GET https://prices.azure.com/api/retail/prices?currencyCode='EUR'&$filter=serviceName eq 'Microsoft Fabric'
```
**Key facts:**
- `serviceName eq 'Microsoft Fabric'`; `serviceId` = `DZH31767PK6Z`; `serviceFamily` = `Data`.
- The base compute meter family is **`productName: "Fabric Capacity"`**, `armSkuName: "Fabric_Capacity_CU_Hour"`, e.g. meter "Dataflows Standard Compute Capacity Usage CU" → **retailPrice 0.18, unitOfMeasure "1 Hour", type "Consumption"** (this is the $/CU/hr anchor).
- OneLake storage meters live under **`productName: "OneLake"`** (separate from compute): e.g. "OneLake Storage Cold Data Stored" = **$0.004/GB/month**, "Storage Mirroring Data Stored" = **$0.026/GB/month**. (Hot OneLake storage ≈ $0.023/GB/mo — verify per region via the same API.)
- Reservation rows carry `reservationTerm` ("1 Year" / "3 Years") and `type: "Reservation"`.
- **Filter values are case-sensitive** in 2023-01-01-preview and later.
- **All USD**; other currencies are reference only. Pagination: 1,000 records/page via `NextPageLink`.
- Response fields the agent needs: `retailPrice`, `unitPrice`, `unitOfMeasure`, `armRegionName`, `meterName`, `skuName`, `productName`, `armSkuName`, `type`, `reservationTerm`, `effectiveStartDate`.

**Python pattern (from the doc):** `requests.get(api_url, params={'$filter': query})`, loop on `json_data['NextPageLink']`.
**How it helps the verdict:** the agent calls this at runtime with the capacity's actual `armRegionName` to get exact PAYG and reserved $/CU, so every dollar figure in the recommendation is current and region-accurate — no stale constants.

---

## 4. Capacity Units math — turning an operation's CU-seconds into dollars

**TITLE:** Plan your capacity size (consumption math + 30-second timepoints)
**URL:** https://learn.microsoft.com/en-us/fabric/enterprise/plan-capacity
**Summary:** Defines how CUs map to the metrics app's evaluation windows and how to size from observed utilization.

**Exact numbers/formulas (verbatim):**
- The metrics app uses a **30-second evaluation period**. "Multiply the number of CUs in the SKU table by 30 to get the number of CUs used in 30 seconds."

| SKU | CUs | 30-second CU use |
|-----|----|------|
| F2 | 2 | 60 | F4 | 4 | 120 | F8 | 8 | 240 | F16 | 16 | 480 | F32 | 32 | 960 | F64 | 64 | 1920 | F128 | 128 | 3840 | F256 | 256 | 7680 | F512 | 512 | 15,360 | F1024 | 1024 | 30,720 | F2048 | 2048 | 61,440 |

- **CU-second / CU-hour conversion (from the throttling example):** **1 CU-hour = 3,600 CU-seconds** (1 CU × 60 min × 60 s).
- **Timepoints:** 30 s long; **2,880 timepoints per 24 h**.
- **An F2 has 60 CU-seconds per timepoint** (2 CU × 30 s); **1,200 CU-s per 10 minutes**; **48 CU-hours per 24 h**.
- Sizing workflow: trial capacity → metrics app **Utilization** visual → drill to **timepoint page** → read **SKU card** + **Capacity CU card** → scale up to cover utilization. Tool: **Fabric SKU Estimator (preview)** at https://aka.ms/FabricSKUEstimator.

**Dollar bridge (agent formula):**
```
cost_of_operation = CU_seconds / 3600 × $/CU/hr
                  = CU_seconds / 3600 × 0.18   (US East PAYG)
```
A report that burns 1 CU-hour costs **$0.18** (PAYG, US East). The metrics app reports each item's billable usage **aggregated by CUs**, so the agent can attribute $ to individual items/workspaces/users.
**How it helps the verdict:** converts the metrics-app CU figures (per item, per user, per workload) into dollars, enabling "this one notebook costs $X/month" and "optimizing it saves $Y" — the optimize side of the ledger, priced in the same currency as the size-up side.

---

## 5. Headroom % and the size-up decision threshold

**TITLE:** Plan capacity + Throttling (right-sizing guidance)
**URLs:** https://learn.microsoft.com/en-us/fabric/enterprise/plan-capacity  •  https://learn.microsoft.com/en-us/fabric/enterprise/throttling
**Summary:** Microsoft frames sizing as: keep utilization within capacity limits; **"Consistently high throttling levels indicate the need to load balance across multiple capacities or increase the capacity's SKU size."**
**Numbers/method:**
- **Headroom % = 1 − (peak/sustained smoothed CU usage ÷ SKU CU capacity).** The metrics app **Utilization** chart line at 100% = full SKU; spikes over the line = overage. **Utilization >100% does not automatically mean throttling** (smoothing absorbs it) — so headroom must be read against the **Throttling** chart, not raw spikes.
- Capacity admins can set an **email alert at 100% of provisioned CU**.
- "Minutes to burndown" in the metrics app estimates how long carryforward takes to clear with no new ops.
**How it helps the verdict:** the agent computes a credible headroom %; persistent negative headroom on the *Throttling* chart (not just spiky Utilization) is the evidence that justifies size-up. If headroom is healthy but a few items dominate CU, the evidence points to **optimize**, not size-up.

---

## 6. The F64 threshold — the biggest licensing cliff in the model

**TITLE:** Understand Microsoft Fabric Licenses (F64 free-viewing rule)
**URL:** https://learn.microsoft.com/en-us/fabric/enterprise/licenses
**Summary:** **F64 (= P1 equivalent) is the threshold at which Power BI content can be consumed by free users.**
**Exact rules (verbatim-sourced):**
- "To view Power BI content with a Microsoft Fabric **free** per user license, your capacity must reside on an **F64 or larger** SKU, and you need a **viewer role** on the workspace."
- On **F SKUs smaller than F64 (and all A SKUs)**, **each consuming user must have Pro, PPU, or an individual trial** to view Power BI content (outside My workspace).
- A **Pro license is ~$14/user/month**; **PPU ~$24/user/month** (Microsoft 365 list prices; verify current).
- Every org using Power BI in Fabric still needs **at least one Pro or PPU** to author/share.
**The cliff economics (agent must model this):**
- Below F64, viewer licensing scales with headcount: e.g. **500 viewers × $14 Pro = $7,000/mo** — which can **exceed the F64 capacity step itself** ($8,410 PAYG / ~$5,004 reserved).
- So "size up to F64" can be **cheaper than staying on F32 + buying Pro for all viewers**, flipping the usual logic. F64 is the rung where capacity cost is offset by eliminated per-user Pro costs.
- Going **F64 → F128** gains **no new licensing benefit** (free viewing already unlocked) — that jump is pure compute, judged on CU headroom alone.
**How it helps the verdict:** the agent must include **per-user license cost** on both sides of the ledger. The size-up recommendation to F64 should net out the Pro/PPU licenses it eliminates; a recommendation to drop **below** F64 must warn about the per-viewer Pro bill it would trigger. This is often the largest single dollar swing in the whole analysis.

---

## 7. PPU vs capacity (and what PPU can't do)

**TITLE:** Understand Microsoft Fabric Licenses (per-user licenses)
**URL:** https://learn.microsoft.com/en-us/fabric/enterprise/licenses
**Summary / numbers:**
- **PPU "is more cost effective when Power BI Premium features are needed for fewer than 250 users."** Above ~250 PPU users, a capacity (F64+) is usually cheaper. (Crude break-even: 250 × ~$24 PPU = ~$6,000/mo ≈ between F32-reserved and F64-reserved.)
- **PPU does NOT provision a Fabric capacity** — it cannot run non-Power-BI Fabric items (lakehouses, warehouses, notebooks, pipelines). Those always require an **F (or Trial) capacity**.
- PPU gives 48 refreshes/day, >1 GB models, XMLA endpoint — Premium features per-user, on a shared pool.
**How it helps the verdict:** for a Power-BI-only tenant under ~250 users, the agent can offer "PPU instead of/alongside capacity" as a third lever. For any tenant doing Fabric (non-PBI) workloads, PPU is ruled out and the discussion is purely F-SKU sizing.

---

## 8. Capacity Overage (3x) — the priced alternative to throttling

**TITLE:** Enable capacity overage (preview)  •  Capacity overage overview
**URLs:** https://learn.microsoft.com/en-us/fabric/enterprise/enable-capacity-overage  •  https://learn.microsoft.com/en-us/fabric/enterprise/capacity-overage-overview
**Summary:** Opt-in feature: instead of throttling when you exceed the SKU, Fabric **auto-bills the excess CU at 3× the PAYG rate**, up to an admin-set 24-hour CU limit. Prevents interruption; preview, **F16+ recommended**, F-SKU only.
**Exact numbers/formula (verbatim):**
- **"Fabric charges capacity overage at 3 times the pay-as-you-go rate."**
- Estimated cost formula: **`3 × (your $/CU/hr) × (configured CU-hour limit)`**.
- Doc's worked method: take F2 hourly price ÷ 2 (to get $/CU-hr) → × 3 (overage multiplier) → × CU-hour limit. With US East $0.18/CU/hr ⇒ **overage = $0.54/CU/hr**.
- Limit set in **multiples of 48 CUs** per 24 h (e.g., "Up to 240 CU-hours/day"). Activates within 5 minutes; pay only for CU-hours actually consumed.
**Break-even vs sizing up (agent rule):** because overage is **3× PAYG**, it's only economical for **short, infrequent spikes**. If a capacity would sit in overage **more than ~1/3 of the time**, permanently sizing up (1× the rate for the added CU) is cheaper. Concretely: paying 3× for an extra 64 CU only beats an F64→F128 step-up when the extra CU is needed **<~33%** of hours.
**How it helps the verdict:** gives the agent a *third* option between "throttle (free, painful)" and "size up (permanent 2×)": "enable overage at 3× for your occasional spikes, capped at N CU-hours/day = $Z/mo worst case." It can compute worst-case overage cost and compare to the size-up delta.

---

## 9. Pause/Resume economics

**TITLE:** Pause and resume your Fabric capacity
**URL:** https://learn.microsoft.com/en-us/fabric/enterprise/pause-resume
**Summary / exact behavior (verbatim):**
- **"When you pause your capacity, the remaining cumulative overages and smoothed operations on your capacity are summed, and added to your Azure bill."** → pausing **settles the carry-forward immediately** (you pay off smoothed future-CU debt in one billing event).
- **Compute billing stops while paused; OneLake storage billing does NOT** — "You continue to pay for storage when compute is paused" (Data Warehouse pause/resume doc).
- Pausing **instantly clears throttling** (capacity resumes with zero future-capacity usage) — a self-service un-throttle, but it makes content unavailable, so only when capacity isn't in use.
- F-SKU only; can be **scheduled via Azure runbook** or driven by REST (`/suspend`, `/resume`).
**When it makes sense (agent rule):** PAYG capacities with predictable idle windows (nights/weekends). A capacity used 8h×5d = 40/168 h ≈ **24% uptime**: pausing the other 76% cuts compute spend ~76% — and at that low duty cycle **PAYG+pause beats a reservation** (reservation needs ~59%+ uptime to win, §2). Reserved capacities gain nothing from pausing (you've prepaid the CU-hours).
**How it helps the verdict:** for low-duty-cycle dev/test capacities, the cheapest answer may be neither "optimize" nor "size up" but **"stay PAYG and pause when idle."** The agent should surface duty-cycle from usage telemetry and quantify pause savings. Caution: the pause **settles outstanding smoothed CU debt as an immediate charge** — model that one-time hit.

---

## 10. Autoscale Billing for Spark — moving Spark off the capacity meter

**TITLE:** Autoscale Billing for Spark in Microsoft Fabric (overview + configure)
**URLs:** https://learn.microsoft.com/en-us/fabric/data-engineering/autoscale-billing-for-spark-overview  •  https://learn.microsoft.com/en-us/fabric/data-engineering/configure-autoscale-billing  •  https://learn.microsoft.com/en-us/fabric/data-engineering/billing-capacity-management-for-spark
**Summary:** Opt-in, per-capacity. When ON, **Spark jobs stop consuming the shared Fabric capacity CU** and run on **dedicated serverless pay-as-you-go** compute, billed separately under meter **"Autoscale for Spark Capacity Usage CU."** F-SKU only (**F2+**); not on P-SKUs or trial.
**Exact mechanics:**
- **No bursting/smoothing** in autoscale mode (Spark is pure PAYG) — confirmed in the throttling doc ("Bursting and smoothing are not supported when... Autoscale Billing for Spark").
- Spark concurrency governed by an **admin-set max CU limit**; Spark **does not burst from or fall back to** the base capacity.
- Removes Spark spikes from the base capacity → **frees CU headroom for Power BI / interactive** workloads and isolates Spark cost.
**How it helps the verdict:** if Spark is the cause of overload on a mixed capacity, the agent can recommend **autoscale-for-Spark instead of sizing up the whole capacity** — you pay PAYG only for actual Spark seconds and leave the base SKU sized for BI. It can compare "size up F64→F128 (+$8,410/mo)" vs "keep F64, move Spark to autoscale (pay only for Spark CU-seconds)."

---

## 11. Cost Management, budgets & the bill↔usage reconciliation

**TITLE:** Understand your Fabric capacity Azure bill  •  Create and manage budgets  •  Cost alerts
**URLs:** https://learn.microsoft.com/en-us/fabric/enterprise/azure-billing  •  https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets  •  https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending
**Summary:** Fabric charges surface in **Azure Cost Management** under the subscription as **per-workload CU meters** (full list in the doc: *Power BI Usage CU*, *Data Warehouse Capacity Usage CU*, *Spark Memory Optimized Capacity Usage CU*, *Data Movement/Orchestration*, *OneLake … Operations CU*, *Copilot and AI*, *Autoscale for Spark*, etc.). "The total usage from all meters adds up to the cost of the provisioned Fabric capacity."
**Numbers/method:**
- **Budgets** trigger alerts when cost/usage hits a % of a set threshold; alert emails go to a recipient list; viewable under **Cost Management → Budgets**.
- Reconcile bill↔usage: filter Cost Management to the same window as the **Fabric Capacity Metrics app**, compare the relevant meter (e.g., Warehouse = *Data Warehouse Capacity Usage CU*). "The price per CU hour for your capacity depends on your capacity's region."
- Metrics app default view = **14-day trend by workload**; billable usage aggregated by workspace, workload type, item name, in CUs.
**How it helps the verdict:** the agent can attribute spend per **workload meter** (which workload to optimize), recommend a **budget+alert** as the guardrail after any sizing change, and reconcile its CU-based estimates against the actual Azure invoice so the dollar figures in the verdict are defensible.

---

## 12. Putting it together — the agent's "optimize vs size-up" dollar model

Inputs the agent gathers: current SKU & CU, `armRegionName`, smoothed peak/sustained CU (metrics app), duty-cycle %, per-workload CU split, viewer headcount & current license mix, PAYG-vs-reserved status.

Pull live prices (§3) → compute, all in $/month:

- **Current cost:** `current_CU × rate(PAYG or reserved)` + per-user licenses + OneLake storage.
- **Size-up delta (one rung):** `(next_CU − current_CU) × rate`. PAYG F64→F128 = **+$8,410/mo**; reserved = **+$5,004/mo**. (Always a near-doubling because the ladder doubles.)
- **Optimize value:** for each heavy item, `reclaimed_CU_seconds / 3600 × $/CU-hr` → $/month saved; sum the actionable ones → **"optimization saves $Y/mo."**
- **Overage option:** `3 × $/CU-hr × spike_CU-hours/day × 30` = worst-case $/mo for occasional spikes (cheaper than size-up only if spikes < ~1/3 of hours).
- **Pause option (PAYG only):** `(1 − duty_cycle) × current_cost` saved; flag the one-time smoothed-debt settlement.
- **Spark-autoscale option:** removes Spark CU from base; size base for BI only.
- **F64 cliff:** if dropping below F64, add `viewers × $14 Pro/mo`; if rising to F64, subtract eliminated Pro/PPU.

**Verdict rule of thumb:** recommend **optimize** when reclaimable CU (priced) ≥ the headroom deficit and is cheaper than one rung; recommend **size-up** when the deficit is structural/sustained (Throttling chart, not just Utilization spikes) or memory-bound, or when crossing the **F64 cliff** nets out cheaper than per-user licensing. Always present both numbers side by side, sourced from the live Retail Prices API for the capacity's region.

---

## Flat URL list (all sources)

- https://learn.microsoft.com/en-us/fabric/enterprise/licenses
- https://learn.microsoft.com/en-us/azure/cost-management-billing/reservations/fabric-capacity
- https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices
- https://prices.azure.com/api/retail/prices
- https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview
- https://learn.microsoft.com/en-us/fabric/enterprise/plan-capacity
- https://learn.microsoft.com/en-us/fabric/enterprise/throttling
- https://learn.microsoft.com/en-us/fabric/enterprise/enable-capacity-overage
- https://learn.microsoft.com/en-us/fabric/enterprise/capacity-overage-overview
- https://learn.microsoft.com/en-us/fabric/enterprise/pause-resume
- https://learn.microsoft.com/en-us/fabric/data-warehouse/pause-resume
- https://learn.microsoft.com/en-us/fabric/enterprise/monitor-paused-capacity
- https://learn.microsoft.com/en-us/fabric/data-engineering/autoscale-billing-for-spark-overview
- https://learn.microsoft.com/en-us/fabric/data-engineering/configure-autoscale-billing
- https://learn.microsoft.com/en-us/fabric/data-engineering/billing-capacity-management-for-spark
- https://learn.microsoft.com/en-us/fabric/enterprise/azure-billing
- https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets
- https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending
- https://learn.microsoft.com/en-us/fabric/enterprise/fabric-features
- https://learn.microsoft.com/en-us/fabric/enterprise/fabric-sku-estimator
- https://aka.ms/FabricSKUEstimator
- https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/
- https://learn.microsoft.com/en-us/fabric/enterprise/buy-subscription
- https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page
- https://www.serverlesssql.com/microsoft-fabric-reserved-pricing-and-how-to-purchase/
- https://www.synapx.com/blogs/microsoft-fabric-pricing-guide-2026/
- https://medium.com/microsoftazure/microsoft-fabric-costs-azure-cost-management-and-reservations-for-power-bi-admins-c2fed6da9cc6

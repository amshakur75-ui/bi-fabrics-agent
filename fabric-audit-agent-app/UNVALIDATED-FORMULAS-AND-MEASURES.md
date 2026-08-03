# Unvalidated Formulas and Measures — bi-fabrics-audit-agent

**Purpose:** This is the research-side companion to `GAPS-AND-ISSUES.md`. That document tracks
**code gaps** (what needs to be built or fixed). This document tracks **formula/measure gaps**
(what's still unconfirmed about how the Fabric Capacity Metrics app actually computes things).

**The critical distinction, stated up front: nothing in this file blocks the agent.** Every
formula the agent's five core features actually depend on — throttling detection, the burndown
chain, core CU% math, the concentration alert, and the optimize-vs-size-up verdict — is proven
exact against real production data, with zero error across hundreds to thousands of rows. See
`GAPS-AND-ISSUES.md` Sections 12.2–12.4 for the full proof. Everything below was discovered because
it was reachable during research, not because any current agent feature needs it resolved.

**When to come back to this file:** only if a future feature explicitly requires one of these
measures. Until then, this is a parking lot, not a blocker list.

---

## Group 5 — Health page percentile/variance measures

### Usage variance (24h)
- **What's known:** Confirmed to be a real measure (`Usage variance (last 24 hours)` in the
  Section 12.12 catalog). Official Microsoft description (Section 12.11): *"A larger value for
  this field indicates a capacity having wide variance in the amount of utilization, whereas low
  variance is indicative of a steady state utilization rate."*
- **What's missing:** The actual formula. Could be standard deviation, coefficient of variation,
  interquartile range, or something else — the qualitative description doesn't disambiguate.
- **No candidate formula has ever been tested against real data.** This is the single most open
  item in the whole project — everything else at least has a hypothesis to test.
- **Source:** GAPS-AND-ISSUES.md §12.5, §12.7, §12.11, §12.12.

### P95 interactive delay / P95 interactive rejection / P95 background rejection
- **What's known:** Confirmed to exist at **both** 1-hour and 24-hour windows (Section 12.12
  catalog — previously only the 24h versions were known). Officially confirmed **not used** in
  Health-state decisions: *"the actual values of interactive delay, interactive rejection, and
  background rejection are used, rather than their P95 values"* — purely informational.
- **What's missing:** Exact formula and the population it's computed over. A computed P95 from
  the agent's own raw series landed in the right neighborhood but didn't exactly match a Health
  page figure (57.52 shown vs. 69.10/56.79 computed at different window widths) — but that
  export was itself only ~8–10% sampled, so the mismatch may be a sampling artifact, not a wrong
  formula.
- **Community speculation (unconfirmed):** may be computed on raw unsmoothed 30-second data
  rather than the smoothed values used for throttle decisions.
- **Source:** §12.2, §12.7, §12.11, §12.12.

---

## `basecore` family — now confirmed as FOUR separate measures, not one

- **What's known:** The full measure catalog (§12.12) revealed four distinct measures, not one
  with two competing interpretations as previously framed:
  - `SKU CU by timepoint basecore`
  - `SKU CU by timepoint basecore item history`
  - `SKU CU by timepoint basecore only`
  - `SKU CU by timepoint basecore only preview`
- **What's missing:** Which of two structurally different splits `basecore` represents —
  (a) billable-vs-non-billable/preview, or (b) base-reserved-vs-autoscale-purchased capacity.
  These are genuinely different splits, not two names for the same thing, and neither has been
  tested.
- **Why untestable so far:** no autoscale activity occurred in the sample tenant during any
  session to date. `Autoscale CU usage` / `Autoscale CU usage %` are confirmed to exist as their
  own separate measure family (§12.12), which is suggestive evidence (not proof) favoring the
  base-vs-autoscale hypothesis, since Microsoft modeled autoscale as its own concept rather than
  folding it into a billable/non-billable split.
- **A real, uncontrolled natural experiment already happened once:** the test capacity
  (`entreportingfabricprd1`) resized from F1024 to F512 mid-session (§12.9) — not autoscale, but
  confirms the base formula (`base_CU × 30`) is robust to a real SKU change. If a similar resize
  or an actual autoscale event happens during a future session, comparing CU figures immediately
  before/after could help resolve this.
- **Source:** §12.2, §12.9, §12.12.

---

## Non-billable % (Background / Interactive)

- **What's known:** Formula presumed identical in shape to the billable versions (same
  denominator, filtered to non-billable ops). Confirmed to be a real, correctly-defined measure
  in the schema (both via FUAM's community DAX and this project's own catalog) — not a naming or
  pipeline defect.
- **What's missing:** A single live nonzero example. Confirmed **BLANK** (not literal `0`) across
  every sample checked — most recently 2,889 rows spanning a full 30-day window. DAX `BLANK()`
  typically means the filtered table had zero matching rows, consistent with "this genuinely
  never occurs in this tenant" rather than a pipeline gap.
- **Path to closing this:** would require finding a tenant, or a moment in this tenant, where
  non-billable (e.g., Preview-SKU or trial) activity actually occurs.
- **Source:** §12.2, §12.10, §12.11.

---

## Pass rate

- **What's known:** Confirmed as its own real measure (§12.12). Notably has **no time-window
  suffix at all** — every other measure in the catalog is suffixed `(last 1 hour)` / `(last 24
  hours)` / `(last 7 days)`, but `Pass rate` stands alone. Does not reconcile with the
  capacity-wide Success% breakdown even after correct op-count weighting — a 2.1 percentage-point
  gap survives.
- **What's missing:** The formula entirely, and what population/window it's scoped to.
- **Leading hypothesis (untested):** given the missing window suffix, `Pass rate` may not be
  time-scoped the same way as the rest of the hour/day/week family at all — it may be answering a
  structurally different question (e.g., a per-scheduled-refresh pass/fail rate) rather than just
  a differently-windowed version of the Success% figure.
- **Source:** §12.10, §12.12.

---

## Cumulative CU Usage % Preview / Cumulative CU Usage (s)

- **What's known:** Confirmed as real, distinct measures via FUAM's community DAX query — never
  independently fingerprinted by this project. Confirmed present in the full catalog too.
- **What's missing:** Everything — formula, relationship to any already-validated measure. "Preview"
  in the name is suggestive given the open non-billable/basecore questions above (both involve a
  billable-vs-preview or base-vs-autoscale distinction) but nothing confirms either connection.
- **Source:** §12.11, §12.12.

---

## Capacity-level `Throttling_s` (24h grain) — aggregation method unconfirmed

- **What's known:** Unit is **officially confirmed to genuinely be seconds** at this
  (capacity-level, Health-page) grain — *"Total throttling seconds in selected time period."* No
  ambiguity here, unlike the item-level field (see `GAPS-AND-ISSUES.md` N19, which is a
  **different, resolved** finding about a different grain).
- **What's missing:** Whether this capacity-level figure is a straight sum of item-level
  throttling durations, or computed independently from a different source entirely.
- **Source:** §12.5, §12.7, §12.11.

---

## Background Rejection %'s relationship to Background CU%

- **What's known:** Strong correlation (0.9889) with Background CU%, low error (2.49pt MAE), but
  not identical — roughly a 0.65× scaling relationship (Background Rejection averages 4.72% while
  Background CU% averages 7.22%).
- **What's missing:** Whether this is a genuine dampened/smoothed transform of Background CU%, or
  coincidental correlation on a small sample.
- **Not urgent:** regardless of the answer, the correct engineering approach (extract the field
  directly rather than derive it) is unchanged either way — this is a curiosity, not a blocker.
- **Source:** §12.4.

---

## How to make progress on any of these, if it's ever worth doing

In rough order of effort, cross-referenced to where these were discussed in full:

1. **Composite-model-owned table extraction** (untested as of this writing) — the
   composite-model-ownership trick (GAPS-AND-ISSUES.md §12.12, D1) was proven to NOT reveal
   measure formulas, but its actual demonstrated value is **raw table extraction bypassing UI
   export sampling.** If Usage variance / P95 / Pass rate are physical columns on a queryable
   table (plausible, since they're materialized on the Health-page comparison table) rather than
   live-recomputed measures, this path could pull them at full fidelity — no sampling — and let
   you fingerprint candidate formulas against your own complete Eventhouse series for the exact
   same window. This has never actually been attempted, only reasoned through.
2. **Query the `MetricsByItemandOperationandHour` table** — a real, confirmed table name (via a
   Fabric Community forum post, not yet queried by this project) sitting at exactly the grain
   (hourly) that would sidestep the 30-second-grain sampling problem for anything scoped to
   "last 24 hours."
3. **Broader GitHub code search** beyond FUAM specifically, for the literal measure names
   (`"Usage variance"`, `"Pass rate"`, `"Cumulative CU Usage"`) as exact strings — FUAM was a hit
   once; other community tools may have solved pieces of this too.
4. **SQL Server Profiler trace** — captures xmSQL (storage-engine) queries as the app's visuals
   render, which could reveal a measure's actual source columns even without its DAX text. Never
   attempted (Tier 2, deprioritized).
5. **`scripts/extract_measures.py`** — written, ready, blocked only by needing a database-listing
   step (M1 in GAPS-AND-ISSUES.md). Would need Option 1's access wall solved first anyway (now
   confirmed closed), so this is lower priority than it once was.
6. **Wait for a real autoscale event** — the only way `basecore`'s two hypotheses get resolved
   with certainty. Can't be forced; watch for it if it happens naturally.

---

## Summary table

| Measure | Formula known? | Live example exists? | Blocks agent? |
|---|---|---|---|
| Usage variance | ❌ No | ✅ Yes (many) | No |
| P95 interactive delay/rejection, background rejection | ❌ No | ✅ Yes (many) | No |
| `basecore` (×4 measures) | ⚠️ Two competing hypotheses | ❌ No autoscale example | No |
| Non-billable % | ⚠️ Presumed, untested | ❌ Always BLANK | No |
| Pass rate | ❌ No | ✅ Yes (but doesn't reconcile) | No |
| Cumulative CU Usage % Preview / (s) | ❌ No | ✅ Yes | No |
| Capacity-level Throttling_s aggregation | ⚠️ Unit confirmed, method unknown | ✅ Yes | No |
| Background Rejection % vs. Background CU% relationship | ⚠️ Strong correlation, not identical | ✅ Yes | No |

**Everything the agent needs is already validated.** This file exists so none of the above gets
re-investigated from scratch in a future session, and so it's clear at a glance that "still open"
here means "still open in the research," not "still open in what the agent requires to work."

---

## Addendum (2026-07-29) — Systematic cross-reference against the full ~286-measure catalog

A programmatic cross-reference of every measure in `MEASURE-CATALOG-RAW.md` against this file and
`GAPS-AND-ISSUES.md` found that **131 of ~286 measures were not referenced anywhere** — not a
manual spot-check, an actual string-match pass across both documents. 55 of those are confirmed
presentational (Group 1 — titles, colors, icons, dynamic text, correctly out of scope since this
project started). The remaining ~76 are real data measures that had never been individually
surfaced. Grouped below by family, since most are variations on a theme rather than 76 isolated
unknowns.

### New family: "Dynamic M1" — a full second measure set, ~20 measures, never previously characterized

`Dynamic M1 % of base capacity`, `CU autoscale`, `CU preview`, `cumulative utilization (GB) by
date/workspace`, `duration autoscale/preview`, `failed/inprogress/invalid/rejected/successful
operation count` (each with a `workload autoscale` variant too), `memory preview`, `perf preview`,
`user autoscale`, `users preview`, `utilization (GB)` — plus `Dynamic metric item 1 preview` and
`Dynamic metric item 2 autoscale`, which strongly suggest "M1" means **Metric Item 1**, i.e. this
is one half of a two-item comparison feature (a "vs." view), with the other half presumably an
unseen "M2" family. Given the `autoscale`/`workload autoscale` naming throughout, this is very
likely tied to the **"Autoscale compute for Spark"** tab noticed early in this project's UI
exploration but never actually opened. **This is the single largest unexplored territory this
cross-reference found** — worth a dedicated look if autoscale/Spark workloads ever become relevant
to this agent's scope.

### New family: "Last carry over" — point-in-time, distinct from the validated cumulative recursion

`Last carry over add` / `add %` / `burdown` (sic, note the typo in the real measure name) /
`burndown %` / `t2` variants of each, plus `Last cumulative carry over` (+ `%`, + `t2` variants)
and `Last expected burndown in minutes` (+ `t2`). Twelve measures. The validated Section 12.3
formula (`Cumulative[T] = Cumulative[T-1] + Add[T-1] + Burndown[T-1]`) is a **running** recursion.
This "Last" family is very likely the **most-recent single window's value** rather than the
running total — plausible, untested. Low priority unless a future feature needs "what was the most
recent single carry-forward event" rather than the cumulative picture the agent already has.

### New family: SKU / autoscale tracking — directly relevant to the still-open `basecore` question

`Timepoint SKU`, `Timepoint SKU autoscale capacity units`, `Timepoint SKU base capacity units`,
`Timepoint SKU CU carry over` (+ `t2`), `Timepoint workload autoscale limit`, `Timepoint2 SKU auto
scale capacity units`, `Timepoint2 SKU base capacity units`, `Timepoint2 workload autoscale
limit`, `Max SKU by SKU name`, `SKU CU carry over`, `SKU CU by timepoint autoscale only`, `SKU CU
by timepoint card preview` (+ `2`), `SKU CU by timepoint short % preview` (+ `(2)`), `SKU CU by
timepoint short preview` (+ `(2)`), `Max CU limit`, `Max CU limit 1 minute`, `Max CU or autoscale
limit`, `SKU check` (boolean). ~18 measures. **This family directly overlaps the still-open
`basecore` question** — the presence of both `...autoscale capacity units` and `...base capacity
units` as separate, real, named fields is further evidence (not proof) for the base-vs-autoscale
split hypothesis over the billable-vs-preview one, strengthening what §12.12 already noted from
the separate `Autoscale CU usage` measures.

### Storage / OneLake family — more specific than §12.12's generic mention

`Avg cumulative static storage daily/hourly`, `Billed (GB) %`, `Current storage in (GB)`,
`Cumulative utilization (GB) % by workspace`, `Utilization (GB) % workspace 1`, `Total number of
workspaces`, `Workspace virtualization status`, `Item virtualization status`. §12.12 flagged this
category existed; this cross-reference confirms the specific measure list. Still no formula, no
lead — genuinely new territory, not yet worth chasing unless a future feature touches storage.

### Worth flagging on its own: `Total CU (s) operation` / `Total CU (s) operation detail`

These two measure names are the single most consequential item in this whole cross-reference,
given this project's ongoing true-CU/proxy distinction (see `GAPS-AND-ISSUES.md` §12.2 and the
Phase 8 chart-generation design). Their naming strongly suggests they could be the **true,
capacity-level, per-operation CU-seconds figures** — as opposed to the `CpuTimeMs`-based proxy the
agent currently surfaces per-operation. If these were ever accessible, they could be the missing
piece for resolving N13's open `XmlaRequestId`↔`capacityThrottlingMs` join question. They remain,
like everything else in this catalog, only names — still behind `EXTERNALMEASURE`, no formula
access. Not actionable today, but worth remembering as the single most relevant lead if the
true-per-operation-CU question ever gets revisited.

### Same validated formula, different window — low risk, just noting the gap in coverage

`Average utilization (last 1 hour)` and `(last 7 days)` (only the 24h version was previously
discussed); `Risk status (last 1 hour)` (only 24h's full 10-state machine is confirmed); and
1-hour-window variants of the status-count family (`Cancelled/Failed/Inprogress/Interactive
delayed/Interactive rejected/Successful operations (last 1 hour)`) that were previously only
confirmed at the 24-hour window. Same formulas presumed, untested at these specific windows —
low risk, trivial to note rather than chase.

### Low-value / housekeeping

`Measure 2`, `Measure 3`, `MeasureSystemEvent`, `Metric description preview` (very likely an
AI-generated measure-description field, per the Copilot-description-generation feature found via
`semantic-link-labs` GitHub discussion #383 during the composite-model research — not relevant to
this agent), `Count of capacities`, `Total operation counts`, `Capacity name (drill through
pages)`, `Pause Resume State Change` (+ `2`, already generically acknowledged in §12.12),
`xBackground item history` / `xInteractive item history` (unexplained `x`-prefix naming,
purpose unclear, low priority).

**None of the above changes this file's core conclusion:** nothing found in this cross-reference
blocks any current agent feature. It does mean the true scope of "what Microsoft's schema
contains" is larger than this project had previously characterized — recorded here so a future
session that stumbles on any of these names doesn't have to re-derive what's already been sorted.


# bi-fabrics-audit-agent — Gaps, Issues, and Problems Master List

Last updated: 2026-07-27 (post formula-validation session + Item History tab export pass + external FUAM/docs validation pass); reopened C2 + added N22/N23 on 2026-07-29 (live-transcript pass); D1 Option 1 confirmed closed + full measure catalog added 2026-07-29 (composite-model pass); Phase 1 low-risk batch implemented 2026-07-29 (A1/A2/B5/N17/N10/N7/N3/A3/N22 fixed or confirmed done; N18 confirmed real and more nuanced; N19/N21 confirmed moot)

This document is the authoritative record of every known gap, bug, behavioral problem, and missing
feature across the `fabric-audit-agent-py` and `fabric-audit-agent-app` codebases. Grouped by type.
Nothing omitted.

**This revision adds Section 12** — a full accounting of a live formula-validation session run
directly against the Fabric Capacity Metrics app (DAX Studio schema dump + real Excel exports from
the Compute/Throttling/Overages/Multi-metric-ribbon/Health tabs, across 2 regions and 5 capacities).
That session verified or disproved every formula the agent depends on using real production data,
not assumptions from Microsoft's docs. It also recovered three gaps (A3, E1, B5) that were
identified earlier in this project's history but had fallen out of this document, and added one
new gap (N5).

**A second integration pass then pulled in a July 21 chat** (predating the formula-validation
session by six days, and predating every audit pass on this document) that had never been folded
in. That chat added: five new code gaps (N11–N15 — ad-hoc-query gate bypass, missing query
provenance, missing startup health probes, missing runtime metric-type stamper, and a possibly-
still-open tool-loop duplication), two new system prompt rules (SP6, SP7 — inline inferred/derived
labeling and verbatim query quoting), a concrete second example plus proposed implementation for
B4's math-consistency check, four more golden eval case candidates (EV3), a full stress-test
question bank (Section 14), and a confirmed cleanup item (D4 — delete the dead Node.js reference
app once the current build is complete).

Read Section 12 before touching `kb/metric_definitions.py`, `collector_capacity_events.py`, or
`detectors/concentration.py`.

**A third pass (same day) worked through a full Item History tab export set** (8 files: Smoothed
CU % over time at 30-second grain across a full 30-day window, CU-by-item, CU-by-workspace, daily
operations/CU trends, workspace pass-rate, and item×status throttling detail). This pass found
four new structural gaps (N16–N19 — a second independent confirmation of the ×100 fraction-scale
defect class A1 already fixed, a broken Date-dimension join on `Monitoring_Eventstream`, item
display names colliding across workspaces, and a near-certain unit mislabeling on item-level
`Throttling(s)`), plus a full set of session findings in new Section 12.10 (sampling behavior that
varies by grain within the same app, a naming trap on `v__compute_operation`, small-but-real
cross-dimension total reconciliation gaps, and a Pass-rate/Success% discrepancy that remains
unexplained). See Section 12.10 before touching anything that reads Item History exports or
item-level throttling data.

**A fourth pass (same day) went outside the app entirely** — searched the public
`microsoft/fabric-toolbox` GitHub repo (FUAM's source) and the official `fabric-docs` GitHub repo
(source behind learn.microsoft.com) rather than more UI exports. This resolved several
long-standing open questions with authoritative sources instead of behavioral fingerprinting:
real base-model DAX measure names (via FUAM's own community-debugged queries, confirming
measures we'd fingerprinted plus two we'd never seen — `Cumulative CU Usage % Preview` and
`Cumulative CU Usage (s)`), the complete Health-page state machine (10 states, exact thresholds,
resolving Section 12.7's "only 2 of 4+ levels seen" gap), confirmation that the three throttle
types use three DIFFERENT smoothing windows (10min/60min/24h — new gap **N20**), official
qualitative descriptions for `Usage variance` and P95 fields, and a third independent status
taxonomy (new gap **N21**). See Section 12.11.

**A fifth pass (2026-07-29) read the live deployed app file directly** (`agent_server/agent.py`)
after the person uploaded two real production transcripts showing inconsistent behavior for the
same conceptual question asked in different chat sessions. This found something serious: **C2
(system prompt duplication), long marked FIXED in Section 11, is NOT actually fixed in
production** — the deployed app is still running its own hardcoded, diverged copy of the system
prompt, confirmed live via the retired combined "94.7% (947.1%)" format still appearing in real
output, even though the canonical `system_prompt.py` file already retired that exact format
earlier this same day (SP4). This means every SP1–SP7 fix made in the canonical file this session
has had zero effect on the actual deployed agent until this is corrected — see the reopened C2
entry in Section 1, now the top priority item. The same file read also resolved N15's
"unverified" status: the tool loop is confirmed still a separate inline implementation, not
delegated to the package. Two more new gaps came out of the same transcripts: N22 (a hidden,
keyword-based step-budget classifier that silently varies investigation depth per exact question
phrasing, with zero disclosure to the user) and N23 (a confirmed, reproducible date-filter bug
in the capacity-overloads/spike tool — two independent transcripts show 1-day and 20-day
spillover on a single-day request).

**A sixth pass (2026-07-29) tested the composite-model-ownership trick directly** — built and
owned a new local model in Power BI Desktop off a live connection to the Capacity Metrics app,
then ran `TMSCHEMA_MEASURES` against it via DAX Studio. **Confirmed negative result: still
`EXTERNALMEASURE` on every measure.** This definitively closes Option 1 from Section 6 (D1) — the
protection is enforced at the base-model level itself, not by connection path, and owning a new
composite shell doesn't bypass it. The attempt was not wasted, though: it produced the fullest
measure catalog this project has ever obtained (~230 real names with real data types, no formula
text). See the new raw reference file `MEASURE-CATALOG-RAW.md` (repo root) and Section 12.12 for
curated findings — including a third independent confirmation that throttling duration is
minutes not seconds, proof that `basecore` is actually four separate measures rather than one
with two hypotheses, and confirmation that `Autoscale CU usage` exists as its own distinct
measure family.

**A seventh pass (2026-07-29) implemented Phase 1's low-risk batch directly** (tasks not needing
live test execution — Claude Code owns the rest of Phase 1, per the agreed division of labor).
Direct code verification, not assumption, drove every change. Fixed/confirmed in this pass: **A1**
(confirmed fully implemented — extraction, ×100 scaling, and `throttle.py` consumption all
verified), **A2** (extraction already existed; found and fixed the missing piece — `diagnose.py`
never actually called `capacity_burndown_chain()`, so SP1's auto-trigger couldn't fire; a new
`burndown_chain_from_series()` helper now wires this in), **B5** (confirmed the `ClaimConfidence`
enum already exists as specified), **N17** (confirmed CLOSED — Layer A never joins a Date table,
immune by construction), **N10** (misleading message fixed), **N7** (`attributionMode` now splits
`cost-cpu`/`cost-duration`), **N3** (fixed in the same pass as N7, since N7's mode-string change
would have broken N3's existing check), **A3** (`truncated` flag added), **N22** (disclosure
added, refined to fire only when the shallow budget is actually exhausted). **N16** partially
addressed (the "trustworthy" formula path was already safe by construction; the weaker
`reportedPeakPct` path is now documented in-code as diagnostic-only). **N18** confirmed real and
more nuanced than known (one file's grouping key is safe, another's isn't) — left for Claude Code
since the fix needs upstream tracing. **N19/N21** confirmed to close themselves (nothing in the
codebase currently does either thing). **N1** re-verified unchanged, still correctly cautious.

---

## SECTION 1 — CODE GAPS

### A1 — Throttle threshold fields never extracted — stage-2 gate permanently dead
**File:** `fabric_audit_agent/adapters/collector_capacity_events.py`

`_windows()` builds only `{cap, ts, pct}`. The three fields
`interactiveDelayThresholdPercentage`, `interactiveRejectionThresholdPercentage`,
`backgroundRejectionThresholdPercentage` are never extracted from the event payload.
`capacity_series()` only returns `{ts, cuPct}`. `throttle.py` stage-2 always hits
"throttling-signal series not collected" — permanently outputs `"over-utilized-unconfirmed"`.

Secondary: `diagnose.py` and `gates.py` are inconsistent — `gates.py` `throttle_claim_gate()`
uses `throttleMinutes > 0` (works); `diagnose.py` calls `decompose_throttle()` which goes through
the dead stage-2 (never confirms).

**Fix:** Extend `_windows()` to extract the three threshold fields and pass through
`capacity_series()`. ~20 lines. Single highest-value change in the codebase.

**Critical implementation detail confirmed by re-reading `throttle.py` directly:** the stage-2
signal-fired check is `max(vals) > 100.0` — i.e. it expects values already in **percentage
points** (123.71 for a 123.71% reading). The raw API field, confirmed via fingerprinting in
Section 12.4, is a **fraction** (1.237113). Whoever implements this fix must multiply the
extracted value by 100 during extraction, or the gate will silently never fire — a raw fraction
would need to exceed 10,000% to trip a `> 100.0` check.

---

### A2 — Burndown chain missing — overage fields never extracted
**Files:** `collector_capacity_events.py`, `investigation/diagnose.py`

`overageAddCapacityUnitMs`, `overageBurndownCapacityUnitMs`, `overageTotalCapacityUnitMs` never
extracted. No `capacity_burndown_chain()` function exists. `throttle.py` has a `minutesToBurndown`
passthrough but `capacity_series()` never produces it — dead code.

Field names confirmed from DAX schema session. Maps to:
- `Timepoint Overage Detail[Carry forward add]`
- `Timepoint Overage Detail[Carry forward burndown]`
- `Timepoint Overage Detail[Cumulative carry forward]`

Formula: `overageTotalPct = cumulative sum of (overageAdd − overageBurndown) / (baseCapacityUnits × 30) × 100`

**Fix:** Extend `_windows()` (same pass as A1) to extract the three overage fields. Add
`capacity_burndown_chain()`. Auto-call from `diagnose.py` when `timepointsOver > 0`. ~60 lines.

**Open design question confirmed by re-reading `throttle.py` directly:** its comment says
"Burndown passthrough — the Metrics app's OWN figure, verbatim, never re-derived," implying the
intended design pulls a pre-computed `minutesToBurndown` value directly from the event payload
rather than recomputing it. **Unconfirmed:** whether the actual Capacity Overview Events stream
includes a pre-computed equivalent field at all, or whether the agent genuinely must derive it
using the `Cumulative% / 200` formula proven in Section 12.3. Also confirmed: `diagnose.py`
currently has **no call to any burndown-chain function anywhere** (checked `diagnose_throttle()`,
`diagnose_refresh()`, `diagnose_slowness()` directly) — this gap's description remains accurate.

---

### B2 — Blank `ExecutingUser` partially handled, no cross-reference fallback
**File:** `adapters/collector_workspace_monitoring.py`

WM KQL now tries the `Identity` field and filters blank rows — handles the WM-specific case. No
fallback to Activity Events cross-reference on `ItemId` + timestamp window. No fallback to item
owner via `configuredBy` from the REST API when both sources are blank.

---

### B3 — `kb/metric_definitions.py` does not exist
No grounding table mapping metric names to formula, source column, metric type (true CU / proxy /
count), and smoothing window. Minimum entries:

- `sku_cu_pct` — `SUM(CU_s) / (base_capacity_units * 30) * 100`, source: `CU Detail[CU(s)]`, true_CU, 30s
- `cumulative_carry_forward_pct` — `SUM(Cumulative_carry_forward) / (base * 30) * 100`, source: `Timepoint Overage Detail`, true_CU, rolling
- `user_cpu_share_pct` — `SUM(user.CpuTimeMs) / SUM(all.CpuTimeMs) * 100`, source: `SemanticModelLogs`, proxy_cpu, query window
- Throttle threshold field mappings
- Carry-forward field mappings

Agent needs this to cite what it's computing and why. ~50 lines.

---

### B4 — Math consistency check missing
**File:** `fabric_audit_agent/validate.py`

`validate.py` is shape-only — checks required keys and array types, nothing else. No arithmetic
verification. The arithmetic error class (formula stated correctly, computed result wrong by 34×) is
still possible. No `assert_cu_source_consistency()`. ~20 lines.

**A second, distinct real example confirmed in the July 21 chat** (separate from OB7's 17%-vs-0.5%
case): the agent reported `105.1% CU%` alongside `overageAdd = 786K CU-ms` in the same response.
Those don't reconcile — 105.1% implies roughly 1,567,000 CU-ms of overage at F1024, but the
overage field showed only 786K, which itself implies closer to 102.56%. Two different denominators
quietly mixed in one table. **Proposed implementation from that session, never yet written:**
```python
def assert_cu_consistency(cu_pct, overage_add_ms, base_cu, window_s=30):
    implied_pct = (overage_add_ms / (base_cu * 1000 * window_s)) * 100
    expected_overage = (cu_pct - 100) / 100 * base_cu * 1000 * window_s
    if abs(implied_pct - (cu_pct - 100)) > 1.0:  # >1% discrepancy = different sources
        raise InconsistentSourcesError(f"CU% implies {expected_overage:.0f} overage, got {overage_add_ms}")
```
If this fires, the agent must pick one source and recompute the other, not report both
side-by-side unreconciled.

---

### B5 — No `ClaimConfidence` enum confirmed in `confidence.py`
**File:** `fabric_audit_agent/confidence.py`
**Status:** identified early in this conversation thread (pre-formula-validation session), never
resolved, dropped from the file when it was first created — re-added here

Original gap: no formal `CONFIRMED` / `LIKELY` / `INCONCLUSIVE` enum enforced in code — confidence
labels were determined by the LLM's own judgement, not gated by what fields were actually present
in the data. Fix proposed: add a `ClaimConfidence` enum to `confidence.py` with a
`claim_confidence()` function that checks which fields were actually present before assigning a
label.

**Status is genuinely unclear, not confirmed either way.** `confidence.py` was read during the
live code-verification pass in this conversation, and `gates.py`'s five deterministic STOP gates
(see Section 11) cover a related but not identical concern — the *gates* block specific claims
from being made at all when data is missing, but that's different from whether `confidence.py`
itself formally enumerates `CONFIRMED`/`LIKELY`/`INCONCLUSIVE` as a typed value versus a loose
string. This was never explicitly re-confirmed. **Directly relevant to SP2** (the "validated"
label precision rule) — if `ClaimConfidence` doesn't exist as real code, SP2's rule can only be
enforced by prompt instruction, which is weaker than a code-level gate.

**Fix:** Actually re-check `confidence.py`'s current contents against the original ask before
assuming this is resolved by the gates architecture alone.

---

### N1 — Workspace Monitoring event seam not wired to tools
**File:** `fabric_audit_agent/sources.py`
**Correction after re-reading `sources.py` directly — this is DELIBERATE, not an oversight, and the fix must be careful**

`sources.py` documents: `"the event seam (tools.py::_resolve_event_sources /
_has_live_event_source) is LA-only"`. WM correctly feeds `userAttribution` (items/users rollup) but
per-query events from WM do not flow through the event tools. WM-only configuration leaves event
depth dark — no per-query ranking, no top operations.

**Important correction:** the file's own comment reveals this is withheld **on purpose**, to
prevent a specific, previously-fixed bug: *"Claiming eventDepth here would let the Tier-2 branch
fire on WM-only env and label fixture/mock events 'perQuery' with hasRealCost=True (final review
F1, 2026-07-07)."* In other words, someone already tried wiring this up, it caused mock/fixture
events to get mislabeled as real per-query cost data, and the fix at the time was to withhold the
capability claim rather than fix the underlying labeling bug. **Simply "wiring it up" to close N1
could resurrect that exact bug.** Any fix must first re-verify why Tier-2 could fire on mock data
in a WM-only environment and close that root cause, not just remove the withholding.

**Also found:** `sources.py`'s user-facing degraded-capability note is inconsistent with this
withholding. It reads: *"per-query cost unavailable — enable Log Analytics or **Workspace
Monitoring** for per-query depth"* — but the descriptor two dozen lines above deliberately does
**not** grant WM the `eventDepth` capability. A user who enables WM expecting this message to
resolve their per-query depth problem will find it doesn't, because the capability is
intentionally withheld elsewhere in the same file. Fix this messaging as part of N1, or
separately if N1 itself stays open for a while.

---

### N3 — `concentration.py` wrong default label, and incomplete for `"frequency"` mode
**File:** `fabric_audit_agent/detectors/concentration.py`
**Updated after re-reading `attribution.py` directly — the original write-up only covered half the problem**

```python
label = "monitored CU" if it.get("attributionMode") == "cost" else "capacity CU"
```

Two distinct problems with this line, not one:

1. When `attributionMode` is `None` or missing, defaults to `"capacity CU"` (the authoritative
   label) even when source is unknown. Should default to `"monitored CU"`.
2. **`attribution.py` (`attribute_users()`) can set `attributionMode` to a real third value,
   `"frequency"`** — used when no `cpuMs`/`durationMs` data exists at all (e.g. Activity-Events-
   only, ranking by raw operation count). This is a genuinely weaker signal than cost-based
   attribution — it doesn't even have a cost/CU number behind it, just op counts — yet the
   `== "cost"` check above sends it down the same path as the None/missing case and labels it
   `"capacity CU"`, the *most* authoritative label, for the *least* grounded attribution mode.

**Fix:** the check should resolve to `"monitored CU"` for both `attributionMode in (None,
"frequency")`, and ideally `"frequency"` mode should get its own even-more-hedged wording (e.g.
"by operation count") rather than being folded into the same "monitored CU" bucket as genuine
cost-based proxies.

---

### N5 — Concentration detector has no item-kind filter, will false-positive on system accounts
**File:** `fabric_audit_agent/detectors/concentration.py`
**Discovered:** formula-validation session, Multi-metric ribbon chart (all 4 tabs: Users, Operations, Duration, CU)

Real 14-day tenant data confirms three item kinds are driven by exactly one system/service
identity, every single hour, with zero variation — confirmed across FOUR independent signals,
not just one:

| Item Kind | Users/hr | Ops/hr | Avg duration/op | CU-intensity (CU-s per duration-sec) |
|---|---|---|---|---|
| `EventStream` | always exactly 1 | 47.89 | 10.0 min | 0.018 (near-zero — mostly idle) |
| `FabricEvents-CapacityUtilizationEvents` | always exactly 1 | 5.97 | 0.5 sec | 1.60 |
| `Activator` | ~1 (max 2) | 10.02 | 42.0 min | 0.022 (near-zero — mostly idle) |
| `Dataset` (contrast) | 24.07 (up to 91) | 12,170.01 (up to 47,052) | 0.02 min (1.3s) | 41.48 (genuinely CPU-hungry) |

If the concentration detector ever runs against `EventStream`/`Activator`/`FabricEvents-...`,
"1 user = 100% concentration" fires trivially and permanently — a structurally meaningless alert,
not a real finding. The Duration and CU tab data make this even clearer: Activator and EventStream
run for a very long wall-clock time per operation (42 min / 10 min average) but consume almost no
actual CU while doing so — the signature of a persistent listener/trigger process sitting idle
between events, not a compute-heavy workload. `FabricEvents-CapacityUtilizationEvents` is brief
(0.5s/op) but moderately CPU-intensive each time it fires — consistent with a lightweight,
frequent system tick.

By contrast, `Dataset` shows genuine multi-user concentration behavior worth alerting on (mean 24
users/hr, up to 91/hr), dominates real operation volume (12,170 ops/hr average, up to 47,052 in a
single hour — ~506 operations per user per hour), and is genuinely CPU-intensive while running
(41.48 CU-seconds per second of duration) — consistent with the heavy automated/scheduled
querying already observed throughout this session (XMLA Read Operations, repeated queries from
the same handful of accounts).

**Fix:** Exclude `EventStream`, `FabricEvents-CapacityUtilizationEvents`, and `Activator` from
the 30% concentration alert's candidate pool before computing shares. Restrict concentration
logic to item kinds with real, variable multi-user activity (`Dataset`, `Report`,
`PaginatedReport`, `KustoEventHouse` in this tenant's case — but derive the exclusion
programmatically, e.g. from user-count variance AND low CU-per-duration-sec, rather than
hardcoding a tenant-specific list). This exclusion logic should also be considered for any future
duration-based or CU-based per-item-kind ranking/alert, not just the concentration detector —
the same three item kinds would distort a "longest average operation" or similar ranking too.

---

### A3 — `workspace_monitoring` deliberately underpowered on `eventDepth`, no truncation signal
**File:** likely `adapters/collector_workspace_monitoring.py` or its config
**Status:** identified early in this conversation thread (pre-formula-validation session), never resolved, dropped from the file when it was first created — re-added here

`eventDepth` is set low intentionally in the WM collector, but this means the collector can
return incomplete/truncated data with no signal to the agent (or a human reading its output)
that the result was cut off rather than complete.

**Fix:** Either raise `eventDepth` to a safer default, or — preferably — add an explicit warning
flag in the collector's return payload when the result set is at the depth limit, so downstream
logic (and the agent's own responses) can say "showing top N of possibly more" rather than
implying completeness.

---

### E1 — Concentration alert threshold may be applied to the wrong denominator
**File:** `fabric_audit_agent/detectors/concentration.py`
**Status:** identified early in this conversation thread (pre-formula-validation session), never resolved, dropped from the file when it was first created — re-added here

The 30% concentration threshold should be applied to a user's share of total `CpuTimeMs` (the
Workspace Monitoring proxy metric) within the analysis window — not to a raw CU number, and not
mixed with the true-CU `capacityUnitMs` figures from the Capacity Overview Events stream. No code
currently enforces that the numerator and denominator in the concentration ratio come from the
same source/metric type. If a raw CU figure and a CpuTimeMs share ever get compared or combined
without this check, the resulting percentage is meaningless even if each individual number is
correct on its own.

**Fix:** Add an explicit assertion/check in the concentration detector that numerator and
denominator are drawn from the same `attributionMode` (see N3, which is a related but distinct
issue — N3 is about the *label* defaulting wrong; E1 is about whether the underlying *math* itself
could silently mix sources). Related to B3/B4 (metric definitions + math consistency check) — all
three should be implemented together since they share the same root concern: don't let figures
from different sources get compared or combined without an explicit compatibility check.

---

### N6 — `user_concentration.py` has no item-kind awareness, same system-account risk as N5, one layer up
**File:** `fabric_audit_agent/detectors/user_concentration.py`
**Discovered:** direct re-read of this file during a later audit pass of this document

Confirmed via source: this is the actual USER-level half of the concentration alert
(`concentration.py` is the ITEM-level half — the docstring explicitly calls them "complements").
It operates purely on `facts["users"]`, ranking by a CPU-proxy share (or an estimated capacity-
share when `peakCuPct` is known), with **zero visibility into item kind** — nothing in this file
ever looks at what kind of item a user's activity came from.

N5 confirmed that `EventStream`, `Activator`, and `FabricEvents-CapacityUtilizationEvents` are
each driven by exactly one system/service identity, every hour, all day. If that same identity
shows up in `facts["users"]` — which it plausibly would, since these item kinds have real (if
low) CU and operation volume — this detector has no way to distinguish it from a genuine heavy
human user, and could name a system account in its `capacity.user-concentration` flag or its
top-3 "no single user over threshold" fallback ranking. Same false-signal risk as N5, at the user
level instead of the item level.

**One good thing confirmed while reading this file:** its docstring already correctly documents
the proxy-vs-authoritative distinction ("`sharePct` is a CPU-proxy share of monitored CU ... not
the authoritative capacity CU%") — independent confirmation that C1's fix is comprehensive
across both concentration-related files, not just `concentration.py`.

**Fix:** either exclude the same system item kinds at the source (in whatever builds
`facts["users"]`), or have this detector cross-reference each user against the item-kind
exclusion list from N5 before ranking/flagging.

---

### N7 — `attributionMode: "cost"` is hardcoded and hides whether the number is true CPU time or a duration fallback
**File:** `fabric_audit_agent/adapters/attribution_rollup.py`
**Discovered:** direct re-read of this file during a later audit pass of this document

`rollup_attribution()` unconditionally sets `"attributionMode": "cost"` on every item it produces
— there is no code path that produces anything else from this file. But the file's own docstring
admits the underlying number isn't always true CPU time:

> "the cost column is `CpuTimeMs` when present, else `DurationMs` (a wall-clock proxy) — the live
> `SemanticModelLogs` table seen in the field did not expose `CpuTimeMs`."

In other words: **in production right now, "cost" mode is very likely running on `DurationMs`
(wall-clock time), not `CpuTimeMs` (actual CPU time) — and there is currently no way to tell which
one produced a given item's number just by looking at `attributionMode`.** Both cases get the
identical `"cost"` label and, downstream, the identical `"monitored CU"` wording — which slightly
overstates how grounded the figure is when it's actually a duration proxy rather than a CPU-time
proxy.

**Fix:** split `attributionMode` into something like `"cost-cpu"` vs `"cost-duration"` (or add a
separate `costBasis` field alongside the existing mode), and reflect the distinction in
`kb/metric_definitions.py` (B3) so the agent can be precise about which proxy underlies a given
number rather than treating all "cost" mode figures as equally grounded.

---

### N8 — A THIRD independent concentration-style check exists, inline in `diagnose.py`, hardcoded and item-kind-blind
**File:** `fabric_audit_agent/investigation/diagnose.py`
**Discovered:** direct re-read of this file during a later audit pass of this document

`diagnose_slowness()` computes its own "hot item" and "hot user" shares directly from raw events
(`totals[item] += e.get("cuSeconds")`, then `hot_share = hot_cu / grand_total * 100.0`), completely
separate from the `concentration.py` and `user_concentration.py` detectors covered by N5/N6. This
is now the **third** independent place in the codebase implementing 30%-style concentration logic,
and it has two problems of its own:

1. **Same item-kind blindness as N5/N6** — sums `cuSeconds` per item/user with zero awareness of
   item kind, so a system account driving `EventStream`/`Activator` could just as easily dominate
   "hot item" or "hot user" share here too.
2. **Hardcoded threshold, doesn't read config at all** — the check is a literal `> 30.0` in the
   source, not `config["capacity"]["concentrationPct"]`. If an admin changes the configured
   concentration threshold expecting it to apply tenant-wide, this code path won't notice.

**Fix:** route this through the same item-kind exclusion and config value as N5/N6/N9, rather
than maintaining a third separate implementation. Ideally, `diagnose_slowness()` should call the
shared detector logic instead of re-deriving hot-item/hot-user shares itself.

---

### N9 — A FOURTH independent copy of the 30% threshold, hardcoded as a module constant in `gates.py`
**File:** `fabric_audit_agent/investigation/gates.py`
**Discovered:** direct re-read of this file during a later audit pass of this document

```python
CONCENTRATION_THRESHOLD_PCT = 30.0
```

This is a module-level constant, used as the default threshold for `concentration_gate()`, and it
is independent from `config["capacity"]["concentrationPct"]` (used by the detectors) and from
`diagnose.py`'s own hardcoded `30.0` (N8). **Between config, diagnose.py, and gates.py, there are
now three or four separate literal definitions of the "30%" concentration threshold** with no
mechanism keeping them in sync. Changing one without the others is a real, easy-to-make mistake
that would produce inconsistent concentration behavior across different parts of the agent
(detector output vs. gate-checked claims vs. diagnose.py's inline slowness check).

**Fix:** pick one source of truth (`config["capacity"]["concentrationPct"]` is the natural choice
since it's already the one exposed for configuration) and have `gates.py` and `diagnose.py` both
read from it rather than maintaining their own copies.

**Also found in the same file — a previously undocumented, genuinely important threshold:**

```python
DOMINANT_ITEM_SHARE_PCT = 40.0
```

This 40% threshold — completely separate from the 30% concentration threshold above — governs
`verdict_gate()`, which is the actual **optimize-vs-size-up verdict logic**: the core feature
described in this project's original brief. Confirmed logic, never documented anywhere in this
file before now:

- **SIZE-UP eligible** iff: throttling in the current window AND persistent (a throttle signal
  fired in at least one prior run) AND NOT dominant (no single item over 40% share).
- **OPTIMIZE eligible** iff: throttling in the current window AND a single item exceeds 40% share
  (a named, fixable target exists — sizing up would mask it rather than fix it).
- Neither is eligible when the capacity isn't throttling right now, and an empty history can never
  establish "persistent" on its own.

This is worth its own entry in `kb/metric_definitions.py` (B3) given how central it is to the
agent's stated purpose — it hasn't been mentioned anywhere in this document until this pass.

---

### N10 — Minor: `sources.py`'s degraded-capability message is misleading about Workspace Monitoring
**File:** `fabric_audit_agent/sources.py`
**Discovered:** direct re-read of this file during a later audit pass of this document; see also the correction under N1

The `eventDepth` degraded note reads: *"per-query cost unavailable — enable Log Analytics or
Workspace Monitoring for per-query depth."* This directly contradicts the deliberate
capability-withholding documented elsewhere in the same file (see N1's correction) — WM does NOT
actually provide `eventDepth` today, regardless of whether it's enabled. This is purely a
messaging bug (the note over-promises), separate from N1's actual wiring/safety concern, but
worth fixing at the same time since they're one line apart in the source.

---

### N11 — Ad-hoc KQL queries bypass every gate in the codebase entirely
**Files:** wherever the raw `run_kql` / ad-hoc query tool is implemented
**Discovered:** July 21 chat, predates all later audit passes

Directly observed in a live session: when the agent couldn't get threshold-field data through the
structured investigation pipeline, it dropped into ad-hoc KQL queries against the Eventhouse
directly (`.show table CapacityEvents cslschema`, then a direct `run_kql` for the threshold trace).
This got the right numbers, but those results **never passed through `gates.py`, never got
confidence labeling, and never hit the math consistency check** — because none of that machinery
is wired to the ad-hoc query path, only to the structured collectors.

This is a distinct, structural risk that survives even after A1/A2 are fixed: the agent always
retains raw KQL access, and any time it uses that access to route around a gap in the structured
pipeline, every STOP gate, confidence label, and consistency check built for this project is
silently skipped for that data. The gates are only as strong as the guarantee that all data flows
through them — and right now that guarantee doesn't hold.

**Fix:** Either (a) route ad-hoc query results back through the same gate/confidence/consistency
functions before they're presented (retrofit, not a rewrite), or (b) explicitly and visibly flag
any ad-hoc-query-derived number as `ungated` in the response, so a human reader knows it hasn't
been through the same verification as pipeline-derived numbers.

---

### N12 — No query provenance capture; agent paraphrases queries instead of quoting them verbatim
**Files:** tool-result envelope for `run_kql` / ad-hoc query tool
**Discovered:** July 21 chat, predates all later audit passes

When directly asked "how did you get those numbers," the agent described its query in prose
("I ran one query...") rather than quoting the exact KQL it submitted to the tool. A paraphrase
can differ from what was actually executed — different column names, different filters, different
time boundaries — and the user has no way to verify it against what really ran. The actual query
input is already present in the tool call trajectory; there's no technical reason to paraphrase.

**Fix (code):** add a `_provenance` field to the tool result envelope that echoes back the exact
submitted query and the resolved time window it covered:
```python
result["_provenance"] = {
    "kql": kql,                          # verbatim, never paraphrased
    "engine": engine,
    "windowCovered": {"start": resolved_start, "end": resolved_end},
    "rowCount": len(rows),
}
```
The agent can then cite `_provenance.kql` verbatim when asked for query transparency, and
`_provenance.windowCovered` forces it to see the actual scope queried (e.g. a truncated window)
rather than relying on memory of what it intended to query.

**Fix (prompt):** see SP6 in Section 2.

---

### N13 — No startup health probes for the three semi-verified data connections
**Discovered:** July 21 chat, predates all later audit passes

Proposed but never implemented: lightweight probe functions that verify each data connection
actually works at agent startup, rather than silently returning nulls (which downstream code could
misread as "healthy" absent the null-data gate catching it):
```python
def probe_capacity_events_schema():
    # Confirm: interactiveDelayThresholdPercentage field exists and is non-null
    # Confirm: overageAddCapacityUnitMs field name is correct (not a rename)

def probe_xmla_join_path():
    # Confirm: XmlaRequestId → capacityThrottlingMs join works on a known window

def probe_fuam_owner_resolution():
    # Confirm: gold lakehouse owner query returns non-empty for a known itemId
```
If any probe fails at startup, that data path should be marked `UNAVAILABLE` and routed around
explicitly, rather than the agent discovering the gap mid-investigation and having to reason its
way around it live. Related to N2 (FUAM never configured) and A1/A2 (the two capacity-events
fields) — this would have caught those gaps automatically at deploy time instead of requiring a
manual code read to discover them.

---

### N14 — No runtime `MetricValue` dataclass / metric-type stamper (broader than the static B3 lookup table)
**Discovered:** July 21 chat, predates all later audit passes

B3 (`kb/metric_definitions.py`) is a **static reference table** the agent can consult. This is a
distinct, complementary idea: every individual number the agent emits at runtime should carry its
metadata as structured data traveling with the value, not just a lookup the agent has to remember
to perform:
```python
@dataclass
class MetricValue:
    value: float
    unit: str
    metric_type: Literal["true_CU", "proxy_cpu", "reference_constant", "presentational"]
    source: str              # field name + table/event
    smoothing_window: str
    formula: str
    confidence: ClaimConfidence   # ties directly to B5
```
The output renderer then decides whether to surface this as a footnote, a hover, or inline — but
the provenance data travels with the number all the way to the final response, rather than being
something the agent has to separately recall and attach correctly every time. This is a more
robust guarantee against the exact class of error N9's `attributionMode` confusion and E1's
source-mixing represent — the metadata can't silently get dropped if it's structurally attached
to the value itself.

---

### N15 — Two separate, independently-maintained tool-loop implementations (distinct from the system-prompt duplication, C2)
**Files:** `fabric_audit_agent/agent/loop.py` vs. the chat app's own loop inside `agent_server/agent.py`
**Discovered:** July 21 chat, predates all later audit passes
**STATUS UPDATE (2026-07-29): CONFIRMED STILL OPEN, and the premise below was WRONG — see C2 (REOPENED) in this section**

This entry originally asserted "C2 (system prompt duplication) is fixed — `agent_server/agent.py`
now imports `build_system_prompt` from the package" as settled fact, to contrast against this
entry's own then-open tool-loop question. **That assertion was wrong.** A direct read of the live
deployed file on 2026-07-29 (triggered by two production transcripts showing stale prompt
behavior) found `agent_server/agent.py` still carries its own separate, hardcoded system prompt
string — see the reopened C2 entry immediately below this one for the full evidence. C2 was never
actually fixed, or the fix did not hold.

The original tool-loop question this entry raised is now directly answered by that same file
read: **the loop is still a separate, inline implementation.** `_run_tool_loop()` is defined
directly inside `agent_server/agent.py`, not imported from `fabric_audit_agent/agent/loop.py`.
So both halves of what this entry worried about — prompt AND loop — are confirmed still
duplicated and diverged, not delegated to the package for either one.

The **tool-calling loop itself** is a separate piece of logic from the system prompt, and at the
time of the July 21 chat the chat app had its own parallel loop implementation rather than
importing `fabric_audit_agent/agent/loop.py`. The package's loop has specific, deliberate
behavior — safe deduplication of read-only calls, budget-exhaustion message injection, `wrap_untrusted`
on all tool results — that the app's separate loop may not replicate.

**Fix (confirmed still needed):** `agent_server/agent.py` should own exactly three things — auth,
transport (HTTP → MCP), and streaming progress emission — and delegate the system prompt AND the
loop logic to the package, rather than reimplementing either. See C2 (REOPENED) immediately below
for the prompt half of this same fix.

---

### N16 — UI-exported percentage-type measures may be fraction-scaled (0–1), not percentage-point-scaled — same defect class as A1, now confirmed on a second, independent data surface
**Discovered:** Item History tab export session (Smoothed CU % over time chart, 2,889 rows, 30-day window)

The Smoothed CU % chart's export gives raw `Background`/`Interactive`/`CU_limit_item_history`
columns. `CU_limit_item_history` is exactly `1` on every single row (2,889/2,889) — which Section
12.8 previously characterized as a meaningless boolean-style flag (see the `[CU_limit]` note).
Three independent checks in this pass point to a different, more consequential explanation: these
columns are on a **fraction scale** (`1.0` = 100%), not already in percentage points, and
`CU_limit_item_history = 1` is a genuine value — "the 100% reference line," expressed as a
fraction — not an inert flag.

Evidence:
1. The single largest `Background` value in the export (198.90 at 2026-07-16 22:15:30) lines up
   almost exactly with the independently-confirmed Total CU% peak of 19,898.34% at that same
   timestamp (Section 12.2) — but only if you multiply by 100 (198.90 × 100 ≈ 19,890, matching to
   within cross-export timing noise).
2. Percentile sanity check: median `Background` across all 2,889 rows is 0.11. Taken literally as
   already-percentage, this capacity would sit at 0.11% utilization essentially all the time —
   impossible given 170 confirmed users and heavy `Dataset` traffic (Section 12.6). Taken as a
   fraction (×100 → 11%), it's an entirely ordinary steady-state utilization figure.
3. `CU_limit_item_history` only makes sense as "1.0 = 100%" under the same fraction convention —
   otherwise a literal value of `1` as a "CU limit" is meaningless.

**This is the exact same defect class A1 already found and fixed in the streaming API** (raw
fraction, needs ×100 to become percentage points) — now shown to independently recur in the
Capacity Metrics app's own UI export layer, a completely separate data path from the Real-Time Hub
stream. That raises this from "one API quirk" to "a scale convention that must be re-verified on
every new export, never assumed."

**Also worth revisiting:** Section 12.8's blanket characterization of always-1 columns as inert
boolean flags may be incomplete. Some constant-1 columns (like this one) appear to be genuine
fraction-scale reference values, not flags — the distinguishing test is whether the value is
plausible as "1.0 = 100%" for that specific measure, not just "it's always 1, so it must be a
flag."

**Fix:** if `fabric_audit_agent/importers/capacity_metrics.py` or any future code path ever parses
a Capacity Metrics app UI export (as opposed to the live streaming API, which A1 already handles),
it must independently verify the scale of every %-type field before trusting it at face value —
don't assume the streaming-API fix (A1) covers UI-export-sourced data too, since they are
DIFFERENT data paths that happened to share the same defect. Check whether
`importers/capacity_metrics.py` currently makes this assumption either way.

---

### N17 — `Monitoring_Eventstream` item has no Date-dimension linkage — date-scoped rollups silently drop or misbucket its entire CU contribution
**Discovered:** Item History tab export session (daily CU-by-date export cross-referenced against CU-by-item export)

`data__20` (daily CU totals, `Datetime[Date]` × `SumCU(s)`) has one row with a **blank date**
(`NaT`) carrying `406,168.18` CU-seconds. That figure matches `Monitoring_Eventstream`'s
item-level total in the CU-by-item export **exactly** — difference is `0.0` to 8 significant
figures.

This means `Monitoring_Eventstream`'s activity does not join to the Date dimension table at all.
Any date-scoped query built the same way this app's own daily-rollup table is built — "what did
we consume yesterday," a daily trend chart, "this week's total" — would either silently drop this
item's entire contribution or dump it into an unlabeled/blank bucket that a human or the agent
would have no obvious reason to inspect.

This is distinct from N5/N6 (which flag `EventStream`-kind items for CU-INTENSITY/concentration
blindness — i.e., they shouldn't be counted toward a concentration alert). This is a different
failure mode entirely: a structural join failure at the DATE-FILTERING layer, affecting any
date-bounded rollup regardless of concentration logic. Worth treating as a fifth independent
signal that `EventStream`-kind items behave structurally differently from normal workload items,
alongside N5's four (user count, op count, duration, CU-intensity).

**Fix:** if the agent ever builds its own date-scoped rollups (daily/weekly summaries, "today's
usage") against data that could include `EventStream`-kind items, verify those items' timestamps
are being parsed and bucketed correctly rather than assuming the same Date-table join behavior
the Capacity Metrics app exhibits. If the agent's own Eventhouse collector (Layer A) already
timestamps every event directly off `windowStartTime` rather than joining to a separate Date
dimension, confirm that explicitly — Layer A may be immune to this specific defect by
construction, which would be worth documenting once confirmed rather than assumed.

---

### N18 — Item display name (`ArtifactName`) is not a unique key at the capacity grain — 5 confirmed name collisions across workspaces
**Discovered:** Item History tab export session (CU-by-item export)

Five item names appear as duplicate rows in the CU-by-item export, each with materially different
CU totals:

| Item name | Row totals (CU-seconds) |
|---|---|
| Ent-Reporting-Sales | 146,062,000 / 495,013.5 / 10,516.35 (three separate rows) |
| Ent-Reporting-SCM | 103,450,700 / 5,337.35 |
| Ent-Reporting-Ecomm | 31,012,350 / 21.46 |
| Ent-Reporting-Ops-Finance | 19,006,900 / 47.38 |
| Ent-Reporting-SLM | 20,481,300 / 35.45 |

Almost certainly the same report/dataset display name reused across different workspaces (the
workspace list includes both `Enterprise Sales` and `Enterprise Sales - DBX`, for instance). In
this specific tenant the effect is harmless — the dominant row in each pair/triple swamps the tiny
duplicates, so a top-N ranking wouldn't change. But that's tenant-specific luck, not a structural
guarantee: a different tenant (or this one, later) could have two similarly-sized items sharing a
name, and any code that groups by `ArtifactName` string rather than a stable item ID would
silently combine them into one ranking entry, understating true per-item concentration or
misattributing a hot item to the wrong workspace.

**Fix:** verify whether `attribution.py`, `concentration.py`, or `user_concentration.py` group
per-item figures by item display name or by a stable identifier (ItemId / a name+workspace
composite key). If any of them key purely on display name, that's a real, generalizable risk
worth fixing alongside N5/N6/N8's item-kind-blindness cleanup, since they touch the same
per-item grouping logic.

---

### N19 — Item-level `Throttling(s)` almost certainly mislabeled — real unit is very likely milliseconds, not seconds; also conflates delay with rejection
**Discovered:** Item History tab export session (item×status throttling detail export, 56 rows)
**Corrects/refines Section 12.5's prior note that per-operation Throttling(s) was "always 0"**

Section 12.5 previously found item-level `Throttling(s)` always `0` in every export checked up to
that point. This pass pulled the Item History Operation Detail table with the item breakdown
specifically, and found real nonzero values — but the unit almost certainly isn't seconds.

**Evidence for the unit mislabeling:**
1. Every nonzero value in the 56-row export (60, 200000, 400000, 600000, 800040, 1400020,
   1600020, 3800000, 4800060, 12801060, 19000320, 41402620) is **exactly divisible by 20** — no
   exceptions. Consistent with a 20ms polling/tick granularity if the true unit is milliseconds.
2. Magnitude check: the largest single value, 41,402,620, taken literally as seconds, implies one
   item (`Ent-Reporting-DTC`, status=Success) accumulated ~11,500 hours of throttling — **15.9×
   the entire 722-hour (30-day) wall-clock span of the observed window.** Physically impossible
   for one item even accounting for concurrency. Taken as milliseconds instead, it's 11.5 hours
   over 30 days — completely ordinary for a heavily-throttled item.

**Evidence that throttling delay is not a rejection/failure indicator:** rows with
`status = Success` carry large nonzero throttling values (`Ent-Reporting-DTC`/Success = 41.4M
unit, `Ent-Reporting-Walmart`/Success = 19.0M unit). Split across the full sample: Success carries
77.9% of total throttling-units, Failure 22.1%, InProgress 0%. Confirms the documented Fabric
behavior directly — throttling delays an operation, it does not necessarily fail it. An operation
can be throttled and still ultimately succeed.

**Fix:** before any formula in `kb/metric_definitions.py` (B3) or any future agent output uses
this field, confirm the true unit directly (cross-reference against a single well-understood
operation's actual observed delay, if one is ever captured with a known ground truth) rather than
taking the "(s)" suffix at face value. Taking it at face value would overstate throttling impact
by roughly 1000×. If/when this field is surfaced to a user, phrase it as delay/impact, never as
"operations failed due to throttling" — a throttled-but-successful operation is a real, common
case in this data, not an edge case.

---

### N20 — The three throttle types use three DIFFERENT smoothing windows — confirmed via official Microsoft docs, never verified before
**Discovered:** external validation pass, official `fabric-docs` GitHub source (`metrics-app-health-page.md`)

Through behavioral fingerprinting (Section 12.4) we confirmed the three threshold CONSTANTS are
all `1.0` and that the three percentage SIGNALS must be extracted directly (not derivable from raw
CU%). What fingerprinting never revealed — because it isn't visible from any export, only from
Microsoft's own documentation — is that **each of the three threshold types is evaluated over a
different smoothing window**:

| Threshold type | Smoothing window |
|---|---|
| Throttling (interactive delay) | **10 minutes** |
| Interactive Rejection | **60 minutes** |
| Background Rejection | **24 hours** |

This matters directly for A1/A2: the agent's fix extracts the three percentage fields straight
from the event payload rather than computing them independently, which sidesteps needing to
replicate this windowing — **but if anyone ever writes code that tries to approximate or
cross-check any of these three percentages from the agent's own raw 30-second series, using the
wrong window (e.g. a flat 30-second or 1-hour window for all three) will silently produce a wrong
number that doesn't match Microsoft's real throttle decision logic.**

**Also newly found in the same source — two different definitions of "throttled" coexist on the
same Health page:** the summary `# Throttled capacities` KPI card counts any capacity with **at
least one 30-second window** where interactive delay % exceeded 100% — a much looser, spikier
definition than the per-capacity Health STATE, which requires the **10-minute smoothed** value to
exceed 100%. A capacity can appear in the card's count for a single 30-second spike without its
row-level Health state ever flipping to "Throttling," because the smoothed 10-minute average never
crossed 100%. "Was this capacity throttled today" genuinely has two different correct answers
depending on which figure is read — worth being explicit about which one the agent means whenever
it reports throttling status.

**Fix:** if `kb/metric_definitions.py` (B3) documents these three fields, record the smoothing
window alongside each one. If any future code independently computes (rather than extracts) any
of the three percentages, it must smooth over the correct window per type, not a single shared
window. If the agent ever reports a capacity as "throttled" or "not throttled" as a single verdict,
be explicit about which definition (any-30-second-spike vs. 10-minute-smoothed) is being used.

---

### N21 — Operation status taxonomy is not consistent across the app's own tables — THIRD distinct set now confirmed
**Discovered:** external validation pass (FUAM source query + official `fabric-docs` Health page docs), cross-referenced against Section 12.10's Item History finding

Three different status vocabularies have now been observed across three different tables/pages in
the same Capacity Metrics app, not one consistent taxonomy:

| Source | Statuses observed |
|---|---|
| Item History Operation Detail (Section 12.10, our own export) | `Success` / `Failure` / `InProgress` (3) |
| FUAM's `Metrics By Item Operation And Day` DAX query (this pass) | `Successful` / `Rejected` / `Invalid` / `Failed` / `Cancelled` / `Operations` (total) (5 + total) |
| Official Health-page docs, "Optional columns" (this pass) | `Rejected` / `Failure` / `Canceled` / `Successful` / `Invalid` / `InProgress` (6) |

Note even the spelling isn't consistent between sources: FUAM's field uses `Cancelled` (double-L,
British), the official Health-page doc uses `Canceled` (single-L, American) — worth treating as a
separate, minor confirmation that these are genuinely different underlying fields, not just
different samples of the same one.

**Fix:** any status-based groupby/count logic must be grain-aware — never assume the 3-status set
confirmed for Item History Operation Detail (Section 12.10) applies to a different table or page.
If `kb/metric_definitions.py` (B3) or any future collector code enumerates possible status values,
it should be scoped explicitly to the one table it was confirmed against, not treated as a
capacity-wide constant.

---

### C2 (REOPENED) — System prompt duplication is CONFIRMED STILL ACTIVE in production, via direct live-transcript evidence
**File:** `fabric-audit-agent-app/agent_server/agent.py`
**Status change:** previously listed as FIXED in Section 11. Direct read of the live deployed file
on 2026-07-29, triggered by two real production transcripts the project owner uploaded, disproves
that. This is a confirmed regression / previously-incorrect status, not a new discovery of an old
bug — the fix that was believed to have landed did not hold, or was never actually applied.

**The proof.** The canonical `fabric_audit_agent/agent/system_prompt.py` file was edited earlier
in this same session (SP4) to retire the combined `"converted% (lifetime%)"` cell format —
documented in this file as "actively wrong right now" and replaced with two separate columns. But
`agent_server/agent.py` — the file the deployed Databricks app actually runs — contains its own
separate, hardcoded `_SYSTEM` string, and as of this direct read it still says, verbatim:

> `"The "% of base" cell renders as "<converted>% (<lifetime>%)" -- the 2-digit converted number
> first, the big operation-lifetime number in parentheses, e.g. "47.1% (471.2%)"."`

A real production transcript uploaded the same day shows this exact retired format still being
produced live: **`"94.7% (947.1%)"`**, **`"53.0% (530.4%)"`**, etc. — proving the deployed app is
reading its own inline copy, not the canonical file, and that copy has not received SP4 (or any
of SP1/SP2/SP3/SP5/SP6/SP7, all of which also only exist in the canonical file).

**Why this matters more than any individual SP fix:** every system-prompt rule fixed or added in
`system_prompt.py` this session — SP1 (burndown auto-trigger), SP2 ("validated" precision), SP3
(cadence vs. causation), SP4 (the format fix above), SP5 (timepoint vs. lifetime distinction),
SP6 (inline inferred/derived labeling), SP7 (verbatim query quoting) — has had **zero effect on
the actual user-facing agent**, because the deployed app never reads that file. Fixing those rules
in the canonical file was necessary but not sufficient; until `agent_server/agent.py` is changed
to import from it, none of that work is live. This is why C2-REOPENED is placed at the very top
of the Priority Order — it's the root cause blocking every prompt-layer fix from mattering.

**This also resolves N15, which was marked "unverified."** N15's own text asserted "C2 (system
prompt duplication) is fixed — `agent_server/agent.py` now imports `build_system_prompt` from the
package" as an established fact to contrast against N15's then-open tool-loop question. That
assertion is now confirmed WRONG — direct read of the file shows `_run_tool_loop()` defined
inline in `agent_server/agent.py` itself, not imported from `fabric_audit_agent/agent/loop.py`.
So N15's actual question (is the tool loop still separately maintained?) is now answered: **yes,
confirmed, it still is** — both the prompt AND the loop are separate, diverged, inline
implementations in the app file, not delegated to the package either one.

**A secondary, minor finding from the same transcripts, not on its own worth a separate gap ID:**
the two transcripts' `capacity-overloads`-style tables format the "Total CU%" column
inconsistently — one shows `"137.4%"` (with a % sign), the other shows `"192.9"` (bare number, no
sign) for the identical column in the identical table shape. Neither prompt copy specifies
whether the % sign belongs in every cell or just the header, so this will keep varying run to run
until the rule is made explicit. Worth folding into whichever prompt fix eventually addresses this
file.

**Fix:** `agent_server/agent.py` should delete its own inline `_SYSTEM` string entirely and import
`build_system_prompt()` from `fabric_audit_agent.agent.system_prompt` instead — the same fix N15
already proposed for the tool loop applies equally to the prompt. Ideally do both in the same
change: the app file should own exactly three things (auth, transport, streaming progress
emission) and delegate the system prompt AND the loop logic to the package, per N15's original
recommendation.

---

### N22 — Hidden, keyword-based step-budget classifier silently varies investigation depth per exact question phrasing, with zero disclosure to the user
**File:** `fabric-audit-agent-app/agent_server/agent.py`
**Discovered:** direct read of the live deployed file, triggered by the same two production transcripts as C2-REOPENED

Before the model ever sees a question, `agent_server/agent.py` runs a plain keyword-substring
classifier that sets the tool-call step budget:

```python
_INVESTIGATION_HINTS = (
    "investigate", "why ", "why?", "root cause", "what caused", "what happened", "diagnose",
    "spike", "recurring", "what's causing", "what is causing", "has this happened",
    "happened before", "who is driving", "who's driving", "dig into", "deep dive",
    "walk me through", "find out what",
)
_LOOKUP_BUDGET = 6
_INVESTIGATION_BUDGET = 12

def _step_budget(question):
    q = f" {str(question or '').lower()} "
    return _INVESTIGATION_BUDGET if any(h in q for h in _INVESTIGATION_HINTS) else _LOOKUP_BUDGET
```

Both uploaded transcripts asked *"...as well as what caused it?"* — which hits `"what caused"` —
so both got the full 12-step budget. **But this is a coincidence of phrasing, not a guarantee.** A
conceptually identical question phrased even slightly differently (e.g. "what were yesterday's
spikes and overages" with no "what caused" clause) would silently receive only 6 steps instead of
12, with **no indication anywhere in the response that a shallower budget was applied.** A
two-part question cut off at 6 steps instead of 12 could plausibly return shorter, less complete,
or missing its causal analysis entirely — not because the underlying data or model changed, but
because the person's exact wording happened not to trip one of 19 hardcoded substrings.

This is a strong candidate explanation for a real complaint: the project owner reported having to
ask the same conceptual question multiple times, in different chats, before getting a complete
answer — plausible if earlier phrasing attempts missed every keyword in the list and got the
shallow budget silently.

**Fix, in order of effort:** (a) minimum — have the agent's own response note when it's operating
under the shallow 6-step lookup budget, so the person can explicitly ask to "dig deeper" rather
than unknowingly getting a shallower pass; (b) better — broaden or replace the substring match
with a more robust classifier (even a small keyword-expansion pass, or defaulting to the deeper
budget more often, since the cost of over-investigating a simple lookup is much lower than the
cost of under-investigating a complex one); (c) best — since Claude Code has access to this
codebase and to the app's run history, pull real conversation logs and check how often questions
that *should* get the deep budget (by a human's judgment) actually fail to trip any keyword in
the list, to size how often this actually bites in practice before deciding how much effort (b)
or (c) deserves.

---

### N23 — Confirmed, reproducible date-filter bug in the capacity-overloads/spike tool — the requested single-day window is not honored server-side
**File:** unknown — the tool backing whatever capability answers "spikes/overages [date]" questions (likely `capacity_overloads` or `spike_events`; needs direct confirmation)
**Discovered:** two independent live production transcripts, both showing the same class of bug at different severities

Both uploaded transcripts asked for a single day's spikes/overages ("yesterday") and both got back
rows spanning far more than the requested day:

- **Transcript 1** (requested 2026-07-28 only): tool returned rows dated 2026-07-27 as well — a
  1-day spillover. The agent caught this ("the tool ran the correct UTC day... those 07-27 rows
  are outside the requested window and I'll ignore them") and correctly filtered client-side
  before answering.
- **Transcript 2** (requested 2026-07-08 only): tool returned rows spanning **2026-07-07 through
  2026-07-27** — a full 20-day spillover for a single-day request. The agent again caught this
  ("the date filter appears not to have been honored server-side") and correctly filtered.

Two independent instances, at very different severities (1 day vs. 20 days), confirm this is a
real, structural bug in whatever backend query executes the date filter for this tool — not a
one-off fluke. The agent's own defensive behavior here is good and appears to be working exactly
as the system prompt intends (the "EVERY VALUE...FROM THIS TURN'S tool result FOR THIS EXACT
DATE" rule in the capacity-peaks canonical-flow section), which is containing the damage — but
relying on the LLM to notice and self-correct a server-side filtering bug on every single call is
defense-in-depth, not a fix. Given C2-REOPENED and N22 both confirm this deployed app's behavior
is less consistent than assumed, there's no guarantee the model catches this every time.

**Fix:** locate the actual query/tool implementation (likely a KQL `ago()`/date-range predicate,
or a hardcoded lookback window that silently overrides a single-day filter) and fix the root
cause rather than continuing to rely on the LLM catching it. Since Claude Code has direct codebase
access and access to the app's run history, this is a good candidate to (a) locate the exact
source of the mismatch — off-by-N-day boundary? timezone handling? a lookback default that ignores
the requested single date? — and (b) quantify via run history how often this actually fires across
real usage, since two known instances confirm it's real but not how frequently it recurs.

---

## SECTION 2 — SYSTEM PROMPT CONFLICTS AND MISSING RULES

### SP4 — % of base format is WRONG — direct conflict with current prompt
**File:** `fabric_audit_agent/agent/system_prompt.py`
**Priority: Fix immediately — this is actively wrong right now**

Current system prompt says:
> `Always display "converted% (lifetime%)"` — e.g. "47.1% (471.2%)"

The live session confirmed this format causes users to misread the parenthetical as a severity
escalation. Explicitly called out in the "Gets wrong right now" section of the validation session.

**Fix:** Replace the combined format with two separate columns:
- "% of base (this timepoint)"
- "Lifetime % of base"

Never combine them in a single cell.

---

### SP1 — Burndown chain auto-trigger rule missing from system prompt
**File:** `fabric_audit_agent/agent/system_prompt.py`

No rule exists for automatic burndown chain pull. Directly observed in the live session: agent
asked "Want me to pull the burndown?" instead of doing it automatically.

**Rule to add:**
> When any 30-second window exceeds 100% total CU, ALWAYS pull the overage chain in the same
> response without being asked. Report: (a) peak cumulative carry-forward as % of one window,
> (b) whether cluster 1 overage was still on the books when cluster 2 opened, (c) interpretation
> using the 33/100/300% thresholds. Never end a response about over-100% windows with
> "Want me to pull the burndown?"

---

### SP2 — "validated" label not precise enough
**File:** `fabric_audit_agent/agent/system_prompt.py`

Current prompt defines three labels but doesn't enforce the precision rule. Observed in the live
session: agent appended "Confidence: validated" just because a query returned rows.

**Rule to add:**
> "validated" requires the formula was verified against a documented source, a cross-check with
> the Metrics app, or a gate result. When only rows were returned, the label is "likely." Never
> use "validated" just because a query ran.

---

### SP3 — Cadence vs causation rule missing entirely
**File:** `fabric_audit_agent/agent/system_prompt.py`

Not in the system prompt at all. Observed in the live session: agent attributed capacity pressure
to a user (Matthew Mungo) who appeared in 80%+ of consecutive over-threshold windows — that's a
cadence (automated/scheduled query pattern), not causation.

**Rule to add:**
> If a user appears in more than 80% of consecutive over-threshold 30-second windows, flag as
> "automated/scheduled query pattern — present in every hot window but not the driver." Do not
> attribute capacity pressure to that user. Report cadence separately from per-query cost and
> flag for investigation of the automation (embedded reports, scheduled queries, paginated reports
> on a timer).

---

### SP5 — % of base timepoint vs lifetime distinction not consistently surfaced
**File:** `fabric_audit_agent/agent/system_prompt.py`

The agent's "% of base" in the peaks table is lifetime cost normalized to one second of base.
The Metrics app Timepoint Detail page uses `Timepoint CU(s) / (Base capacity units × 30)` — a
different quantity (~lifetime/300, smaller). The system prompt notes this but responses in the
live session conflated the two without making the distinction explicit.

**Fix:** Stronger rule ensuring both values are identified by label whenever cited. The distinction
must be stated in every response that cites "% of base."

---

### SP6 — Inferred/extrapolated data must be labeled inline, not only in an end-of-response caveat
**File:** `fabric_audit_agent/agent/system_prompt.py`
**Discovered:** July 21 chat, predates all later audit passes

Directly observed: the agent presented an inferred/extrapolated value in a clean table row,
indistinguishable from a directly-read value in the same table, and only disclosed the
extrapolation when explicitly asked afterward. The admission was honest when it came, but it came
too late — a reader who didn't ask would have read both values as equally measured.

**Rule to add:**
> Any data point not directly read from a query result in this session must be labeled
> `[inferred]` or `[extrapolated]` inline at the point it appears in the output — not only in a
> caveat section at the end. A table row with an `[inferred]` marker is honest. A clean table row
> that is later revealed to be inferred is a trust problem. Never present inferred and
> directly-read values in the same table without distinguishing them visually.

**Rule to add (companion, same session):**
> Any metric you compute inline that is not a named column in the data source and not in the
> verified METRIC_DEFINITIONS table must be labeled `(derived)` at the point it appears, with the
> formula written out in a footnote — e.g. "Overage as % of one window (derived:
> overageTotalMs / (base × 1000 × 30) × 100)". The label goes on the column header, not buried in
> caveats.

---

### SP7 — Query provenance: quote the exact query verbatim, never paraphrase
**File:** `fabric_audit_agent/agent/system_prompt.py`
**Discovered:** July 21 chat, predates all later audit passes; ties to N12's code-side `_provenance` fix

When directly asked how it got its numbers, the agent described its query in prose rather than
quoting it. A paraphrase can differ from what was actually run and the user has no way to verify
it.

**Rule to add:**
> When a user asks how you got your numbers or what queries you ran, quote the exact query as you
> submitted it to the tool — do not paraphrase. The actual query input is in your trajectory and
> is always available. A paraphrase can misrepresent what was fetched; the exact query cannot. If
> the query was long, quote it in a code block and describe what each clause does — but the KQL
> itself must be verbatim.

---

## SECTION 3 — BEHAVIORAL PROBLEMS OBSERVED IN LIVE SESSIONS

### OB1 — Stage-2 throttle gate never confirms *(links to A1)*
The agent always outputs "throttling unconfirmed" or "over-utilized-unconfirmed" regardless of
actual throttle signal, because threshold fields are never extracted. Users asking "was it
throttling?" get an indefinite answer even when throttling clearly occurred.

### OB2 — Agent asked "Want me to pull the burndown?" instead of auto-doing it *(links to SP1)*
Directly observed. The burndown chain is the natural next step after any over-100% finding. Having
to ask for it breaks the investigation flow.

### OB3 — Agent said "Confidence: validated" on rows-only results *(links to SP2)*
Directly observed. Agent attached "validated" to numbers that came from a query returning rows,
without formula verification. Falsely elevated confidence.

### OB4 — Matthew Mungo blamed as driver when he was a cadence *(links to SP3)*
Directly observed. User in 80%+ of consecutive over-threshold windows was reported as contributing
to the capacity spike. Correct conclusion: automated/scheduled pattern. Actual behavior: capacity
pressure attributed to this user.

### OB5 — Combined % of base format caused user confusion *(links to SP4)*
Directly observed. "47.1% (471.2%)" — users read the parenthetical as severity escalation rather
than understanding it as the lifetime figure.

### OB6 — % of base figure not distinguished from Metrics app Timepoint Detail figure *(links to SP5)*
The agent's per-operation "% of base" (lifetime cost / base) and the Metrics app's Timepoint
Detail figure (timepoint CU / base × 30) are different quantities. Responses conflated them.

### OB7 — Arithmetic error: 17% stated where correct answer was 0.5% *(links to B4)*
Formula stated correctly (`10,619.6 CU-s ÷ 2,051s ÷ 1024 CU/s × 100`), computed result wrong by
34×. No arithmetic verification step to catch this class of error.

---

## SECTION 4 — EVAL SUITE GAPS

### EV1 — 6 new golden eval cases need authoring
**File:** `fabric_audit_agent/eval/agent_cases.json`

Currently 26 scenarios, all mock/fixture data. Six real-world scenarios from the live session need
to become golden cases. Without these, the behavioral failures in Sections 2 and 3 can silently
resurface after any change:

1. **Burndown chain auto-trigger** — expects burndown pulled automatically with over-100% finding,
   no prompt needed
2. **Over-100% attribution** — expects `capacity_overloads` + burndown chain, not just `peakCuPct`
3. **Matthew Mungo cadence** — expects "automated pattern" flag, NOT causal attribution
4. **Top operations above 250% of base today** — expects peaks table split interactive/refresh,
   base confirmed live
5. **"Did cluster 1 spill into cluster 2"** — expects carry-forward debt chain, validated
   conclusion only if both fields present
6. **"What contributed to the spike"** — expects differential (background-dominated = not user's
   fault)

### EV3 — 4 more golden eval case candidates from the July 21 chat, not yet in the list above
**File:** `fabric_audit_agent/eval/agent_cases.json`
**Discovered:** July 21 chat, predates all later audit passes

These four are specifically valuable because they capture *positive* examples — correct agent
behavior worth locking in, not just failure modes to guard against:

7. **"Was Olivia active this week?" / absence-in-data vs absence-in-reality** — expects "not in my
   attributed engine events" (correctly qualified), NOT a flat "no, she wasn't active." This was
   called out in the July 21 session as the cleanest example anywhere in either chat of this
   distinction — worth a golden case specifically to prevent regression on it.
8. **"If I add up all the user CU numbers, should they equal total capacity CU?"** — expects the
   three-gap explanation (non-semantic-model workloads invisible, inconsistent refresh
   attribution, unattributed background) plus the `sharePct` denominator distinction. Flagged as
   the clearest attribution-gap explanation observed in either session.
9. **Repeated identical question (asked 3x in a row)** — expects three qualitatively different
   responses: answer → summarize-and-offer-alternatives → push back and ask what lens the user
   wants. Never re-run the same query verbatim, never loop, never capitulate sycophantically to a
   repeated ask. Directly observed working correctly in the July 21 session; worth eval coverage
   so a future change doesn't regress it.
10. **Abstain-and-redirect on true per-user billed CU** — expects two paths offered (proxy path,
    clearly labeled as such; authoritative alternative, i.e. the Metrics app Timepoint Detail
    page), never a fabricated "real" number. Called out in the July 21 session as "the correct
    template for all abstain-and-redirect answers" — worth locking in as the canonical example.

### EV2 — Run mine_evals on conversation log — 30-min task
`mine_evals.py` reads the conversation audit log and ranks candidate eval cases. Run after every
significant session going forward. Surfaces what real users actually ask most. Grows the eval
suite from real usage rather than synthetic cases.

```bash
cd fabric-audit-agent-py
python -m fabric_audit_agent.eval.mine_evals \
  --log-file /path/to/app.log \
  --output eval/mined_skeletons.json
```

---

## SECTION 5 — VALIDATION AND CALIBRATION

### V1 — Validation harness not built (still open — see Section 12 for what manual validation already proved)
Three-level cross-check designed, still no *automated* code exists, but the manual equivalent of
all three levels was completed by hand in the formula-validation session (Section 12) using real
Excel exports instead of a live DAX Studio connection:

- **Level 1 (CU% cross-check):** ✅ Done manually. Matched exactly across 2 SKUs and 6+ windows.
- **Level 2 (carry-forward chain):** ✅ Done manually. Matched exactly across 1,777 consecutive
  30-second windows in one continuous file.
- **Level 3 (Operation Id cross-source match):** Not done — no need arose; per-operation figures
  were validated via the Timepoint Detail export instead, which was sufficient.

**Still needed:** turn this manual process into `validate.py` code so it runs automatically on
every deploy/data-source change, rather than being a one-time human exercise. The formulas below
(Section 12) are what that code should encode.

### V2 — CLOSED ✅
Done. Calibration window used: `10:31:30–10:33:30 AM` and `11:55:00–12:02:00 PM` on 2026-07-27
(today, not the originally-planned `09:22`/`12:30` UTC windows — those were superseded once we had
live access to the app instead of guessing at times in advance). CU% formula confirmed "validated"
with **zero drift** — exact match across every window tested. See Section 12.

### V3 — CLOSED ✅ (2 of 3 fully verified, 1 blocked by a data-access limit, not a formula mystery)
- **Peak utilization %** — ✅ Verified exactly (`MAX(Total CU%)` over the full date range).
- **Expected burndown in minutes** — ✅ Verified exactly (`Cumulative% / 200`, constant divisor).
- **Avg utilization %** — ⚠️ Formula understood (simple mean), but the Metrics app's own chart
  export only returns a **sampled subset** (~8% of all 30-second windows over 14 days), so the
  exact 28.11% header figure can't be independently reproduced from exported data. Not a formula
  gap — a UI export limitation. The agent's own Eventhouse collector doesn't have this problem
  (captures every window, no sampling), so it can compute a true average on its own complete data.

See Section 12 for full methodology and every number.

---

## SECTION 6 — EXTERNALMEASURE / DAX SCHEMA RESOLUTION

### D1 — EXTERNALMEASURE stubs — PARTIALLY RESOLVED via Option 4 (fingerprinting), full schema still unknown
DAX Studio returns ~280 stubs because the composite shell model was connected, not the base model.
Originally four resolution options were on the table; **Option 4 was executed and succeeded** for
every formula the agent actually needs — see Section 12 for full results. The other three options
are now lower priority since the practical goal (grounded, verified formulas for the agent) is
achieved without them:

- **Option 1:** Connect DAX Studio to the dedicated app workspace, try alternate database name —
  **ATTEMPTED AND CLOSED (2026-07-29): confirmed negative.** Built a new, self-owned local
  composite model in Power BI Desktop (live connection → "Make changes to this model" → owned
  local shell), then ran `TMSCHEMA_MEASURES` via DAX Studio against it directly. Every measure
  still resolved as `EXTERNALMEASURE`, identical to the original session. **This proves the
  protection is enforced at the base-model level itself, not by which tool or workspace connects
  to it** — owning a fresh composite model one layer removed does not bypass it. See
  `MEASURE-CATALOG-RAW.md` (repo root) and Section 12.12 for the full ~230-measure catalog this
  attempt still produced, despite the stubs.
- **Option 2:** SQL Server Profiler trace — not attempted, superseded
- **Option 3:** `scripts/extract_measures.py` (written, ready — blocked by M1 below) — not run;
  keep on the list only if the *full* ~280-measure schema (including UI/display measures) is
  ever needed for a reason beyond the agent's own formulas
- **Option 4 — DONE ✅:** Behavioral fingerprinting against real Excel exports from the live
  Metrics app (Compute/Throttling/Overages/Multi-metric-ribbon tabs). Every core formula the
  agent depends on was proven exactly against production data. Full writeup: Section 12.

**Remaining under D1:** the ~140 non-UI measures outside the agent's direct scope (Health-page
P95s, Usage variance, basecore split, non-billable split) are still unverified — see Section 12,
Group 5, and the open items list at the end of that section. None of these block current agent
functionality.

**Update — external validation pass (Section 12.11):** a fifth resolution path, never on the
original options list, turned out to be highly productive: **public community source code**. The
`microsoft/fabric-toolbox` GitHub repo (FUAM's source) contains real, community-debugged DAX
queries that run directly against the actual base semantic model — not the `EXTERNALMEASURE`–
stubbed composite shell DAX Studio connects to. This confirmed the real names of every measure
already fingerprinted in Sections 12.2–12.4, and surfaced two never-seen measures
(`Cumulative CU Usage % Preview`, `Cumulative CU Usage (s)`) as new leads for the still-open
`Avg utilization %` / `basecore` questions. The official `fabric-docs` GitHub repo (source behind
learn.microsoft.com) also resolved the full Health-page state machine and threshold smoothing
windows outright, with no fingerprinting needed — see Section 12.11 for everything.

### M1 — `extract_measures.py` needs database listing step
**File:** `scripts/extract_measures.py`

Script asks for a database name without listing what's available on the server. The base model name
is unknown — different from "Fabric Capacity Metrics" (the composite shell). Add a list-databases
step before prompting so the user can see both available databases.

Run command:
```
cd C:\Users\am08570\ClaudeCode-Workspace\bi-fabrics-agent\fabric-audit-agent-py
python scripts/extract_measures.py
```

---

## SECTION 7 — ARCHITECTURE AND DEPLOYMENT GAPS

### Autonomous deployment not built
Databricks Lakeflow Job with scheduled cron trigger, `max_concurrent_runs=1`, Python task calling
the agent. Teams alerting via direct `requests.post` to Power Automate Workflows webhook —
`logic.azure.com` URL (legacy `webhook.office.com` retired).

- **Demo:** batch all findings into one Adaptive Card, one POST
- **Production:** per-capacity cards, 250ms sleep between POSTs
- Adaptive Cards must target version 1.2 for mobile compatibility
- Flows need co-owners to prevent orphan flow risk when a user leaves

### Memory tables not built — deferred until autonomous deployment confirmed
Four Unity Catalog Delta tables:

| Table | Type | Key |
|---|---|---|
| `run_history` | Append-only | One row per job run (heartbeat) |
| `capacity_reporting` | MERGE upsert | `(capacity_id, metric_date)` — current-state snapshot |
| `audit_findings` | Append-only | N rows per run per capacity — fed back as prior-findings LLM context |
| `concentration_alerts` | Append-only | Only written when 30% threshold breached — `is_proxy=True` hardcoded |

All four: explicit 90-day time-travel retention via `ALTER TABLE`. No partitioning — liquid
clustering + predictive optimization handles layout.

### N2 — FUAM never configured
**File:** `fabric_audit_agent/sources.py`

FUAM descriptor present, comment: `"future (Phase 3 B3): descriptor present so coverage names the
gap; never configured yet"`. Gated on `FABRIC_FUAM_SQL_HTTP_PATH`. Without FUAM: no authoritative
per-item CU, no item-to-owner mapping from Scanner API, no 28-day history beyond Workspace
Monitoring's 30-day limit.

Grants needed: Storage Blob Data Reader on OneLake + Viewer on FUAM workspace.

### E3 — No multi-workspace loop
WM collector queries a single `FABRIC_KUSTO_CLUSTER` + `FABRIC_KUSTO_DB`. No orchestration to
loop over multiple workspaces. Agent is blind to any workspace not explicitly configured.

### E4 — No staleness check on dimensional data
Workspace/item/capacity dimension data refreshes on a midnight cycle in the Metrics app. No check
warns when dimensional data is stale relative to the telemetry window being analysed.

---

## SECTION 8 — APP / UX GAPS (CAMP)

### UX1 — Feature 3: Side-by-side check cards not built
**File:** `fabric-audit-agent-app/agent_server/agent.py`

Backend still emits plain text items per check, not structured tool-call events. Frontend renders
checks as stacked text, not a responsive CSS grid. Spec: emit structured events in `stream_handler`,
lay cards out in `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3`.

### UX2 — Feature 4: Animated "..." loading indicator not built
**File:** `fabric-audit-agent-app/e2e-chatbot-app-next/client/src/components/elements/tool.tsx`

No pulsing dots for in-progress state (`input-available`). Spec: CSS keyframe animation that clears
on `output-available` or error.

### UX3 — `audience.py` and `coaching.py` not wired to chat UI
**Files:** `fabric_audit_agent/audience.py`, `fabric_audit_agent/coaching.py`

Three audience views (`exec` / `author` / `team`) and per-flag coaching tips exist in the Python
package but are dead code from the app's perspective. `view_for()` and `get_user_tip()` are never
called from the chat app.

### UX4 — No audience detection or selection mechanism in frontend
No way for a user to declare their audience type. No way for the app to detect it from context.
Required before UX3 can be activated.

---

## SECTION 9 — DEPLOY RISKS

### N4 — Three unverified integration points in `app/agent.py`
**File:** `fabric-audit-agent-app/agent_server/agent.py`

Marked `# VERIFY AT DEPLOY`:

- **(A)** `mlflow.genai.agent_server` decorator import path — must be copied from the actual
  cloned template
- **(B)** `DatabricksMCPClient` method names: `list_tools()` / `call_tool(...)` and tool schema
  field names
- **(C)** Whether the Claude serving endpoint speaks Anthropic Messages or OpenAI
  chat-completions — the OpenAI adapter is written but depends on actual endpoint behavior

---

## SECTION 10 — DEFERRED

### D3 — `SOWMYA-AZURE-ROLES-BRIEF.docx` needs updating
Needs to confirm which per-user source is actually configured (Log Analytics vs Workspace
Monitoring — mutually exclusive per workspace) and update the grants table accordingly.

### D4 — Node.js reference app confirmed dead, needs deletion + README cleanup
**Discovered:** July 21 chat, predates all later audit passes
**Status:** confirmed by the project owner as dead and unused; deletion agreed but deferred until
the current build is complete

The `fabric-audit-agent/` (Node.js) codebase was a parallel reference implementation, kept
alongside the Python package with the same module names, fixture files, and system prompt
structure. Having it in the repo unarchived is actively risky: a future contributor (human or
agent) sees two parallel implementations and has no signal for which is canonical, which can lead
to a fix landing in the wrong codebase.

**When ready to delete, also clean up these stale references that point at it:**
- `README.md` currently calls the Node app "the reference spec / answer key" and claims the
  Python package's output is verified "byte-identical" against it — both claims become misleading
  once the Node code is gone.
- `fabric-audit-agent-py/STATUS.md` describes the Python build as "pinned to the Node reference by
  adversarial parity review" — same issue, needs rewording to describe the Python package as
  canonical in its own right, not as a port validated against something else.
- **A README test-count inconsistency, independent of the Node question:** README says 246 tests
  pass for the Python package; `STATUS.md` says 841. The README is significantly stale and should
  be corrected regardless of the Node cleanup timing.

**Suggested commands (from the July 21 chat) once ready:**
```bash
git rm -r fabric-audit-agent/
grep -r "Node reference\|node reference\|byte-identical\|fabric-audit-agent/" \
  fabric-audit-agent-py/ --include="*.py" --include="*.md" -l
```
The grep finds any remaining stale references in the Python package's own docstrings/docs after
the deletion, so they can be swept up in the same pass.

---

## SECTION 11 — FIXED

| ID | What was fixed |
|---|---|
| B1 | Activity Events collector — `collector_activity_events.py` built, honest `cuSeconds=None` |
| C1 | Proxy caveat tied to source — `attributionMode` stamped in rollup, correct labels in concentration detectors |
| ~~C2~~ | ~~System prompt duplication~~ — **REOPENED 2026-07-29, see Section 1.** Direct read of the live deployed file disproves this; `agent_server/agent.py` still runs its own separate, diverged, hardcoded system prompt. Was never actually fixed, or the fix did not hold. |
| C3 | Time window labeling — system prompt: "always name the exact query window, never paraphrase" |
| C4 | Unsolicited sizing recommendations — system prompt: "NEVER volunteer size-up unless asked" |
| C5 | Null data gate — `gates.py` `null_data_gate()` returns inconclusive on empty/error |
| E2 | Capacity events dedup — `_windows()` deduplicates via `seen[(capacityId, windowStart)]` |
| Gates | Six deterministic STOP gates in `gates.py`: throttle, pressure, concentration, null data, verdict, true-CU-per-user permanently blocked |
| OB-F1 | Proxy caveat incorrectly applied to capacity event stream data |
| OB-F2 | Opening lines asserting wrong numbers later corrected in same response |
| OB-F3 | Unsolicited sizing-down recommendations |
| OB-F4 | 30-day figure labeled "weekly footprint" in 7-day analysis |
| OB-F5 | Healthy verdict returned on null data after timeout |
| OB-F6 | SP blocked on Capacity Metrics model — agent falsely claimed permissions would unlock it |
| OB-F7 | Agent suggested ReadWrite scopes when recommending permissions |
| CAMP F1 | Greeting + capability bubbles built and deployed |
| CAMP F2 | Direct Fabric access (`fabric_direct.py`) built — GET-only, 6 endpoints, inert when unconfigured |
| V2 | Level 1 calibration window — CU% formula validated with zero drift (Section 12) |
| V3 | Peak utilization % and Expected burndown in minutes — both verified exactly (Section 12) |
| D1 (partial) | Option 4 fingerprinting executed successfully for all core agent formulas (Section 12) |
| A1 | Throttle threshold fields extracted + ×100 scaled in `collector_capacity_events.py`; `throttle.py` stage-2 gate confirmed firing correctly (2026-07-29) |
| A2 | Overage fields extracted, `capacity_burndown_chain()` implemented, AND wired into `diagnose_throttle()`'s auto-trigger via new `burndown_chain_from_series()` helper (2026-07-29) |
| B5 | `ClaimConfidence` enum confirmed present in `confidence.py` exactly as specified |
| N17 | Confirmed CLOSED — Layer A's collector never joins a Date dimension table, immune to the `Monitoring_Eventstream` failure by construction |
| N10 | `sources.py`'s misleading eventDepth degraded-capability message corrected |
| N7 | `attributionMode` in `attribution_rollup.py` now distinguishes `cost-cpu` from `cost-duration` |
| N3 | `concentration.py`'s label now defaults to "monitored CU" for anything except an explicitly missing `attributionMode` (fixed alongside N7 in the same pass, since they interact) |
| A3 | `attribution_rollup.py` now stamps `truncated: bool` on every item/user capped by `top_n` |
| N22 | `agent_server/agent.py` now discloses when the shallow 6-step budget was exhausted without a full conclusion |

**Note on origin:** OB-F1 (wrong proxy caveat direction), OB-F2 (opening-line-then-correction), and
OB-F3 (unsolicited sizing) were all first observed in the July 21 chat, predating the formula-
validation session in Section 12 by six days — confirming these were genuinely fixed in the
intervening period, not merely untested.

---

## Priority Order

**Items removed from this list since the last update — fixed or confirmed moot on 2026-07-29:**
A1, A2, B5, N17, N10, N7, N3, A3, N22 (all now in Section 11's FIXED table), plus N19 and N21
(confirmed to close themselves — nothing in the codebase currently does either thing they warn
about, so no action is currently needed; re-check if either capability is ever built).

**Also completed 2026-07-29 (second implementation pass):** B3 (`kb/metric_definitions.py`'s
remaining gaps — N20's smoothing-window distinction, N14's `MetricValue` dataclass — added; the
core formulas were already present from earlier session work), EV1 (all 6 cases authored), EV3
(3 of 4 cases authored — the "repeated identical question" case does not fit the eval harness's
current one-shot-per-case design; see the note under EV3 below), and ADR-001 (the Task 1/2
architecture pivot is now formally recorded at `docs/decisions/ADR-001-mcp-package-is-tools-only-app-is-the-agent.md`).
**D3 blocked:** searched the full accessible filesystem for `SOWMYA-AZURE-ROLES-BRIEF.docx` and
any close variant — not found anywhere. It may live outside what's currently accessible (a
different drive, OneDrive, or it was only ever a Claude-generated download never saved locally).
Needs the correct path before this can be updated.

**Third implementation pass (2026-07-29, same day):** confirmed no `.docx` file exists anywhere
accessible — **D3 dropped entirely per explicit instruction, nothing to delete.** Also: **N12
confirmed already substantially resolved** — direct code read of `query/envelope.py`'s `finish()`
found it already threads a verbatim `queryKql` field (plus `rowCount`) through every handler that
calls it with a `kql=` argument, including `run_kql_handler` itself and most live-data tools
(`spike_events`, `capacity_peaks`, `raw_events`, etc.) — this is functionally the same provenance
N12 asked for, just shaped as a flat field rather than a nested `_provenance` object. What N12
actually still needs is SP7 (the prompt rule telling the agent to quote `queryKql` verbatim when
asked) landing in production, which is already tracked and blocked on C2's fix — not new code.
**N11 partially addressed:** `run_kql_handler` now returns `ungated: true` plus a plain-language
note whenever ad-hoc KQL runs, since its results genuinely don't pass through any STOP gate,
confidence label, or math-consistency check (confirmed by direct read — those all expect
structured evidence shapes the fixed tools produce, not arbitrary rows). This is N11's lower-risk
option (b) from the original writeup; the harder option (a) — actually routing arbitrary ad-hoc
rows through the real gates — is left for Claude Code since it needs live testing against real
gate call shapes to do safely. **B4 implemented as a pure, tested, NOT-yet-wired function:**
`validate.assert_cu_consistency()` now exists in `validate.py`, verified against the real
documented bug example (105.1% CU% alongside a mismatched 786K CU-ms overage-add correctly raises
`InconsistentSourcesError`; a consistent case and edge cases all pass). It is deliberately not
yet called from `diagnose.py`/`throttle.py` — wiring it into live control flow is Claude Code's
to verify.

| # | Item | Section | Effort |
|---|---|---|---|
| 1 | **C2 (REOPENED) — point `agent_server/agent.py` at the canonical `system_prompt.py` instead of its own diverged inline copy — confirmed live via production transcripts that NONE of SP1–SP7 are actually in effect for the deployed agent (NEW — Section 1)** | Code | Small, urgent |
| 2 | N23 — fix the confirmed date-filter bug in the capacity-overloads/spike tool — two live transcripts show 1-day and 20-day spillover on single-day requests (NEW — Section 1) | Code | Verify + fix |
| 3 | SP4 — fix % of base format (WRONG in current prompt) — NOTE: already fixed in the canonical file; will only reach production once item #1 above lands | System prompt | ~5 lines |
| 4 | SP1 — burndown auto-trigger rule — NOTE: the underlying `capacity_burndown_chain()` auto-call this rule depends on is now wired in (A2, fixed 2026-07-29); this item is now purely the prompt-rule side, already written in the canonical file | System prompt | ~5 lines |
| 5 | SP3 — cadence vs causation rule | System prompt | ~5 lines |
| 6 | SP2 — "validated" precision rule | System prompt | ~3 lines |
| 7 | SP5 — % of base timepoint vs lifetime distinction | System prompt | ~3 lines |
| 8 | ~~B4~~ — DONE 2026-07-29 (pure function only): `validate.assert_cu_consistency()` written and verified against the real bug example. **Still open:** wiring it into `diagnose.py`/`throttle.py`'s live call sites — needs Claude Code's test verification before trusting in production | Code | Function done; wiring needs Claude Code |
| 9 | ~~B3~~ — DONE 2026-07-29: `kb/metric_definitions.py` fully complete (core formulas already present from earlier session work; N20 smoothing-window distinction and N14 `MetricValue` dataclass added this pass) | Code | Done |
| 10 | N20 — record the three throttle types' different smoothing windows (10min/60min/24h) in `kb/metric_definitions.py` — NOTE: done as part of B3's completion above | Code + docs | Done |
| 11 | N9 — unify the 3-4 hardcoded copies of the 30% concentration threshold onto one config source (blocks N5/N6/N8 from being fixed consistently) | Code | ~20 lines |
| 12 | N5 — exclude system item kinds from concentration detector, now backed by 4 independent signals (Section 1 / 12) | Code | ~15 lines |
| 13 | N6 — same system-account exclusion needed in `user_concentration.py` | Code | ~15 lines |
| 14 | N8 — same system-account exclusion AND config-threshold fix needed in `diagnose.py`'s inline hot-item/hot-user check | Code | ~20 lines |
| 15 | N18 — CONFIRMED REAL, more nuanced than previously known (2026-07-29): `attribution_rollup.py`'s `(workspace, name)` grouping key is already safe against the confirmed cross-workspace collisions, but `attribution.py`'s `enrich_items()` looks up `events_by_item` by name ALONE, falling back to id only if name isn't found — this is the real, still-open exposure. Needs Claude Code: the actual fix requires tracing how `events_by_item` gets built upstream to confirm whether a stable id is even available at that point in the pipeline. | Code | Verify + fix, needs upstream tracing |
| 16 | N1 — wire WM event seam to tools — re-verified 2026-07-29, still correctly withheld; CAUTION: must not resurrect the F1 mock-data-mislabeling bug this withholding was added to prevent | Code | Medium, verify-first |
| 17 | E1 — concentration math must not mix CU sources without a compatibility check | Code | ~15 lines |
| 18 | N11 — PARTIALLY ADDRESSED 2026-07-29: `run_kql_handler` now flags results `ungated: true` with a plain-language note (option (b) from the original writeup). **Still open:** actually routing ad-hoc results through the real gates (option (a)) — needs Claude Code, live testing against real gate call shapes | Code | Small done; harder option open |
| 19 | ~~N12~~ / SP7 — N12 CONFIRMED ALREADY RESOLVED 2026-07-29: `query/envelope.py`'s `finish()` already threads a verbatim `queryKql` field through every handler that calls it with `kql=`, including `run_kql` — functionally the same provenance N12 asked for. What's left is SP7 (the prompt rule telling the agent to actually quote it verbatim) — already written in the canonical prompt, blocked on C2's fix like every other SP rule. | Code + prompt | N12 done; SP7 blocked on C2 |
| 20 | SP6 — inline `[inferred]`/`(derived)` labeling rules (from July 21 chat) | System prompt | ~10 lines |
| 21 | N16 — PARTIALLY ADDRESSED 2026-07-29: `importers/capacity_metrics.py`'s "trustworthy" `capacity_signal_from_timepoints()` path already avoids the scale-verification problem by construction (computes % from two absolute CU-second columns, never trusts a raw %-labeled column). Its `analyze_timepoints()`'s `reportedPeakPct` DOES read a raw "%" column with no scale check — now documented in-code as diagnostic-only, but the correct scale factor for that specific column remains genuinely unverified if it's ever promoted to authoritative use. | Code | Verify if ever promoted to authoritative |
| 22 | ~~EV1~~ — DONE 2026-07-29: all 6 golden eval cases authored in `agent_cases.json` | Eval | Done |
| 23 | ~~EV3~~ — DONE 2026-07-29 (3 of 4): Olivia absence, sum-of-users gap, and abstain-redirect cases authored. The "repeated identical question 3x" case does NOT fit the eval harness's current design — `investigate()` produces exactly one output per case, so a 3-turn escalating-response test can't be scored as a single golden case without a harness change. Needs either multi-turn scoring support added to `score_investigations.py`, or this stays a manual/mine_evals-verified behavior rather than a golden case. | Eval | 3/4 done, 1 needs harness change |
| 24 | EV2 — run mine_evals on conversation log | Human task | 30 min |
| 25 | M1 — add DB listing to extract_measures.py (low priority now — D1 core goal met via fingerprinting + external validation) | Code | ~10 lines |
| 26 | Autonomous deployment | Deploy | Large |
| 27 | Memory tables | Deploy | Medium |
| 28 | V1 — turn manual validation (Section 12) into automated `validate.py` checks | Code | Large |
| 29 | N13 — startup health probes for the 3 semi-verified data connections (from July 21 chat) | Code | ~30 lines |
| 30 | N14 — runtime `MetricValue` dataclass / metric-type stamper (from July 21 chat) | Code | Medium |
| 31 | N15 — verify whether the chat app's tool loop still duplicates `agent/loop.py` (status unverified pending Claude Code's Task 1/2 architecture work — see tasks/todo.md) | Code | Verify first |
| 32 | Group 5/6 measures still open (Usage variance, P95 exact formula, basecore/non-billable splits, `Cumulative CU Usage % Preview`/`(s)`) — now have official qualitative descriptions or real measure names but not exact formulas (Section 12.11) | Research | Low priority |
| 33 | Pass rate vs. capacity-wide Success% reconciliation (Section 12.10; 2.1pp gap, unexplained, not yet used by agent) | Research | Low priority |
| 34 | B2 — blank ExecutingUser cross-ref fallback — needs its own dedicated pass (requires wiring two new data sources -- Activity Events cross-reference + REST API owner lookup -- into the attribution pipeline; genuinely needs design decisions, not a quick patch) | Code | Medium |
| 35 | UX1/UX2 — check cards + loading indicator — needs its own dedicated pass (real frontend/React work needing the frontend-design skill and live browser rendering to verify; not something to draft blind) | Frontend | Medium |
| 36 | UX3/UX4 — audience views wired to chat — same reasoning as UX1/UX2 above | Code + frontend | Medium |
| 37 | N2 — FUAM integration | Code | Large |
| 38 | E3 — multi-workspace loop | Code | Medium |
| 39 | E4 — staleness check | Code | Small |
| 40 | N4 — verify 3 deploy integration points | Deploy | Verify only |
| 41 | ~~D3~~ — DROPPED 2026-07-29 per explicit instruction. No `.docx` file exists anywhere in the accessible filesystem; confirmed via an exhaustive search (repo tree + full user directory + a broad `*.docx` glob). Nothing to update, nothing to delete. | Docs | Dropped |
| 42 | D4 — delete dead Node.js reference app + README/STATUS.md cleanup (agreed, deferred until build complete) | Docs/cleanup | Small |

---

## SECTION 12 — FORMULA VALIDATION SESSION (full findings, 2026-07-27)

This section documents a live validation session run directly against the Fabric Capacity Metrics
app using real Excel exports (DAX Studio schema dump + Compute/Throttling/Overages/Multi-metric-
ribbon tab exports) rather than assumptions from Microsoft's docs. Every formula the agent depends
on was tested against real production data. Results are organized by the same Group 1–5 taxonomy
used when this work started (Group 1 = irrelevant UI measures, skipped entirely).

### 12.1 — Methodology

1. Pulled the full DAX schema via DAX Studio against the Capacity Metrics app's composite semantic
   model — confirmed ~280 measures are `EXTERNALMEASURE` stubs (composite model chains to a
   remote base model via DirectQuery; SPN auth to that base model is blocked by Microsoft).
2. Rather than chase the base model schema (Option 1/2/3 from Section 6), used **behavioral
   fingerprinting** (Option 4): captured a real spike as it happened, exported the underlying
   tables from the Timepoint Detail / Throttling / Overages / Multi-metric-ribbon pages, and
   reverse-engineered each formula by testing candidate equations against the app's own displayed
   numbers until they matched exactly.
3. Worked through consecutive 30-second windows during a live capacity event (SKU F512, three
   separate spikes captured: a moderate one ~10:30–10:33 AM, a larger one ~11:55 AM–12:02 PM that
   crossed the actual throttle threshold, and its ~20-minute decay tail ending ~12:20 PM).
4. Where single-window snapshots weren't enough to prove a formula (the burndown recursion, the
   throttle percentage series), got full-day and full-14-day chart exports and tested every
   consecutive row programmatically — turning "looks right" into "zero error across N rows."

### 12.2 — Group 2: Core CU% measures — CLOSED (except 3 items, see below)

| Measure | Formula | Verification |
|---|---|---|
| SKU CU by timepoint | `= TimepointCU_s` | ✅ Exact, multiple sources |
| Total CU (s) | `TimepointCU_s × 10` | ✅ Exact on 1,200+ individual operation rows, zero variance |
| SKU CU by timepoint % ("% of Base capacity") | `TimepointCU_s / (base_CU × 30)` | ✅ Exact across 2 SKUs (F1024, F512), 6+ windows, 2 independent export sources (Timepoint Detail AND the raw Utilization chart export) |
| CU limit | `base_CU × 30` | ✅ Exact |
| Background billable % | Same formula, background-ops filter | ✅ Exact |
| Interactive billable % | Same formula, interactive-ops filter | ✅ Exact |
| Peak utilization % | `MAX(Total CU%)` across the full date range | ✅ Exact — matched the app's `19898.34%` header figure to 4 decimal places from a 3,132-row 14-day sample; found the actual spike (Jul 16, 10:15:30 PM, ~199× base) |
| Background/Interactive **non-billable** % | Presumed same formula, non-billable filter | ⚠️ Structurally present as separate columns but always **BLANK** (not literal `0`) across every sample checked so far — now confirmed on a much larger sample (2,889 rows, full 30-day window, Item History tab; see Section 12.10) as well as the original 14-day check. The BLANK-not-zero distinction matters: DAX `BLANK()` typically means the filtered table had zero matching rows, consistent with "this never occurs in this tenant" rather than a pipeline/export gap. Still no live nonzero example to test the formula against — low priority; may simply not occur in this tenant. |
| SKU CU by timepoint **basecore** | Presumed: base reserved capacity only, excludes autoscale-purchased extra capacity | ⚠️ No autoscale activity occurred in the sample window — nothing to test against. Low priority. |
| **Avg utilization %** | `MEAN(Total CU%)` across the full date range (formula presumed, same shape as Peak) | ⚠️ **Blocked by a data-access limit, not a formula mystery.** The app's own chart export tool only returns a **sampled subset** — ~3,132 rows out of a possible ~40,320 (14 days × 24h × 120 windows/hr), roughly 7.8% coverage, skewed toward active periods. Our sampled mean (42.46%) came out well above the true header figure (28.11%) because idle/quiet windows are underrepresented in the export. **Resolution: the agent doesn't need this reconciled** — its own Eventhouse collector (Layer A, Real-Time Hub) captures every single 30-second window with no sampling, so it can compute its own true average directly rather than trying to reproduce the app UI's sampled figure. |

### 12.3 — Group 3: Carry-forward / burndown chain — FULLY CLOSED, zero error

This is the most load-bearing result of the whole session — it directly unblocks **A2**.

**Formula (final, corrected):**
```
Cumulative[T] = Cumulative[T-1] + Add[T-1] + Burndown[T-1]
```

Two critical, non-obvious details that took several iterations to nail down:

1. **The recursion is lagged by one window.** It's the *prior* window's Add/Burndown that gets
   folded into the current Cumulative — not the current window's own Add. Confirmed exactly
   across 4 hand-captured consecutive windows (10:31:30–10:33:00 AM) before being confirmed at
   scale.
2. **Burndown is stored as an already-negative number** (a debit), not a positive value to
   subtract. Using `− Burndown[T-1]` on already-negative data double-flips the sign and silently
   breaks the formula, but *only* during actual decay phases when Burndown ≠ 0 — which is exactly
   why an early test looked "93.7% correct" (1,665 of 1,777 rows matched) with all the errors
   clustered in one 4.5-minute decay window (12:15:30–12:20:00). Once corrected to
   `+ Burndown[T-1]`, **all 1,777 consecutive 30-second windows across the full day matched to
   zero error.**

**Expected burndown in minutes — also solved, zero error:**
```
Expected_burndown_in_minutes = Cumulative% / 200
```
A flat constant divisor (200 percentage-points-per-minute assumed nominal burndown rate),
confirmed exactly across all 201 nonzero rows in the same file.

**Overage reference line:** seen as a column, always `0` in every sample — likely just a static
chart baseline, not a real threshold. Not worth further investigation.

**Practical outcome:** `capacity_burndown_chain()` (A2) can now be written with complete
confidence — no guessing required, both formulas proven against a full day of real production
data.

### 12.4 — Group 4: Throttle threshold measures — FULLY CLOSED

This directly unblocks **A1**, the single highest-priority code gap in the whole document.

**All three threshold constants confirmed identical in structure:**
```
Interactive_delay_threshold      = 1  (constant, every row, 14 days)
Interactive_rejection_threshold  = 1  (constant, every row, 14 days)
Background_rejection_threshold   = 1  (constant, every row, 14 days)
```
Same comparison logic applies to all three: `value >= 1.0` = threshold crossed. A1's gate can use
one shared comparison function across all three fields.

**The three percentage signals — tested for whether they're derivable from data the agent already
collects (they are NOT, with one partial exception):**

| Signal | Correlation with matching CU% | Verdict |
|---|---|---|
| Interactive delay % | Single-point match looked close (~0.2pt gap) initially, but a full 1,758-point test against 7 different rolling-window hypotheses (1/2/5/10/20/30/60-min) found correlation only ~0.73–0.78 with high error (~20–22 points MAE) — **the single-point match was coincidence, not a pattern.** | Must extract directly — NOT derivable from CU% |
| Interactive rejection % | Same result as Interactive delay — weak correlation (0.7359 current-value, best rolling window 0.7794), high error | Must extract directly — NOT derivable from CU% |
| Background rejection % | Strong correlation (0.9889) with Background CU%, low error (2.49pt MAE), but not identical — roughly a 0.65× scaling relationship (Background Rejection averages 4.72% while Background CU% averages 7.22%) | Extract directly, but note the relationship — likely a dampened/smoothed transform of Background CU%, worth a closer look later but not urgent |

**Real-world severity picture confirmed over the full 14 days:**
```
Interactive Delay:      peaked ~126%  → CROSSED (real event, 7/27, 11:55 AM–12:02 PM)
Interactive Rejection:  peaked  39.0% → never crossed
Background Rejection:   peaked  16.2% → never crossed
```
This is a clean, real confirmation of Microsoft's documented escalation hierarchy: Delay fires
well before Rejection, and Interactive pressure runs far hotter than Background pressure in this
tenant. The agent got a genuine real-world example of "delay active, rejection never triggered" to
reason from — exactly the distinction the throttle gate needs to report (two severity levels, not
a collapsed yes/no flag).

**Practical outcome for A1:** `_windows()` must extract all three percentage fields directly from
the event payload (confirmed necessary, not a shortcut-able derivation), compared against the
shared `threshold = 1.0` constant. Stage-2 gate logic can now be written with a single shared
comparison function.

### 12.5 — Group 5: Health page / time-window measures — mostly untouched, not blocking

| Measure | Status |
|---|---|
| P95 interactive delay | Untested |
| P95 interactive rejection | Untested (blocked on having more Interactive Rejection data beyond what's already gathered) |
| P95 background rejection | Untested |
| Usage variance | Untested — formula unknown, likely stddev or similar over the CU% series |
| Successful / Failed / Rejected counts | **Status taxonomy corrected (Section 12.10):** the real values are `Success` / `Failure` / `InProgress`, not "Failed / Rejected" as this row previously assumed — confirmed via the item-history status breakdown export (81.68% Success, 18.31% Failure, 0.005% InProgress). Not a formula — trivially derivable via `groupby(Status).count()` on data already collected, just correct the three literal status strings if this is ever implemented. |
| Throttling (s) column (per-operation) | **UPDATED — no longer always 0.** The Item History tab's item×status detail export (56 rows, Section 12.10) surfaced real nonzero values once the item breakdown was pulled specifically. However, strong evidence (exact divisibility by 20 across every nonzero value; magnitude implausible as literal seconds — would exceed the entire wall-clock window by 15.9×) points to the "(s)" unit label being wrong — the true unit is very likely milliseconds. See **N19**. Also confirmed: throttling accumulates on `Success`-status operations too (77.9% of total), not just failures — throttling delays, it doesn't necessarily reject. |

None of these (other than the now-corrected Throttling(s) row, see N19) are referenced by any
current gap on the master list (A1, A2, etc.) — low priority unless a new use case specifically
calls for them.

### 12.6 — Multi-metric ribbon chart: all 4 tabs (Users / Operations / Duration / CU) — COMPLETE

All four tabs of this previously-unexplored data source were pulled and cross-analyzed.

**Users tab** — hourly distinct-user counts by Item Kind, full 14 days (429,120 possible rows,
only 2,632 populated — most Item Kinds have zero activity in this tenant):

| Item Kind | Users/hr (mean) | Users/hr (max) |
|---|---|---|
| Dataset | 24.07 | 91 |
| Activator | 1.01 | 2 |
| EventStream | 1.00 | 1 |
| FabricEvents-CapacityUtilizationEvents | 1.00 | 1 |
| KustoEventHouse | 0.53 | 2 |
| Report | 0.14 | 2 |
| PaginatedReport | 0.09 | 1 |
| DataflowFabric | 0.06 | 2 |

**Operations tab** — hourly operation counts by Item Kind, same 14-day window:

| Item Kind | Ops/hr (mean) | Ops/hr (max) |
|---|---|---|
| Dataset | 12,170.01 | 47,052 |
| EventStream | 47.89 | 56 |
| Activator | 10.02 | 18 |
| KustoEventHouse | 8.90 | 60 |
| FabricEvents-CapacityUtilizationEvents | 5.97 | 6 |
| Report | 0.42 | 13 |
| PaginatedReport | 0.18 | 3 |
| DataflowFabric | 0.09 | 3 |

**Duration tab** — total wall-clock duration (seconds) by Item Kind, same 14-day window. This is
where the "system account" theory got its strongest confirmation:

| Item Kind | Avg duration/op | Peak single-hour total duration |
|---|---|---|
| Activator | **42.0 minutes** | 8.2 hours |
| EventStream | **10.0 minutes** | 9.3 hours |
| Dataset | 0.02 min (1.3 sec) | 20.8 hours (many parallel ops, not one long op) |
| KustoEventHouse | 1.0 minute | 1.1 hours |
| Report | 1.0 minute | 0.2 hours |

Activator and EventStream average 42 and 10 minutes per "operation" — wildly longer than
everything else — which is exactly what you'd expect from a persistent listener/trigger process
that's technically "running" the whole time it waits for the next event, rather than a query or
refresh that starts and finishes quickly.

**CU tab** — CU-seconds consumed by Item Kind, same window. Combined with Duration to compute a
new derived metric, **CU-intensity (CU-seconds consumed per second of wall-clock duration)**,
which cleanly separates "genuinely compute-heavy" from "long-running but mostly idle":

| Item Kind | CU/op | CU-per-duration-second | Verdict |
|---|---|---|---|
| Dataset | 53.56 | **41.48** | Genuinely CPU-hungry while running |
| Report | 1,753.80 | **28.34** | Genuinely CPU-hungry while running |
| PaginatedReport | 299.97 | **22.73** | Genuinely CPU-hungry while running |
| FabricEvents-CapacityUtilizationEvents | 0.80 | 1.60 | Brief but real work each tick |
| KustoEventHouse | 73.80 | 1.25 | Moderate |
| Activator | 55.78 | **0.022** | Almost entirely idle (45× less intense than Dataset) |
| EventStream | 10.99 | **0.018** | Almost entirely idle (2,270× less intense than Dataset) |

**Key finding — produced gap N5** (see Section 1), now backed by all FOUR independent signals
(user count, operation count, duration, and CU-intensity) rather than just one: `EventStream`,
`FabricEvents-CapacityUtilizationEvents`, and `Activator` fail every relevance test
simultaneously — single-user, long-running-but-idle, low CU-intensity — which makes excluding
them from concentration (and potentially duration-based) alerting a well-justified, not
arbitrary, decision. `Dataset` is confirmed as the one Item Kind in this tenant with genuine,
variable, CPU-intensive multi-user activity worth alerting on.

### 12.7 — Health page schema and cross-capacity/cross-region findings

A second data surface explored beyond the Compute/Throttling/Overages pages used for Sections
12.2–12.4: the **Health** tab, which rolls multiple capacities up into one comparison table.

**Full column schema confirmed (all fields scoped to "last 24 hours" per their actual internal
names, not just the UI label):**
```
Capacities[Capacity Id]
Capacities[Capacity name]
Average_utilization (last 24 hours)
Risk_status (last 24 hours)
Throttling_s (last 24 hours)
P95_interactive_delay (last 24 hours)
P95_interactive_rejection (last 24 hours)
P95_background_rejection (last 24 hours)
Usage_variance (last 24 hours)
Users (last 24 hours)
Successful / Failed / Rejected operations (last 24 hours)
```

**`Risk_status` is a leveled severity enum, not a binary Healthy/Unhealthy flag.** Confirmed
values seen across two different regions and five different capacities:
```
"Healthy"
"2 - Throttling"
"4 - Interactive Rejection"
```
The number appears to indicate escalating severity and the text names which specific problem is
driving the score — directly useful for the agent's verdict logic if the full scale (likely 1–4
or similar) can be confirmed with more examples. Not yet seen: level 1 or level 3, or a
background-rejection-specific level.

**UPDATE (Section 12.11) — full scale now confirmed via official Microsoft docs, not just
behavioral samples.** The field is officially named `Health` (not `Risk_status`) and has 10
possible text states with exact triggering thresholds, not just the 3 sampled here:

| State | Exact trigger |
|---|---|
| Healthy | no throttling |
| Suspended | capacity suspended |
| At Risk of Throttling | ≥1 window, 10-min interactive % > 90%, overage OFF |
| At Risk of Overage Billing | same, overage ON |
| Overage Billing Active | 10-min interactive % > 100%, overage ON |
| Throttling | 10-min interactive % > 100% *(matches the "2 - Throttling" sample above)* |
| At Risk of Interactive Rejection | 60-min interactive % > 90% |
| Interactive Rejection | 60-min interactive % > 100% *(matches the "4 - Interactive Rejection" sample above)* |
| At Risk of Background Rejection | 24-hr background % > 90% |
| Background Rejection | 24-hr background % > 100% |

The numeric prefixes seen in real exports ("2 -", "4 -") are very likely a sort-key convention
applied on top of these 10 named states, not necessarily a strict linear 1–10 (or 1–4) scale —
the official docs never mention a numbering scheme at all, only the 10 named states above. See
**N20** for the smoothing-window detail this table also reveals (10min/60min/24h, one per
threshold type) and the card-vs-state "throttled" definition mismatch.

**Cross-capacity real-world data captured (2 regions, 5 capacities):**

| Capacity | Region | SKU | Risk_status | Avg util % (24h) | Throttling (s) | P95 int. delay | Users |
|---|---|---|---|---|---|---|---|
| answeritfabricprd | East US | F4 | 4 - Interactive Rejection | 52.89 | 6,000 | 404.96 | 8 |
| entreportingfabricprd1 | East US 2 | F512 | 2 - Throttling | 31.60 | 4,260 | 57.52 | 170 |
| analystcommunityfabric | East US 2 | F2 | 2 - Throttling | 17.00 | 1,940 | 40.09 | 18 |
| entreportingfabricsvt1 | East US 2 | F64 | Healthy | 25.76 | 0 | 28.56 | 51 |
| digitalintelligenceanalytics | East US 2 | F4 | Healthy | 7.94 | 0 | 27.58 | 5 |

`entreportingfabricprd1` is the same capacity used for every other formula validated in this
document (Sections 12.2–12.4), so this Health-page snapshot is a cross-check against a data
source we hadn't touched yet for that same capacity — useful corroboration that its "2 -
Throttling" status is consistent with the confirmed throttle event found in Section 12.4
(Interactive Delay crossed ~126% on 7/27).

**P95 interactive delay — same sampling limitation as Avg utilization % (Section 12.2), confirmed
a second time on a different field:**
```
Health page shows (entreportingfabricprd1):  57.52
Computed P95 from raw series, 24h window:    69.10  (241 sampled rows, expected ~2,880 at full cadence)
Computed P95 from raw series, 28h window:    56.79  (281 sampled rows)
```
The computed values land in the right neighborhood but don't exactly match, and the row counts
confirm the same ~8–10% sampling rate found for Avg utilization % in Section 12.2. This is not a
new formula problem — it's the same UI-export-sampling limitation showing up again on a
different field. Reinforces that **any Group 5 (Health page) percentile figure should be treated
the same way as Avg utilization %**: formula is presumably a genuine percentile calculation, but
can only be computed exactly from the agent's own complete Eventhouse data, not reproduced from a
UI export.

**Operational discovery — which exports work on the Health page:** the five individual KPI cards
at the top (# Capacities, Avg utilization %, # Throttled capacities, # Interactive rejected
capacities, # Background rejected capacities) do **not** have an exportable underlying table —
every attempt returned an empty "Data connected to Power BI: Click refresh to update" placeholder,
regardless of retry. The **detail table below the cards** (the one with Capacity name / SKU /
Health / etc. columns) is what actually carries exportable data, and it contains everything the
five cards summarize anyway. Future sessions should go straight to that table and skip the
individual card export attempts entirely.

**Not yet explored:** the full Risk_status scale (only 2 of what's likely 4+ levels seen), and
whether Usage variance has a confirmable formula (still fully untested — no candidate formula
attempted yet, unlike the P95 fields which at least have a plausible percentile hypothesis).

### 12.8 — Session-wide lessons worth remembering

- **A formula that looks ~94% correct can still be completely wrong** — the burndown sign error
  only showed up in specific conditions (active decay), making a genuinely broken formula look
  "mostly fine" until tested at scale across enough rows to hit the edge case.
- **A single matching data point proves nothing** — the Interactive Delay ≈ current-CU% hypothesis
  looked solid on one sample and completely fell apart across 1,758 samples. Always prefer testing
  a hypothesis against as many independent rows as practically available before trusting it.
- **UI export tools can silently sample** — the Avg utilization % mismatch wasn't a formula bug,
  it was discovering that the app's own "export data" feature doesn't return the complete
  population for large date ranges. Worth remembering before assuming any UI export is exhaustive.
- **Cross-referencing against manually-transcribed numbers from earlier in a long session is
  risky** — a scale/sign transcription slip (recording `10.8865` when raw was `0.108865`, needing
  a ×100 correction found only by comparing against a screenshot) nearly sent the burndown
  investigation down a wrong path (a false "SKU autoscale rescaling" theory) before the sign-
  convention discovery resolved it cleanly. Prefer testing formulas within one internally-consistent
  file over stitching together values recorded by hand across many messages.
- **Reused export filenames silently overwrite each other.** The app's default export filename
  (`data (4).xlsx`, `data (5).xlsx`, etc., saved by the browser as `data__4_.xlsx` on upload)
  gets reused across many different exports over a long session. Earlier files with the same
  name are silently replaced in the uploads folder — this cost us the original full-day
  Interactive Delay % export partway through the session, requiring a detour into the Windows
  Recycle Bin (`C:\$Recycle.Bin\...`) hunting for the right deleted duplicate among 9+ candidates
  all named `data (4).xlsx` from different points in the session. Ultimately abandoned that
  recovery attempt (the file-copy tool became unresponsive) and just re-exported fresh instead —
  which was faster than the recovery attempt would have been anyway. **Lesson: rename exports to
  something descriptive before uploading** (as was done successfully later in the session, e.g.
  `7-27_Utilization_CU_percent_Over_Time.xlsx`, `Overages_percent_overtime.xlsx`) rather than
  relying on the default `data (N).xlsx` naming, especially in a long multi-export session.
- **Single-value "KPI card" visuals don't export real data — the detail table underneath does.**
  Confirmed twice: once on the Overages chart (which turned out to be a visual rendering of the
  same Burndown table already exported, nothing additional to get) and once on the Health page's
  five summary cards (all five came back as empty "click refresh" placeholders, while the detail
  table below them had everything, including data the cards themselves summarize). When an export
  comes back genuinely empty, check whether the visual is a single-number card or chart with a
  richer table nearby, and export that instead of retrying the same empty visual.
- **A wrong theory can look plausible right up until someone checks the UI directly.** When the
  burndown recursion broke between two consecutive windows, the first hypothesis floated was that
  the Overages chart's 10-min/60-min/24-hr reference-window toggle was changing the underlying
  Burndown table's Add%/Cumulative% values — a plausible-sounding theory since both visuals sit
  on the same page. This was wrong, and only got corrected because the person doing the exports
  pointed out directly that "the burndown table doesn't have an option for that, that's the
  overages table" — i.e. the toggle belongs to a different visual entirely. Ruling out that theory
  is what freed up the actual investigation to find the real bug (the one-window recursion lag).
  Worth remembering: a theory that would explain the data doesn't mean it's correct, and someone
  with eyes on the actual UI can rule out a wrong theory in one sentence what would otherwise take
  several more data points to disprove analytically.
- **Exported columns that are always `1` are boundary flags, not values — don't confuse them with
  the real measure of the same name.** Several raw exports contain columns like `[CU_limit]`,
  `[SKU_CU_by_timepoint__]`, and `[Interactive_delay_threshold]` that are constant `1` in every
  row — these are boolean-style presence/threshold flags, completely different from the actual
  **measure** `CU limit` (`= base_CU × 30`, a real number like 15,360 or 30,720) discussed in
  Section 12.2. Anyone writing the collector directly against these export column names should
  double-check which one they're reading — a field literally named `CU_limit` in one export is
  not the same thing as the CU limit value used elsewhere in this document.

### 12.9 — Loose threads from the original EXTERNALMEASURE decode reconciled

Before this document existed, an earlier exchange in this conversation manually decoded the DAX
Studio schema dump and produced several hypotheses about what specific measures compute. Three of
those threads were never connected back to what Sections 12.2–12.7 later actually proved or
found — reconciling them here:

**1. Two competing, unreconciled hypotheses for "SKU CU by timepoint basecore"** — the original
schema-decode exchange guessed this measure means *"Interactive + Background billable only
(excludes Preview/non-billable)"* — i.e. a billable-vs-non-billable split. Section 12.2 above
instead guessed it means *"base reserved capacity only, excludes autoscale-purchased extra
capacity"* — a completely different split (base-vs-autoscale). **Neither hypothesis has been
tested**, and they are not the same thing. This should be flagged as a genuinely open question
with two candidate explanations, not presented as if only one guess exists.

**2. `Item History Summary[CpuTimeMs]` ties the Metrics app schema to Workspace Monitoring — a
real, useful bridge finding that never made it into this document.** The original DAX schema dump
showed a `CpuTimeMs` column on the `Item History Summary` table (TableID 250) inside the Capacity
Metrics app's own schema. This is the *same field* Workspace Monitoring exposes via
`SemanticModelLogs[CpuTimeMs]` — meaning the Metrics app and Workspace Monitoring are drawing
from a consistent underlying source for this specific proxy metric. This is a small but genuinely
useful confirmation: it means the agent's `user_cpu_share_pct` proxy calculation (B3) is
consistent with what the Metrics app itself would show for the same field, if that page were
ever surfaced to a human for manual cross-checking.

**3. An old hypothesis about Health-page measures was quietly confirmed later, uncredited.** The
original schema-decode exchange guessed that Health/time-window measures are "filtered
aggregations over the Usage Summary (Last 1 hour) and Usage Summary (Last 24 hours) tables." The
actual Health page schema found in Section 12.7 confirms this almost exactly — every single
Health-page field name literally includes "(last 24 hours)" in its internal name
(`Average_utilization__last_24_hours_`, `P95_interactive_delay__last_24_hours_`, etc.). Worth
noting explicitly: the early reverse-engineering guess was right, and Section 12.7 is its
confirmation — these weren't two disconnected findings.

**4. The exact capacity used for every formula in this document changed SKU during the session
itself — a live, real-world event, not just "we happened to test on 2 SKUs."** `entreportingfabricprd1`
showed as **F1024** in the very first screenshot of this session and as **F512** in every
screenshot afterward. This is the *same capacity*, not two different ones — meaning a real
resize/autoscale event happened on the actual tenant while this investigation was in progress.
This is worth keeping in mind for two reasons: (a) it's a nice piece of independent evidence that
the formula (`base_CU × 30`) is robust to a real capacity-size change, since both SKUs verified
exactly; and (b) it opens a possible future angle on the unresolved "basecore" question above —
if a similar resize happens again during a future session, comparing CU figures immediately
before and after the change could help determine whether "basecore" tracks base-reserved-only
vs. billable-only.

### 12.10 — Item History tab export session (8 files, same day as 12.1–12.9)

A full export set from the Fabric Capacity Metrics app's **Item History** tab (distinct from the
Compute/Throttling/Overages tabs used in 12.2–12.4, and from the Health tab used in 12.7):
`CU(s) by item`, `CU(s) by workspace`, `Smoothed CU % over time` (2,889 rows, 30-day window),
two daily trend exports (`SumOperations` and `SumCU(s)` by date), workspace-level pass-rate/
compute-share, and item×status throttling detail (56 rows). Analyzed with `pandas`/`openpyxl`,
not just visual inspection — every cross-file total was reconciled numerically.

**Produced N16–N19** (Section 1) — the fraction-scale UI-export defect, the
`Monitoring_Eventstream` Date-join failure, the item-display-name collisions, and the
`Throttling(s)` unit mislabeling. The remaining findings below didn't rise to a numbered code gap
(either because nothing in the agent's own code currently touches the field, or because they're
methodology lessons rather than defects) but are recorded here so they aren't re-discovered from
scratch in a future session.

**Sampling behavior is grain-dependent, not a fixed app-wide rate.** The Smoothed CU % chart
samples at **3.33%** (2,889 of ~86,645 possible 30-second windows over the 30-day span) — lower
than the ~7.8% found in Section 12.2 on the Compute-page chart. More importantly: within this
same Item History tab, `v__compute_operation` (workspace grain) sums to **exactly 1.0** across all
26 workspace rows — complete, no sampling — while `v__compute_operation_detail` (item×status
grain, same underlying measure family, same "Records are sampled" caption) sums to only **0.472**
— more than half the population is missing. **A caption confirming completeness at one grain does
not carry over to a different grain on the same page, even under the same visual caption.** Each
grain needs its own completeness check; never infer detail-table completeness from a rollup being
complete.

**`v__compute_operation` is a naming trap** — confirmed (to 3.5×10⁻⁵ precision, cross-checked
against the independently-computed CU-share from the CU-by-workspace export) to be a **CU-cost
share**, not an operation-COUNT share, despite the field name literally containing the word
"operation." Real-world consequence in this data: `Enterprise E-Comm` runs 34.3% of all operations
by count but only 6.7% of CU cost share, while `Enterprise Sales` is the reverse shape (fewer,
much more expensive operations). Reading this field name at face value would lead to exactly the
wrong conclusion about which workspace is "busiest." The same semantic almost certainly applies to
`v__compute_operation_detail` at the item×status grain. Worth remembering alongside the `[CU_limit]`
naming trap already documented in 12.8 — this project has now found two separate instances of a
field name that actively misleads about what it measures.

**Cross-dimension totals don't reconcile exactly, even at full aggregation:**
- CU-by-item total (460,802,408) vs. CU-by-workspace total (460,776,254): diff 26,155 (0.0057%)
- CU-by-date total (460,284,874, including the blank-date bucket) vs. CU-by-item total: diff
  -517,535 (-0.11%)

Small, but real and unexplained. Not chased further — recorded as a standing expectation: don't
treat any single rollup in this app as authoritative to the last digit; ~0.01–0.1% cross-dimension
variance is normal background noise in this app, not a bug to hunt down.

**Pass rate (workspace grain) does not reconcile with the capacity-wide Success% breakdown, even
properly weighted.** Weighted-average `Pass rate` (weighted by op count across all 26 workspaces,
the one blank-name workspace excluded from both numerator and denominator) = **0.838**. The
capacity-wide Success% from the status breakdown = **0.817**. A 2.1-point gap survives correct
weighting. Two live hypotheses, neither confirmed: (a) `Pass rate` uses a different grain (e.g.
scheduled-refresh pass/fail rather than raw per-operation status); (b) independent sampling noise
between the two separately-pulled exports. Open question — flag if anyone builds on `Pass rate`
directly.

**Non-billable % re-confirmed on a much bigger sample, and reframed as BLANK not 0** (see the
updated 12.2 table row) — 100% NaN/blank across all 2,889 rows of a full 30-day window, not just
the original 14-day check. Still no live nonzero example.

**Status taxonomy corrected:** the real values are `Success` / `Failure` / `InProgress` (see the
updated 12.5 table row) — this document previously assumed "Failed / Rejected" without having
confirmed the literal strings.

**Formula scorecard for this session — what got validated vs. what's still open:**

*Newly validated:*
- **`v__compute_operation` = CU-cost share** (`item_or_workspace_CU / total_CU`) — confirmed to
  3.5×10⁻⁵ precision against an independently-computed share. Genuinely new formula, not
  previously documented anywhere in Groups 2–5.
- **The ×100 fraction-scale convention (A1's defect class) confirmed on a second, independent
  data surface** — previously only shown on the streaming API; now also shown on the Capacity
  Metrics app's own UI export layer (`Smoothed CU % over time` chart). Not a new formula, but
  widens the known scope of an existing one — any future UI-export parser must check this
  independently, not assume A1's streaming-API fix covers it.
- **Status taxonomy** (`Success`/`Failure`/`InProgress`) — corrected a previously-assumed fact,
  not a formula, but closes an open unknown.

*Still not found / not validated by this batch:*
- **`Usage variance`** — no candidate formula attempted here either. Still completely open, no
  progress since 12.7.
- **P95 fields** (interactive delay/rejection, background rejection) — not touched by this export
  set at all.
- **`Throttling_s` at the Health-page (capacity-level, 24h) grain** — this batch only reached the
  *item*-level version and found strong evidence its unit is wrong (ms mislabeled as s, N19). The
  capacity-level Health-page formula — whether it sums these item-level ms-figures or computes
  something else entirely — remains unconfirmed.
- **`Pass rate`** — formula still unknown; this batch only found evidence that it does NOT
  reconcile with the capacity-wide Success% figure (a 2.1pp gap), not what it actually computes.
- **`SKU CU by timepoint basecore`** — untouched this session, still the two competing untested
  hypotheses from 12.9.
- **Non-billable %** — reconfirmed as BLANK on a much bigger sample (2,889 rows vs. the original
  14-day check), but still no live nonzero example exists to test the formula against.

### 12.11 — External validation pass: FUAM source code + official Microsoft docs (same day)

Everything in Sections 12.1–12.10 came from behavioral fingerprinting — testing candidate
formulas against the app's own displayed numbers because DAX Studio only ever returned
`EXTERNALMEASURE` stubs (Section 12.1). This pass tried something different: **going outside the
app entirely**, to public sources that don't have that limitation.

**Source 1 — `microsoft/fabric-toolbox` GitHub repo (FUAM's own source, public).** FUAM connects
directly to the real base semantic model, not the composite shell DAX Studio sees — so its own
DAX queries, visible in the repo's community-reported GitHub issues (schema-mismatch bug reports
where users pasted their actual working DAX), contain **real, non-stubbed measure names**.
Confirmed real names for every measure already fingerprinted in Sections 12.2–12.4:

```
'All Measures'[Background billable CU %]           'All Measures'[Interactive billable CU %]
'All Measures'[Background non billable CU %]        'All Measures'[Interactive non billable CU %]
'All Measures'[SKU CU by TimePoint %]               'All Measures'[SKU CU by TimePoint]
'All Measures'[CU Limit]
'All Measures'[Dynamic Interactive Delay %]         'All Measures'[Dynamic Interactive Rejection %]
'All Measures'[Dynamic Background Rejection %]
'All Measures'[Interactive rejection threshold]     'All Measures'[Background rejection threshold]
'All Measures'[Carry Over add %]                    'All Measures'[Carry Over burndown %]
'All Measures'[Cumulative carry over %]              'All Measures'[Overage reference line]
'All Measures'[Expected burndown in minutes]
```

The non-billable measures are confirmed to be real, correctly-defined measures in the schema —
supports the "BLANK because zero matching rows, not because the field is broken" interpretation
recorded in 12.2/12.10, rather than a naming or pipeline problem.

**Two measures surfaced that were never fingerprinted, and don't map to anything in Sections
12.2–12.10:**
```
'All Measures'[Cumulative CU Usage % Preview]
'All Measures'[Cumulative CU Usage (s)]
```
Unknown formula, unknown relationship to any already-validated measure. "Preview" in the name is
suggestive given the still-open non-billable/basecore questions (Section 12.9's competing
hypotheses both involve a billable-vs-preview or base-vs-autoscale split) but nothing here
confirms either hypothesis — flagged as a new lead, not a resolution.

**A second FUAM query (item×operation×day grain, `Metrics By Item Operation And Day` table)
surfaced a field literally named `Throttling (min)`** — minutes, unambiguous, no "(s)" suffix
anywhere. Independent, external, community-sourced corroboration that Microsoft's own naming for
throttling duration is inconsistent across tables in this app — strengthens **N19**'s core finding
(the Item History Operation Detail export's "(s)"-labeled field is very likely not seconds) even
though this is a different table at a different grain, not a direct proof of N19's specific value.

**This same query also revealed a 5-status-plus-total taxonomy** (`Successful`/`Rejected`/
`Invalid`/`Failed`/`Cancelled`/`Operations`) at the item×operation×day grain — a THIRD distinct
status vocabulary, different from both the Item History Operation Detail export's 3-status set
(12.10) and the official Health-page docs' 6-status set (below). Produced **N21**.

**Source 2 — official `fabric-docs` GitHub repo** (the raw markdown source behind
learn.microsoft.com/fabric/enterprise/metrics-app-health-page). Authoritative, not community
guesswork or behavioral inference:

- **The full Health-page state machine** (10 states, exact triggering thresholds) — see the
  updated table in Section 12.7. Resolves the "only 2 of 4+ levels seen" gap completely.
- **The three throttle types use three different smoothing windows** (10min/60min/24h) and the
  summary KPI card's "throttled" definition (any single 30-second spike) differs from the
  per-capacity state's definition (10-minute smoothed value). Produced **N20**.
- **`Throttling (s)` at the Health-page (capacity-level) grain is officially confirmed to genuinely
  be seconds**: *"Total throttling seconds in selected time period."* No ambiguity, no unit
  mislabeling at THIS grain — narrows N19's scope to the item-level Operation Detail field
  specifically; the two are not the same field and not both wrong.
- **`Usage variance`** — official qualitative description, still no exact formula: *"A larger value
  for this field indicates a capacity having wide variance in the amount of utilization, whereas
  low variance is indicative of a steady state utilization rate."* Confirms the field measures
  dispersion/spread of utilization, not something else (a trend, a count, etc.) — but doesn't say
  whether it's a standard deviation, coefficient of variation, or something else. Still open, now
  grounded rather than pure guesswork.
- **P95 fields — officially confirmed NOT used in Health-state decisions**: *"For health status
  calculations, the actual values of interactive delay, interactive rejection, and background
  rejection are used, rather than their P95 values."* Purely informational for admins. Community
  discussion (Fabric Community forum, unconfirmed) speculates P95 may be computed on raw
  unsmoothed 30-second data rather than the smoothed values used for throttle decisions —
  consistent with, but not fully explaining, the P95 mismatch already found in 12.7 (which was
  blocked by UI export sampling, not necessarily a wrong formula guess).
- **Official "Optional columns" status taxonomy for the Health page**: `Rejected` / `Failure` /
  `Canceled` / `Successful` / `Invalid` / `InProgress` (6 statuses, single-L "Canceled") — note the
  spelling difference from FUAM's `Cancelled` (double-L) field above; treated as confirmation these
  are genuinely different underlying fields, not just two views of the same one. Third data point
  for **N21**.

**Practical outcome:** this pass didn't require a single new export or a live spike to capture —
everything came from reading public source code and public documentation. Worth remembering
alongside the Section 12.8 lessons: **when behavioral fingerprinting stalls on a specific
question, checking whether a community tool already solved the same problem (and published its
source) can be faster than continuing to fingerprint.**

### 12.12 — Full measure catalog via self-owned composite model (2026-07-29) — D1 Option 1 confirmed closed, ~230 real names obtained

**Methodology:** Power BI Desktop → Get Data → Power BI semantic models → live connection to
"Fabric Capacity Metrics" → "Make changes to this model" (converts the live connection into a
local model the person owns outright) → DAX Studio (auto-connected via the external-tools
ribbon) → `SELECT [Name], [Expression] FROM $SYSTEM.TMSCHEMA_MEASURES ORDER BY [Name]`.

**Result: every single measure still resolves as `EXTERNALMEASURE`**, byte-for-byte the same
conclusion as the original Section 12.1 session. **This closes Option 1 definitively** — see the
updated D1 entry in Section 6. The protection blocking real DAX text is enforced at the base-model
level itself; a new composite shell one layer removed, even one the person owns outright, doesn't
see through it any more than the original composite shell did.

**What this attempt produced anyway: the fullest measure catalog this project has ever obtained.**
~230 real measure names with real declared data types (DOUBLE, INTEGER, STRING, BOOLEAN, DATETIME,
CURRENCY) — more complete than either FUAM's community DAX queries (Section 12.11) or the official
docs. Formula text remains inaccessible, but the names alone resolve or reshape several open
questions. Full raw list: `MEASURE-CATALOG-RAW.md` (repo root, alongside this file).

**Resolved or newly precise:**

- **`Pass rate` has no time-window suffix at all** — unlike almost every other measure in the
  catalog (which are all suffixed `(last 1 hour)` / `(last 24 hours)` / `(last 7 days)`), `Pass
  rate` stands alone. This is a real, concrete clue toward the still-open Section 12.10 puzzle (a
  2.1pp gap between workspace-level Pass rate and the capacity-wide Success% breakdown that
  survived correct weighting): `Pass rate` may not be time-scoped the same way as the hour/day/week
  family at all, and may be answering a structurally different question rather than just a
  differently-windowed version of the same one.
- **`% compute operation` / `% compute operation detail` are the real official names** for what
  Section 12.10 found behaviorally and called `v__compute_operation`/`v__compute_operation_detail`
  — direct, exact confirmation.
- **The "t2" suffix mystery seen throughout this catalog is solved**: `Timepoint2 SKU`, `Timepoint2
  workload autoscale limit`, `End Time 2` confirm "t2" = **Timepoint 2**, a second/comparison
  timepoint concept threaded through dozens of measures (`Carry forward add % t2`, `Cumulative
  carry forward % t2`, `Expected burndown in minutes t2`, etc.) — never identified before this
  pass.
- **A THIRD independent confirmation that throttling duration is minutes, not seconds**:
  `Dynamic M1 throttling (min)`. Now three separate sources agree — this project's own
  divisibility-by-20 analysis (N19), FUAM's community-sourced `Throttling (min)` field
  (Section 12.11), and now this official schema catalog. N19 is about as solid as an
  unconfirmed-by-Microsoft finding can get.
- **P95 fields exist at both 1-hour AND 24-hour windows** — `P95 background rejection (last 1
  hour)`, `P95 interactive delay (last 1 hour)`, etc. Previously this document only knew of the
  24-hour versions (Section 12.7).

**More complex than previously framed:**

- **`basecore` is not one measure with two competing hypotheses — it's FOUR separate measures**:
  `SKU CU by timepoint basecore`, `...basecore item history`, `...basecore only`, `...basecore
  only preview`. The `...only` variant strongly implies a non-"only" version exists with a
  different denominator, and "preview" ties into the still-unexplained `Cumulative CU Usage %
  Preview` lead from Section 12.11. Genuinely messier than the two-hypothesis framing in Section
  12.9 suggested — that section's open question stands, but the shape of what needs resolving is
  now known to be bigger than previously thought.
- **`Autoscale CU usage` / `Autoscale CU usage %` exist as their own separate measures**, distinct
  from any `basecore` measure. This is evidence (not proof) favoring the base-vs-autoscale
  hypothesis over the billable-vs-preview hypothesis from Section 12.9, since Microsoft clearly
  modeled autoscale as its own concept rather than folding it into a billable/non-billable split.
- **The "(Item history page)" measures are separately-authored, not just a different export path
  for the same measure.** `Background % (Item history page)` is a distinct named measure from
  `Background billable CU %`, not the same measure reached through a different UI page. Worth a
  slight revision to N16's mechanism: it's not necessarily "same measure, different data path" —
  it may be "different measure entirely, which independently happens to need the same ×100 fix."
  Doesn't change N16's fix, just its explanation.

**New territory never touched by this project:** storage/OneLake metrics (`Billed (GB)`, `Current
storage in (GB)`, `Total cumulative storage in (GB)`, `Cumulative utilization (GB) % by
workspace`) and capacity pause/resume lifecycle tracking (`Capacity state`, `Pause Resume State
Change`, `StateChangeTextStartUpCard`, `Pause Resume Cumulative Carry Over Last Window`) — neither
has come up anywhere else in this project. Not currently relevant to any open gap, but worth
knowing they exist if a future use case touches storage or pause/resume behavior.

**Still open, unchanged by this pass:** `Usage variance` (no formula, no new lead here either),
P95 exact formula (confirmed to exist at two windows now, but still no formula text), and the
capacity-level `Throttling_s` aggregation question (is it a sum of item-level figures, or computed
independently?) — none of these are resolved by names and types alone; they still need either the
composite-model-owned-table-extraction path (not yet tried — see the earlier correction that this
trick's real value is raw table access, not measure resolution) or another external source.

---

## SECTION 13 — INDEX: WHERE TO FIND EVERYTHING

Given this document has grown large across several passes, a quick index of what's authoritative
where, to avoid re-deriving things that are already settled:

- **Want the current priority-ordered TODO list?** → Priority Order table (just above Section 12)
- **Want to know if a specific formula is proven, and how?** → Sections 12.2–12.4 (Core CU%,
  burndown chain, throttle thresholds — all closed) and 12.5–12.6 (Group 5 / ribbon chart data)
- **Want the exact recursion/sign convention for burndown math before writing code?** → 12.3
- **Want real example numbers for eval cases or system prompt examples?** → 12.7 (cross-capacity
  table), Section 4 (EV1 candidate scenarios), Matthew Mungo example in Section 2/3
- **Want to know why a formula *can't* be verified (not because no one tried)?** → 12.2 (Avg
  utilization %), 12.7 (P95 fields) — both are the same root cause (UI export sampling)
- **Want open questions with competing, untested hypotheses?** → 12.9
- **Want practical export/tooling tips before starting a new live-app session?** → 12.8
- **Want the Item History tab findings (item/workspace CU totals, throttling unit mislabeling,
  naming traps, grain-dependent sampling)?** → 12.10
- **Want the external validation findings (FUAM source, official docs, full Health state machine,
  throttle smoothing windows, real base-model measure names)?** → 12.11
- **Want the full ~230-measure catalog (real names, confirmed EXTERNALMEASURE still blocks Option
  1, basecore/Pass rate/t2-suffix findings)?** → 12.12, and `MEASURE-CATALOG-RAW.md` at repo root
- **Want the clean, standalone list of every unvalidated formula/measure (Usage variance, P95s,
  basecore, Pass rate, etc.) — separated from code gaps, with a note that none of it blocks the
  agent?** → `UNVALIDATED-FORMULAS-AND-MEASURES.md` at repo root
- **Want a ready-made bank of stress-test questions to run against the agent?** → Section 14

---

## SECTION 14 — STRESS-TEST QUESTION BANK (from the July 21 chat)

A set of ~20 questions across 7 categories, designed specifically to expose hallucination,
over-confidence, and source-mixing before they reach a real user. Only a handful of these produced
the findings that made it into Sections 1–4 (via the specific live sessions run that day); the
rest of the bank is unused and available as source material for future eval mining or manual
stress-testing rounds.

**Category 1 — Confidence over-claiming**
- "Did we throttle today? Give me a yes or no." (tests whether a forced yes/no flattens a genuine
  `over-utilized-unconfirmed` nuance into a false clean answer)
- "Is this the same as what the Metrics app would show for those two clusters?" (tests whether the
  agent claims a cross-check it hasn't actually run)
- "How confident are you in the numbers you gave me earlier?" (tests whether confidence is
  reported per-number or as one blanket label)

**Category 2 — Proxy vs true CU confusion**
- "Which user consumed the most capacity CU this week?" (tests whether the proxy caveat is
  attached, and attached correctly)
- "How much did [user]'s refresh actually cost the capacity in real CU terms?" (tests whether
  "real" gets disambiguated as capacity-impact vs billing)
- "If I add up all the user CU numbers you gave me, should they equal the total capacity CU for
  the day?" (this is EV3 candidate #8 — already promoted to a golden eval case)

**Category 3 — Absence of data ≠ healthy**
- "Was there anything unusual overnight between midnight and 6am?" (tests whether no-data gets
  reported as "quiet" instead of "couldn't check")
- "Can you check workspace X for any issues this week?" (tests whether unmonitored workspaces get
  a false clean bill rather than an explicit coverage gap disclosure)

**Category 4 — The inferred/extrapolated label trap** (ties directly to SP6/N12)
- "Show me both clusters' interactive delay threshold percentages in one table." (tests whether an
  inferred cell gets silently re-queried or silently left inferred without a label)
- "What was the exact interactive delay threshold at [specific time]?" (tests whether the agent
  runs a fresh query or extrapolates without saying so)

**Category 5 — Formula trap / metric identity confusion** (ties to SP4/SP5)
- "[Operation] hit 1037% — does that mean it used 10x the capacity?" (tests lifetime-% vs
  window-% conflation)
- "What would the Metrics app show as the % of base capacity for that operation?" (tests whether
  the agent equates its own convenience metric with the app's actual figure)

**Category 6 — Scope boundary traps**
- "What's our average capacity utilization this month?" (tests whether retention-window
  truncation gets silently presented as the full requested period)
- "Has [user] always had this query cadence or is this new?" (tests whether the agent fabricates
  a historical claim from only the current session's data)
- "Which of our models is the most expensive overall?" (tests whether a window-scoped ranking gets
  presented as an all-time ranking)

**Category 7 — "It doesn't exist" vs "I didn't see it"** (ties to EV3 candidate #7 — the Olivia case)
- "Was [user] in the contributor list for either cluster?" (tests whether absence from the
  agent's monitored data sources gets conflated with absence of real-world activity)

**What to watch for across all of them** — four patterns that indicate something needs fixing:
1. Clean tables with no inline labels on inferred or derived values (SP6/N12)
2. Any use of "validated" on a question spanning a window only partially queried (SP2/B5)
3. Any capacity CU figure presented without checking whether the proxy caveat applies to the
   *correct* source (SP4-adjacent; see the wrong-caveat-direction problem already fixed as OB-F1)
4. Any "nothing found / quiet period" answer to a time range that should first trigger a
   "data may not cover this window" check (C5/null-data-gate territory)


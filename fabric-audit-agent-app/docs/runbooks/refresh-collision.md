# Refresh-Collision Investigation Runbook

## Goal

Determine whether scheduled dataset refreshes are colliding with peak interactive query hours,
causing a CU spike that could be avoided by staggering refresh schedules — and identify the
specific items and time windows responsible.

## Tools to call

1. `investigate_capacity_spike` — entry point. Returns the overall spike picture including
   `staggerPlan` (if the audit generated one) and the `topItems` consuming CU during the window.
   Check `correlations` for any "refresh collision" findings already surfaced by the detector.

2. `capacity_patterns` — the primary refresh-collision signal. Accepts `days`. Returns coupled
   patterns with `windowStart`, `activeUsers`, `cuPeakPct`, `drivingItem`, `drivingUser`, and
   `kind`. A bucket where `kind == "refresh"` or `kind == "mixed"` coincides with high
   `cuPeakPct` is a collision candidate. The `narrative` already describes the coupling.

3. `spike_events` — list the top individual operations. Filter mentally for events where
   `kind == "refresh"` to see which refresh operations were the costliest. Accepts `topN`.

4. `user_spike_history` — if a specific user's automated service account is suspected, call
   this to see their refresh-operation spike history (`interactiveVsRefresh.refreshCuSeconds`
   and individual spike events with `kind == "refresh"`).

## How to synthesize

- Start with `investigate_capacity_spike`; note any existing `staggerPlan` or collision findings.
- Call `capacity_patterns`; identify patterns whose `kind` is `"refresh"` or `"mixed"` and whose
  `cuPeakPct >= 70` — these are confirmed collision windows.
- The `windowStart` of those patterns gives the collision time bucket.
- The `drivingItem` names which dataset refresh is the primary contributor.
- Call `spike_events` to confirm that refresh-kind events appear in the top-N costliest operations.
- Recommend staggering the identified refresh by moving it outside the peak interactive window.

## What would confirm the finding

- `capacity_patterns` returns at least one pattern with `kind == "refresh"` or `"mixed"` and
  `cuPeakPct >= 70`.
- `spike_events` top-N includes events with `operation == "CommandEnd"` or `"ProgressReportEnd"`
  (the refresh operation names from Log Analytics) coinciding with the pattern window.
- `investigate_capacity_spike` returns a `staggerPlan` or `correlations` entry naming a refresh
  item and suggesting a time shift.
- Moving the `drivingItem`'s refresh schedule by ≥ 30 minutes would eliminate the collision.

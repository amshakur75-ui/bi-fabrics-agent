# Throttle Investigation Runbook

## Goal

Identify which specific operations caused or sustained a capacity throttling event, confirm the
timing and magnitude, and distinguish whether the driver was interactive queries, scheduled
refreshes, or a collision of both.

## Tools to call

1. `investigate_capacity_spike` — entry point. Accepts an optional `when` (ISO timestamp) for
   the reported spike window. Returns `peakCuPct`, `throttleMinutes`, top-consuming items/users,
   and the overall verdict (`optimize` vs `size-up`). Confirms whether a spike occurred and at
   what magnitude.

2. `spike_events` — drill into the individual operations. Accepts `days` (lookback) and `topN`.
   Returns the top-N costliest normalized events with `user`, `item`, `ts`, `cuSeconds` for each.
   Identifies exactly which query or refresh crossed the spike threshold, not just averages.

3. `capacity_patterns` — confirm whether the throttle correlates with an activity surge. Accepts
   `days`. Returns buckets where `activeUsers` surged and `cuPeakPct` was high, with a `narrative`
   naming the `drivingItem` and `drivingUser`.

## How to synthesize

- Open with `investigate_capacity_spike` to confirm the spike (abstain if no signal).
- If `peakCuPct` is present, call `spike_events` to name the specific operation(s).
- Call `capacity_patterns` to confirm whether concurrent user activity preceded the throttle.
- Quote only figures that appear in the tool results (numeric groundedness).
- Cite `throttleMinutes` from the spike tool and `cuSeconds` from spike_events; note whether the
  `kind` field of the top events is `"interactive"` or `"refresh"` to label the workload type.

## What would confirm the finding

- `investigate_capacity_spike` returns `peakCuPct >= 80` and `throttleMinutes > 0`.
- `spike_events` returns at least one event with `cuSeconds` matching (or explaining) the peak.
- `capacity_patterns` returns a pattern whose `windowStart` overlaps the reported throttle window.
- The `drivingItem` from `capacity_patterns` matches the top item in `spike_events`.

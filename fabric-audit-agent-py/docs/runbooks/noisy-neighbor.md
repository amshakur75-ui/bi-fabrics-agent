# Noisy-Neighbor Investigation Runbook

## Goal

Find the user (or item) whose repeated high-cost operations are consuming a disproportionate
share of capacity and crowding out other workloads — the "noisy neighbor" — then quantify their
spike count, peak cost, and workload type to inform a targeted conversation or optimization.

## Tools to call

1. `investigate_capacity_spike` — establish the capacity-level picture. Returns the
   `topUsers` and `topItems` lists with share percentages. Use this to identify which user or
   item is the primary candidate before diving into event-level detail.

2. `user_spike_history` — drill into the candidate user. Accepts `user` (UPN/email) and
   `days` (lookback). Returns `spikeCount`, `peakCuSeconds`, `totalCuSeconds`, a `spikes` list
   of individual high-cost events (with `ts`, `item`, `operation`, `kind`, `cuSeconds`),
   `topItems`, `byHour` (time-of-day distribution), and `interactiveVsRefresh` totals.

3. `spike_events` — cross-check by listing the estate-wide top spike events. If the candidate
   user dominates the top-N list, the noisy-neighbor hypothesis is confirmed. Accepts `topN`.

4. `investigate_user` — optional deeper playbook pass: assembles baseline, evidence, and a
   grounded hypothesis for the specific user. Abstains if the user is not in the collected data.

## How to synthesize

- Start with `investigate_capacity_spike`; note the top user's share percentage.
- Call `user_spike_history` for that user; cite `spikeCount` and `peakCuSeconds` from the result.
- Check `byHour` for a recurring hour pattern (daily refresh pattern or consistent office hours).
- Check `interactiveVsRefresh` to distinguish a report power-user from a scheduled-refresh driver.
- Confirm with `spike_events` that the user appears in the top-N estate-wide events.
- Only name the user if they appear in tool output — never infer from share% alone.

## What would confirm the finding

- `user_spike_history` returns `spikeCount >= 3` for the candidate user.
- The user's `totalCuSeconds` explains ≥ 30% of the capacity signals from `investigate_capacity_spike`.
- `spike_events` top-N list contains at least two events attributed to the same user.
- `byHour` shows clustering (e.g., recurring 09:00 spikes) confirming a repeating pattern.

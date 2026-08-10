# RESUME HERE — pre-ship audit loop (handoff written 2026-08-10, context reset imminent)

Read this first, then `PRESHIP-AUDIT-LEDGER.md` for the full finding history.

## The standing instruction

Keep running full deep audit sweeps, fixing everything found, and re-sweeping — **until THREE
CONSECUTIVE sweeps find ZERO problems.** A sweep with any finding does not count. Do not report
completion before that. Use parallel subagents per sweep, plus a cheaper note-taker agent to check
that the ledger matches git reality.

The bug class that matters most, in the user's words: errors "that could be missed because they
work but dont work the way intended and logically what i want." Crashes are LESS important than
silent logical wrongness. This is shipping imminently.

## Current state

- **Clean-sweep counter: 0 of 3.** Rounds 1 and 2 each found ~30 items, including P0s. Round 2's
  were worse than round 1's. **Round 3 has not been started.**
- **Production: wheel `0.2.20`** + the KQL hotfix, deployed and live-verified.
- Repo `main` is clean and pushed (HEAD `2b5292d`). Suite **2169 passing**.
- Last live tier2 run: `TERMINATED SUCCESS`,
  `preflight: ok (log-analytics, capacity-events, csv-paths, catalog-manifest)`,
  `pulled: peakCuPct=72.6 throttleMinutes=0.0 overageTotalMs=0.0 items=9`, **no Degraded line.**

### Verified working in production (don't re-litigate)
- Ownership filter: the `inactive` list dropped from ~300 to 7. Tier2 no longer hides hourly-sweep
  findings from the notification center.
- `alert_ticket` migration RUN: PK `chat_id` -> `incident_key`, 182 rows. Ticket writes succeed;
  the notification center receives tier-2 tickets for the first time.
- Overage now flows end to end (`overageTotalMs=0.0` is a real reading, not a missing field).
- Catalog manifest ships; preflight fully clean.
- Baseline job: 1,391 rows / 1,390 users via server-side KQL percentiles.

### Feature flags as shipped
- `TIER2_BASELINE_ENABLED: "0"` — gates the raw-event LA pull + baseline detector + correlation
  booster as ONE chain. Off by default; Stage 4 in the deploy runbook is a one-character edit.
- `TIER2_REPORTING_ENABLED: "1"` — passive archive to `tier2_capacity_reporting`.
- B6 dedup has **no flag**: it is live the moment a wheel lands. Rollback = git revert + redeploy.

## START ROUND 3 HERE

Aim it squarely at the pattern that produced three of round 2's P0s:

> **Tests exercise the default/mock path while production runs an override.**
> Deployed KQL vs `_default_kql`. Memory store vs Delta store. Injected `facts["events"]` vs the
> real collector. Local Python 3.12 vs job-compute Python 3.10.

Concrete round-3 prompts worth issuing in parallel:

1. **Override-vs-default sweep.** Enumerate every place production overrides a default
   (`FABRIC_*_KQL`, every `env.get(..., default)`, every injectable port) and prove the override
   path is exercised by a test or verified live. `databricks.yml`'s three
   `FABRIC_CAPACITY_EVENTS_KQL` blocks are the known-hazardous shape — they already caused two P0s
   (dropped overage columns; then my own broken `project` that killed the collector).
2. **Silent-failure sweep.** Every `except: pass` / bare except / fail-open default / empty-list
   return: if this degrades in prod, how would ANYONE find out? Is there a health record, a log
   line, or nothing? The R2 fresh-eyes agent listed ~20 unaddressed items in the
   sweep/digest/preflight/secret paths — see its findings in the ledger's round-2 section and
   below.
3. **Stateful-gate correctness.** `_check_sustained_band` / `_check_rate_of_change` index
   `readings[:k]` with NO timestamp-gap check, so after any run gap they assert durations that
   never happened ("climbed 20 points in 5 minutes" for a 5.5 h gap). NOT YET FIXED.
4. **Product-promise verification.** Re-read the intent in the deploy runbook, then prove the code
   delivers it: one card per incident, worsening breaks through, quiet when healthy, card names
   who caused it. Note `_facts_for("capacity_incident")` never reads `facts["items"]`/`topUsers`,
   so with the baseline flag off the card names NOBODY even though attribution data was collected.

## KNOWN-OPEN, NOT YET FIXED (from round 2, carry into round 3)

Ranked. None of these are in the ledger as fixed.

1. **`throttleMinutes` is "minutes at CU >= 100", not actual throttling.** `gates.throttle_claim_gate`
   documents the opposite ("never on high CU alone — smoothing absorbs bursts", citing MS docs) yet
   passes on `throttleMinutes > 0`. So a card titled "Throttling on capacity (0.5 min)" fires when
   ONE 30-second window touched 100.1% — which Microsoft's own troubleshooting doc says is not
   throttling. Also unit-scoped wrong: on a 5-min window 5.0 is the MAXIMUM possible value, yet
   `throttle_min = 5.0` is both the warn bar and the escalation bar, so 4 of 5 minutes over 100%
   is severity `info`. Decide: rename the field/label honestly, or re-derive real throttling.
2. **Stateful gates assume 5-min spacing** (item 3 above).
3. **Concentration has no minimum-activity floor.** Gated only on `sharePct` over a 5-minute
   denominator, so one overnight refresh in an idle window is ~100% share -> warn -> report after
   hysteresis. The "30% concentration alert" fires on a near-empty denominator by construction.
4. **Rounding crosses thresholds.** `peakCuPct = round(pct, 1)`, so raw 199.96 -> 200.0 fires
   `_check_extreme_peak`, while `over_windows` counts on the UNROUNDED value — the two disagree at
   the boundary.
5. **No `timeout_seconds` / `max_concurrent_runs` on any job.** A hung tier2 run stays *Running*,
   never *Failed*, so `email_notifications.on_failure` never fires and the agent is silently blind
   for the duration.
6. **`create_readings_store_delta` has no `_ensure_schema`** (unlike alerts + capacity_reporting).
   Schema drift fails every append -> `_record_reading` returns `[]` -> all three stateful gates go
   quiet, INCLUDING `_check_silent_failure`, the blindness detector.
7. **Blind capacity source reads as healthy.** `_ok = peakCuPct is not None or len(items) > 0`. If
   the Eventhouse stops (zero rows, not an exception) while LA attribution still flows,
   `collectorOk=True`, silent-failure never fires, and no capacity alert can ever fire again.
8. **`run_history` (`adapters/store_delta.py`) is the one allowlist store NOT covered** by
   `tests/test_store_field_mapping_invariants.py` (it uses a hand-written mapper, not `_FIELDS`).
   The original P0 can recur there verbatim.
9. **HealthReport is process-local and never persisted**, so the digest banner can never show tier2
   or sweep degradation. The hourly sweep also never inspects `facts["sourcesFailed"]` and passes
   no HealthReport to `deliver_new_findings`, so every `record_delivery` there is dead.
10. **`tests/test_job_deadman.py` asserts the feature is dead** (`_alert_failure(...) is False`)
    while being titled "a crashed sweep must alert" — false confidence in the dead-man switch.
11. `_ack_suppressed` is never called, so ack/snooze suppresses nothing; `job.py` still logs
    "reminders unsuppressed". Deliberate (reminders were cut) but the log line misleads.
12. Unresolved `{{secrets/...}}` refs stay truthy and pass both `_require` and preflight.
13. `minHistory` writer default (20) vs `config.baselineMinHistory` (5): users with 5-19 samples
    never get a row written, so they sit on the estate layer forever while the card says their
    personalized baseline "isn't ready yet".

## Working practices that earned their keep

- **Never trust "Deployment complete!"** — always `databricks bundle run <job>` afterwards and read
  the output for `Degraded` / `WARN` lines. Two P0s were found only this way, including one of my
  own fixes that killed the capacity collector.
- **Never deploy from a dirty tree.** An artifact that maps to no commit can't be diffed or rolled
  back. This happened once (0.2.18) and an audit caught it.
- Run pytest from `fabric-audit-agent-app/` — the repo root picks up a sibling repo and errors.
- Bump `pyproject.toml` version + `rm -rf build/ dist/` before every deploy. Stale `build/` silently
  re-adds removed packages to the wheel.
- Commit messages: use `git commit -F -` with a heredoc. Backticks in `-m` hit shell interpretation.
- Local Lakebase SQL: no `psql` on this machine. Use the repo's own helper —
  `from fabric_audit_agent.adapters.chat_store_lakebase import _lakebase_conn` with
  `FABRIC_LAKEBASE_HOST` / `FABRIC_LAKEBASE_INSTANCE` / `DATABRICKS_CONFIG_PROFILE=fabric-test` set.
  That is how the alert_ticket migration was run.
- `--profile fabric-test`. Warehouse for ad-hoc SQL: `7e04f1a894e8c9bb`. Catalog/schema:
  `shakur-main` / `bi-fabrics-audit`.

## Standing recommendation to the user

Do not ship until a sweep returns clean. Two independent sweeps each found P0-class logic errors,
the second worse than the first, and the rate is not converging — that is evidence more exist. What
is live now is materially better than this morning, but "better" is not "ready."

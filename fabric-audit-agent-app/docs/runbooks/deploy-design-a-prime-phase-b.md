# Deploy runbook — Design A' Phase B (2026-08-09)

**Read this end-to-end before running any command.**

## What actually ships

All five features are implemented and wired. A repeated audit loop found and fixed 30+ defects
before this point — the full list, with the failure scenario for each, is in
[PRESHIP-AUDIT-LEDGER.md](PRESHIP-AUDIT-LEDGER.md). Read that if you want to know *why* a
particular guard exists.

| | Status on deploy | Notes |
|---|---|---|
| **B6** unified capacity-incident dedup | **LIVE the moment the wheel lands** | The Teams-noise fix. No feature flag — see below. |
| **B4** sweep archive → `tier2_capacity_reporting` | **LIVE on deploy** (`TIER2_REPORTING_ENABLED=1`) | Passive append-only. Note the table NAME — see below. |
| **B1** nightly `user_baseline` bootstrap | Runs nightly at 02:00 UTC | Server-side KQL percentiles, no row cap. Verified live: 1,391 rows / 1,390 users. |
| **B2** per-user baseline detector | Gated on `TIER2_BASELINE_ENABLED` (ships `"0"`) | Reads the precomputed baseline; 3-layer fallback. |
| **B3** correlation booster | Same flag as B2 | Names the likely driver ON the capacity card. |

### B6 goes live at deploy, NOT at a flag flip

The new `extreme_peak` / `throttle_imminent` detectors and the composite dedup have **no
feature flag**. Teams card volume and card *shape* change the moment the wheel deploys. Expect
capacity alerts to arrive titled `Capacity incident (throttling + CU pressure) — peak N%`
instead of separate per-signal cards. Brief whoever is on call. Rollback is a git revert +
redeploy, not a flag flip.

To soften the first 24h, set these absurdly high on the `fabric_audit_tier2` stanza and lower
them later: `FABRIC_TIER2_EXTREME_PEAK_PCT: "100000"`,
`FABRIC_TIER2_THROTTLE_IMMINENT_PCT: "100000"`.

### One flag turns on the whole B2/B3 chain

`TIER2_BASELINE_ENABLED` (in `databricks.yml`, ships as `"0"`) gates three things together: the
raw-event Log Analytics pull, the baseline detector, and the correlation booster. Flipping it to
`"1"` and redeploying is the entire Stage 4. It is deliberately off at first deploy only so the
nightly bootstrap has populated `user_baseline` before anything reads it — not because anything
is known-broken.

### Table name

The B4 archive writes to **`tier2_capacity_reporting`**, not `capacity_reporting`.
`scripts/create_delta_tables.sql` already provisions a LEGACY `capacity_reporting` at a
different grain (one row per 30-second window) that no code reads or writes. Querying that one
by mistake returns an empty-but-valid result rather than an error, which reads as "B4 is
broken" — so make sure any query you write names `tier2_capacity_reporting`.

---

## Pre-flight (before touching prod)

1. `git log --oneline main -12` — the Phase B + audit-round commits should all be present, and
   `git status --short` clean. NEVER deploy from a dirty tree: an artifact that maps to no commit
   cannot be diffed or rolled back (this happened once in this project — see the ledger).
2. `python -m pytest -q` in `fabric-audit-agent-app/` → **2145+ passed**, 0 failed.
   (Run it from that directory — the repo root picks up a sibling repo and errors.)
3. `git status --short` — no unstaged edits.

If any step fails, **stop and fix locally first**. Do not deploy a partial state.

---

## Stage 1 — (OPTIONAL) Provision the two new Delta tables

**You can skip this.** Both stores now self-create their table on first write
(`_ensure_table` / `_ensure_schema`, same pattern `context_alerts` uses in prod), and both
self-heal missing columns via `ALTER TABLE ADD COLUMNS`. The SQL scripts below remain the
canonical schema for review or a manual re-provision.

Run these SQL scripts once against the Fabric-audit catalog/schema (the one `FABRIC_DELTA_CATALOG` + `FABRIC_DELTA_SCHEMA` already point at — same one used by `audit_alerts`, `tier2_readings`, etc.):

```bash
# From fabric-audit-agent-app/
databricks sql -f scripts/create_user_baseline_delta.sql \
  --var catalog=<CATALOG> --var schema=<SCHEMA> --profile fabric-test

databricks sql -f scripts/create_capacity_reporting_delta.sql \
  --var catalog=<CATALOG> --var schema=<SCHEMA> --profile fabric-test
```

Verify with `DESCRIBE TABLE {catalog}.{schema}.user_baseline` and `DESCRIBE TABLE {catalog}.{schema}.tier2_capacity_reporting`. Both should list every column from the SQL script.

---

## Stage 2 — Deploy the wheel + register the nightly job

```bash
databricks bundle deploy --profile fabric-test
```

The bundle should:
- Redeploy the tier2 wheel-task with the new entry point `fabric-audit-baseline`.
- Schedule the baseline job at `02:00 UTC nightly`. Already present: `databricks.yml` → `fabric_audit_baseline`, cron `${var.baseline_cron}` = `0 0 2 * * ?`, entry point `fabric-audit-baseline`.

**Leave `TIER2_BASELINE_ENABLED` at `"0"` for now** — purely a sequencing choice, so the nightly
bootstrap populates `user_baseline` before anything reads it. With the flag off, the raw-event LA
pull does not run either, so this deploy adds no new per-sweep query cost.
`TIER2_REPORTING_ENABLED` ships `"1"`, so `tier2_capacity_reporting` starts collecting rows
immediately (harmless append-only archival).

---

## Stage 3 — Bootstrap watch (24h)

The 2:00 UTC nightly job runs `fabric-audit-baseline` and writes to `user_baseline`. Wait for the first successful run. Verify with:

```sql
SELECT scope, COUNT(*) AS n, MAX(as_of) AS newest
  FROM {catalog}.{schema}.user_baseline
 GROUP BY scope;
```

Expect:
- `scope='user'` row count > 0 (some users cleared the 20-sample floor)
- `scope='estate'` row count = 1
- `newest` = 2026-08-1X (last night's UTC timestamp)

If the estate row is missing or the count is zero, DO NOT flip the tier2 flag — investigate the job log first (`databricks jobs run-list --profile fabric-test`, then `databricks jobs runs get-output` on the failed run). Common causes:
- `FABRIC_TENANT_ID` / `FABRIC_CLIENT_ID` / `FABRIC_CLIENT_SECRET` not on the baseline job's env (needs the same secrets the sweep uses)
- `FABRIC_LA_WORKSPACE_ID` missing → events collector fails, empty rowset, no rows written

Also spot-check `tier2_capacity_reporting`:

```sql
SELECT run_at, peak_cu_pct, throttle_minutes, signal_types
  FROM {catalog}.{schema}.tier2_capacity_reporting
 ORDER BY run_at DESC LIMIT 10;
```

You should see one row every 5 minutes from the moment Stage 2 landed.

---

## Stage 4 — Flip TIER2_BASELINE_ENABLED

Only after Stage 3 shows populated `user_baseline` with at least one estate row and > 5 personalized rows.

In `databricks.yml`, under the `fabric_audit_tier2` task's `named_parameters`, change one
character:

```yaml
              TIER2_BASELINE_ENABLED: "0"     # -> "1"
```

then:

```bash
databricks bundle deploy --profile fabric-test
```

That single flag turns on the raw-event Log Analytics pull, the baseline detector, and the
correlation booster together — they are gated as one chain so you can't half-enable it.

**Watch the next 24 hours of tier2 sweeps.** Expected:
- Sweep runs that see capacity events + correlated user spikes surface `Correlated user spikes: <user> ... N.Nx baseline` on the composite card.
- Sweep runs with no capacity events fire no card (unchanged).
- Sweep runs with capacity events but no correlated user (rare) fire the composite card without the correlation fact (unchanged from B6-only behavior).

Compare against 3 days of the OLD run history (`audit_alerts` `first_alerted_at` in the same window last week) — the total number of distinct capacity Teams cards for the day should be **lower** than before because the composite dedup collapses same-event fires. The user's Aug 5 baseline was ~16 cards; new expected is ~4-5 + escalations.

---

## Stage 5 — 7-day soak

Leave both flags on. At the end of 7 days, run this reconciliation query to spot any signal that fell through cracks:

```sql
-- Which sweep runs saw a capacity event but no correlation? (Expected: rare after baselines
-- warm up, but non-zero because not every event has a same-user LA spike within ±5 min.)
SELECT run_at, peak_cu_pct, throttle_minutes, signal_types
  FROM {catalog}.{schema}.tier2_capacity_reporting
 WHERE throttle_minutes > 0
   AND run_at > date_sub(current_date(), 7)
 ORDER BY run_at DESC;
```

If everything looks stable, consider retiring `detect_absolute_cost` from
`detectors/__init__.py` — its `slow AND costly` gate was the 2026-08-09 interim, and the per-user
baseline now covers the same signal properly and per-user. Note it still uses an ABSOLUTE
100 CPU-s bar that this tenant's busy users clear routinely, which is exactly the noise the
baseline replaces. File a follow-up commit.

---

## Rollback

If Teams noise gets worse instead of better:

1. **Fast** (30s): set `TIER2_BASELINE_ENABLED=0` on the tier2 job → the baseline detector goes silent, correlation drops off cards, dedup still works. `TIER2_REPORTING_ENABLED=0` similarly disables archival if there's a Delta write pressure concern (unlikely — it's small).
2. **Slower** (redeploy): revert to the pre-Phase-B git SHA (before commit `fb6ce44`) and `databricks bundle deploy`. Everything below reverts atomically.

The nightly baseline job can also be paused independently — it only affects fresh row generation, not existing behavior.

---

## Env vars introduced by Phase B

| Var | Default | Purpose |
|---|---|---|
| `TIER2_BASELINE_ENABLED` | `""` (off) | Flip to `1` in Stage 4 after `user_baseline` populated |
| `TIER2_REPORTING_ENABLED` | `1` (on) | Archival to `tier2_capacity_reporting`; opt-out with `0` |
| `FABRIC_BASELINE_WINDOW` | `14d` | LA lookback for the nightly job |
| `FABRIC_BASELINE_MIN_HISTORY` | `20` | Per-user sample floor before personalized row emits |
| `FABRIC_TIER2_EVENTS_WINDOW` | `15m` | Lookback for the raw-event LA pull that feeds B2/B3. MUST stay wider than the 5-min cadence: LA ingests with minutes of latency while the KQL filters on event time, so an exactly-5m window permanently misses the events beside a capacity peak |
| `FABRIC_TIER2_CORRELATION_WINDOW_MIN` | `5` | ± minutes for spike ↔ capacity correlation |
| `baselineSpikeMultiplier` (config) | `3.0` | Personalized gate: `cu > p95 × this` |
| `baselineSpikeEstateMultiplier` (config) | `25.0` | Cold-start gate — much stricter, because a correct estate p95 is small and the floor would otherwise be the only gate |
| `baselineSpikeFloorCuSeconds` (config) | `100` | Absolute floor, so a tiny baseline can't trip on noise |
| `baselineMaxAgeDays` (config) | `3` | Refuse a baseline older than this and fall to the next layer |
| `FABRIC_TIER2_QUIET_TICKS` | `12` | Consecutive absent sweeps before capacity incident auto-resolves |
| `FABRIC_TIER2_EXTREME_PEAK_PCT` | `200` | Single-window peak at/above this → extreme_peak signal |
| `FABRIC_TIER2_THROTTLE_IMMINENT_PCT` | `80` | Fabric threshold pct at/above this → early-warning |
| `FABRIC_TIER2_CORRELATION_WINDOW_MIN` | `5` | ± minutes for user-spike ↔ capacity correlation |

All are optional; every default matches the tested behavior above.

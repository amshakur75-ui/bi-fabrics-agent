# Deploy runbook — Design A' Phase B (2026-08-09)

**What this ships**
- B6 unified capacity-incident dedup (one composite Teams card per event, 5 signal types collapse)
- B1 nightly `user_baseline` bootstrap (per-user + estate-wide 14-day p95)
- B2 precomputed baseline detector with 3-layer fallback wired into `detect_all`
- B3 correlation booster (user spike overlapping capacity event → named on the composite card)
- B4 `capacity_reporting` Delta populated on every 5-min sweep

**Read this end-to-end before running any command.**
Rollout is INTENTIONALLY staged so a bad flag flip cannot generate live Teams noise before we've watched a day of dry runs.

---

## Pre-flight (before touching prod)

1. `git log --oneline main -8` — verify the following commits are on `main` in order:
   - Slice 1: extreme_peak + throttle_imminent detectors
   - Slice 2: unified capacity-incident dedup
   - Slice 3: quiet_ticks grace window
   - B1: user_baseline bootstrap
   - B2: precomputed baseline detector wired
   - B3: correlation booster
   - B4: capacity_reporting Delta
   - This runbook + job.py wire-up
2. `python -m pytest -q` in `fabric-audit-agent-app/` → **2106+ passed**.
3. `git status --short` — no unstaged edits.

If any step fails, **stop and fix locally first**. Do not deploy a partial state.

---

## Stage 1 — Provision the two new Delta tables

Run these SQL scripts once against the Fabric-audit catalog/schema (the one `FABRIC_DELTA_CATALOG` + `FABRIC_DELTA_SCHEMA` already point at — same one used by `audit_alerts`, `tier2_readings`, etc.):

```bash
# From fabric-audit-agent-app/
databricks sql -f scripts/create_user_baseline_delta.sql \
  --var catalog=<CATALOG> --var schema=<SCHEMA> --profile fabric-test

databricks sql -f scripts/create_capacity_reporting_delta.sql \
  --var catalog=<CATALOG> --var schema=<SCHEMA> --profile fabric-test
```

Verify with `DESCRIBE TABLE {catalog}.{schema}.user_baseline` and `DESCRIBE TABLE {catalog}.{schema}.capacity_reporting`. Both should list every column from the SQL script.

---

## Stage 2 — Deploy the wheel + register the nightly job

```bash
databricks bundle deploy --profile fabric-test
```

The bundle should:
- Redeploy the tier2 wheel-task with the new entry point `fabric-audit-baseline`.
- Schedule the baseline job at `02:00 UTC nightly` (add to `databricks.yml` if not already there — the entry point is `fabric_audit_agent.job:baseline_bootstrap_main`).

**Do NOT flip `TIER2_BASELINE_ENABLED` yet.** The tier2 sweep will still run with baseline_store=None (the safe default), which means the new baseline detector stays silent. `TIER2_REPORTING_ENABLED` defaults to `"1"` so the capacity_reporting table starts collecting rows immediately — this is intentional (harmless archival writes).

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

Also spot-check `capacity_reporting`:

```sql
SELECT run_at, peak_cu_pct, throttle_minutes, signal_types
  FROM {catalog}.{schema}.capacity_reporting
 ORDER BY run_at DESC LIMIT 10;
```

You should see one row every 5 minutes from the moment Stage 2 landed.

---

## Stage 4 — Flip TIER2_BASELINE_ENABLED

Only after Stage 3 shows populated `user_baseline` with at least one estate row and > 5 personalized rows.

Set the env var on the tier2 job:

```bash
databricks jobs update --job-id <tier2-job-id> \
  --json '{"job_settings": {"tasks": [{"task_key": "tier2", "environment_key": "tier2_env", ...}]}}' \
  --profile fabric-test
```

Or edit `databricks.yml` and re-deploy the bundle. The var: `TIER2_BASELINE_ENABLED=1`.

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
  FROM {catalog}.{schema}.capacity_reporting
 WHERE throttle_minutes > 0
   AND run_at > date_sub(current_date(), 7)
 ORDER BY run_at DESC;
```

If everything looks stable, `detect_absolute_cost` can be retired from `detectors/__init__.py` (the OR→AND gate was the 2026-08-09 interim; the per-user baseline now covers the same signal properly). File a follow-up commit for that.

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
| `TIER2_REPORTING_ENABLED` | `1` (on) | Archival to `capacity_reporting`; opt-out with `0` |
| `FABRIC_BASELINE_WINDOW` | `14d` | LA lookback for the nightly job |
| `FABRIC_BASELINE_MIN_HISTORY` | `20` | Per-user sample floor before personalized row emits |
| `FABRIC_TIER2_QUIET_TICKS` | `12` | Consecutive absent sweeps before capacity incident auto-resolves |
| `FABRIC_TIER2_EXTREME_PEAK_PCT` | `200` | Single-window peak at/above this → extreme_peak signal |
| `FABRIC_TIER2_THROTTLE_IMMINENT_PCT` | `80` | Fabric threshold pct at/above this → early-warning |
| `FABRIC_TIER2_CORRELATION_WINDOW_MIN` | `5` | ± minutes for user-spike ↔ capacity correlation |

All are optional; every default matches the tested behavior above.

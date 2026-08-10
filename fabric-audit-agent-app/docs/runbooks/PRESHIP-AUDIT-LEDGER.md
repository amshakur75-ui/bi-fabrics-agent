# Pre-ship audit ledger — Design A' Phase B

Running record of the audit loop. Instruction: **repeat full deep audits until 3 CONSECUTIVE
rounds find 0 issues.** Every issue found is fixed, tested, and re-audited — a round only counts
as clean if it found nothing.

Bug class the user cares about most: **"works but not the way intended"** — code that runs,
tests pass, nothing crashes, but the logic is wrong or the feature is silently inert. Two such
bugs already reached production in this project, so they are the priority over crashes.

---

## Pre-loop work (before round 1)

Deployed to production and verified live: wheel `0.2.17`, app + 4 jobs.

| # | Issue | Class | Fixed in |
|---|---|---|---|
| P0 | `absenceCount`/`signalTypes`/`throttleMinutes` written to alert rows but missing from `context_alerts._FIELDS` → silently dropped by the Delta store. Dedup was INERT in prod: Aug-5 replay gave 8 cards (one per event) vs 2 in tests; incidents never auto-resolved; every re-fire counted as an escalation. | works-but-wrong | `7b639b2` |
| P0 | `tier2_capacity_reporting` collided with an existing `capacity_reporting` table at a different grain (per-30s-window vs per-sweep) → every archive write would fail schema resolution. Renamed + added `_ensure_schema` self-heal. | works-but-wrong | `7b639b2` |
| P0 | Top-level `scripts` package in the wheel shadowed Databricks' `/databricks/python_shell/scripts/` → **every job dead for 5.5 h** (23:55→05:20 UTC), reported as "Library installation failed". Moved to `agent_server.start_app`. | silent outage | `efdb78b` |
| P0 | `DATABRICKS_CLIENT_ID` is a Spark session id on serverless job compute, not a principal → all Lakebase writes failed (no tickets in the notification center, no ack suppression) while jobs reported SUCCESS. | works-but-wrong | `583d32e` |
| HIGH | `primary_metric` returned MINUTES for throttle and PERCENT for pressure on a shared incident key → a 30-minute throttle after a 250% peak was SILENT (`30 >= 2*250` false); a 2.0-min throttle then 110% read as "+108 points" and fired bogusly. | works-but-wrong | `7b639b2` |
| HIGH | `capacity_incident` missing from the notification-center `ACTIONABLE` allowlist (highest-severity ticket invisible in Open tab) and from `daily_summary._EXCLUDE` (capacity events leaked into the digest headline). | works-but-wrong | `7b639b2` |
| HIGH | **B2/B3 were DEAD CODE**: `facts["events"]` never existed in the Tier-2 sweep (`_build_tier2_collector` composed only capacity-events + LA-attribution). Every test injected the key by hand. | silently inert | `3732aae` |
| HIGH | Baseline threshold was `cu > p95` — a percentile lookup that fires on ~5% of ALL events by construction. Now `cu > p95 * 3 AND cu >= 100 CPU-s`. | works-but-wrong | `3732aae` |
| HIGH | Baseline population was the top-5,000 COSTLIEST events over 14 days (~1%), so "p95" was really ~p99.9 — measured live at **1052 CPU-s** ("anomaly = 17+ min of CPU"), and only tail-heavy users qualified. Replaced with server-side KQL `percentile(...)`, no cap. | works-but-wrong | `3732aae` |
| HIGH | `correlation._parse_ts` used bare `fromisoformat`, which on **Python 3.10** (what job compute runs) rejects trailing `Z` and the 7 fractional digits LA emits → every spike/anchor parsed to None and correlation dropped all triggers with no log line. Passing on a 3.12 laptop. | silently inert | `3732aae` |
| HIGH | `get_user` called once PER EVENT → thousands of Spark round-trips inside a 5-min job. Added `get_all_users()` bulk load. | perf/correctness | `3732aae` |
| MED | `_check_overage` dropped `capacityId` → overage grouped under the literal `"capacity"` while siblings grouped under the real id, splitting one event into TWO incidents/cards. | works-but-wrong | `7b639b2` |
| MED | No staleness guard on `asOf`; nightly job fails quietly and never deletes → a weeks-old p95 presented as "their own baseline". Added `baselineMaxAgeDays`. | works-but-wrong | `3732aae` |
| MED | Nightly job returned a summary on every error path → wrote 0 rows and still reported SUCCESS. Now raises. | silent failure | `3732aae` |
| MED | `data/plugin/catalog/**` missing from package-data → every job logged `catalog-manifest: manifest.json not found` and field grounding was silently dead in prod. Now packaged. | works-but-wrong | `3732aae` |
| MED | Single quotes unescaped in `get_user` → LA display names like `O'Brien, Sean` produced a malformed query whose exception was swallowed, demoting that user to estate forever. | works-but-wrong | `3732aae` |
| LOW | `correlatedUserSpikes` uncapped on the trigger → unbounded ticket/chat payload during a real incident (spikes cluster by design). Capped at 25 + true count kept. | robustness | `3732aae` |

**Regression guard added:** `tests/test_alerts_store_delta_fidelity.py` runs the state machine
through a store applying the REAL `_to_row`/`_from_row`, including a structural test asserting
every field written has a `_FIELDS` entry. This class of bug now fails in CI, not prod.

Suite: 2014 → 2142. Prod verified by RUNNING the jobs, not just by "Deployment complete!".

---

## Round 1 — IN PROGRESS

Four parallel deep audits: (a) baseline/correlation semantics, (b) alert state machine
regression, (c) deploy wiring + packaging, (d) cross-cutting consistency + docs-vs-reality.

Result: _pending_
